#!/usr/bin/env python3
"""Shared AIOv2, RTL-SDR, and service lifecycle coordination."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

try:
    import fcntl
except ImportError:  # Windows development environments do not provide fcntl.
    fcntl = None


class RadioCoordinator:
    """Coordinate the AIOv2 SDR rail and services that use the RTL device."""

    def __init__(
        self,
        command_runner=None,
        lock_path: str | os.PathLike[str] = "/run/lock/k7bat-sdr.lock",
        enum_timeout: float = 10.0,
        poll_interval: float = 0.5,
    ) -> None:
        self._run = command_runner or self._run_command
        self.lock_path = Path(lock_path)
        self.enum_timeout = enum_timeout
        self.poll_interval = poll_interval
        self._thread_lock = threading.Lock()

    @staticmethod
    def _run_command(args: Sequence[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def command_available(self, command: str) -> bool:
        return shutil.which(command) is not None

    def service_state(self, service: str) -> str:
        result = self._run(["systemctl", "is-active", service], 5.0)
        state = (result.stdout or "").strip()
        return state or "inactive"

    def service_active(self, service: str) -> bool:
        return self.service_state(service) == "active"

    def _service_action(self, action: str, *services: str) -> bool:
        if not services:
            return True
        result = self._run(["systemctl", action, *services], 15.0)
        return result.returncode == 0

    def rail_states(self) -> dict[str, bool | None]:
        states: dict[str, bool | None] = {"GPS": None, "SDR": None, "LORA": None, "USB": None}
        if not self.command_available("aiov2_ctl"):
            return states

        result = self._run(["aiov2_ctl", "--status"], 5.0)
        output = result.stdout or ""
        if not output.strip():
            result = self._run(["aiov2_ctl", "--power"], 5.0)
            output = result.stdout or ""

        for device in states:
            for line in output.splitlines():
                if device.lower() not in line.lower():
                    continue
                lowered = line.lower()
                if re.search(r"\b(on|enabled|high)\b", lowered):
                    states[device] = True
                elif re.search(r"\b(off|disabled|low)\b", lowered):
                    states[device] = False

        if states["SDR"] is None and self.service_active("readsb"):
            states["SDR"] = True
        return states

    def set_rail(self, device: str, enabled: bool) -> bool:
        if device not in {"GPS", "SDR", "LORA", "USB"}:
            raise ValueError(f"Unsupported AIOv2 rail: {device}")
        if not self.command_available("aiov2_ctl"):
            return False
        result = self._run(["aiov2_ctl", device, "on" if enabled else "off"], 10.0)
        return result.returncode == 0

    def rtl_present(self) -> bool:
        if not self.command_available("lsusb"):
            return False
        result = self._run(["lsusb"], 5.0)
        return bool(re.search(r"0bda:(?:28|283|284)|RTL283[238]", result.stdout or "", re.I))

    def ensure_sdr_ready(self) -> tuple[bool, str]:
        states = self.rail_states()
        if states["SDR"] is not True and not self.set_rail("SDR", True):
            return False, "unable to enable SDR rail"

        deadline = time.monotonic() + self.enum_timeout
        while time.monotonic() < deadline:
            if self.rtl_present():
                return True, "SDR rail enabled and RTL device detected"
            time.sleep(self.poll_interval)
        return False, "SDR rail enabled but RTL device was not detected"

    def radio_status(self) -> dict[str, object]:
        rails = self.rail_states()
        readsb = self.service_state("readsb")
        tar1090 = self.service_state("tar1090")
        sdrpp = self.service_state("sdrpp")
        return {
            "enabled": rails["SDR"] is True,
            "sdr_rail": "on" if rails["SDR"] is True else "off" if rails["SDR"] is False else "unknown",
            "rtl_device": self.rtl_present(),
            "readsb": readsb,
            "tar1090": tar1090,
            "sdrpp": sdrpp,
            "status": "active" if readsb == "active" or sdrpp == "active" else "idle",
            "frequency": None,
            "mode": "",
        }

    def set_enabled(self, enabled: bool) -> tuple[bool, str]:
        if enabled:
            ready, message = self.ensure_sdr_ready()
            if not ready:
                return False, message
            if not self._service_action("start", "sdrpp"):
                return False, "failed to start sdrpp"
            return True, "SDR enabled"

        if self.service_active("sdrpp"):
            self._service_action("stop", "sdrpp")
        if self.service_active("readsb") or self.service_active("tar1090"):
            return False, "stop readsb/tar1090 before disabling the SDR rail"
        if not self.set_rail("SDR", False):
            return False, "failed to disable SDR rail"
        return True, "SDR disabled"

    @contextmanager
    def exclusive_session(self, restore_readsb: bool = True) -> Iterator[None]:
        """Reserve the RTL-SDR and restore readsb when the caller exits."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("w") as lock_file:
            if fcntl is None:
                acquired = self._thread_lock.acquire(blocking=False)
                if not acquired:
                    raise RuntimeError("another SDR session is already active")
            else:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError("another SDR session is already active") from exc

            was_running = self.service_active("readsb") or self.service_active("tar1090")
            if was_running and not self._service_action("stop", "readsb", "tar1090"):
                raise RuntimeError("failed to stop readsb/tar1090")
            ready, message = self.ensure_sdr_ready()
            if not ready:
                if was_running and restore_readsb:
                    self._service_action("start", "readsb", "tar1090")
                raise RuntimeError(message)
            try:
                yield
            finally:
                if was_running and restore_readsb:
                    self._service_action("start", "readsb", "tar1090")
                if fcntl is None:
                    self._thread_lock.release()
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
