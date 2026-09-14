#!/usr/bin/env python3
"""Read-only diagnostics for the Hacker Gadgets AC1200 / MT7921 board."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence


CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


class AC1200Diagnostics:
    """Collect non-destructive USB, driver, RFKILL, and capability checks."""

    def __init__(self, command_runner: CommandRunner | None = None) -> None:
        self._run = command_runner or self._run_command

    @staticmethod
    def _run_command(args: Sequence[str], timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                list(args), capture_output=True, text=True, timeout=timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(args, 1, "", str(exc))

    def _check(self, category: str, name: str, status: str, details: dict[str, Any]) -> dict[str, Any]:
        return {"category": category, "test": name, "status": status, "details": details}

    def usb_hardware(self) -> list[dict[str, Any]]:
        checks = []
        lsusb = self._run(["lsusb"], 5).stdout
        devices = [line.strip() for line in lsusb.splitlines() if "0e8d:7961" in line.lower()]
        checks.append(self._check("USB", "MT7921 device detected", "PASS" if devices else "FAIL", {
            "devices": devices,
            "vendor_product": "0e8d:7961",
        }))

        tree = self._run(["lsusb", "-t"], 5).stdout
        relevant_lines = [
            line.strip() for line in tree.splitlines()
            if "mt7921u" in line.lower() or "btusb" in line.lower()
        ]
        speed = "unknown"
        if any("5000m" in line.lower() for line in relevant_lines):
            speed = "5000M"
        elif any("480m" in line.lower() for line in relevant_lines):
            speed = "480M"
        elif any("12m" in line.lower() for line in relevant_lines):
            speed = "12M"
        speed_status = "PASS" if speed == "5000M" else "WARN" if speed != "unknown" else "WARN"
        checks.append(self._check("USB", "USB link speed", speed_status, {
            "speed": speed,
            "interfaces": relevant_lines,
            "expected": "5000M",
        }))
        checks.append(self._check("USB", "USB Wi-Fi/Bluetooth interfaces", "PASS" if len(relevant_lines) >= 2 else "WARN", {
            "count": len(relevant_lines),
        }))
        return checks

    def driver_health(self) -> list[dict[str, Any]]:
        checks = []
        loaded = self._run(["lsmod"], 5).stdout
        required = ["mt7921u", "mt7921_common", "mt76_connac_lib", "mt76", "btusb", "mac80211"]
        present = [module for module in required if re.search(rf"\b{re.escape(module)}\b", loaded)]
        missing = [module for module in required if module not in present]
        checks.append(self._check("Driver", "Kernel modules", "PASS" if not missing else "WARN", {
            "present": present,
            "missing": missing,
        }))

        rfkill = self._run(["rfkill", "list"], 5).stdout
        interfaces = self.interfaces()
        target_wifi = [item for item in interfaces["wifi"] if item.get("driver") == "mt7921u"]
        target_names = {item["phy"] for item in target_wifi if item.get("phy")}
        for item in target_wifi:
            phy_match = re.fullmatch(r"phy(\d+)", item.get("phy", ""))
            if phy_match:
                target_names.add(f"hci{phy_match.group(1)}")
        blocked_names = []
        current_name = None
        for line in rfkill.splitlines():
            header = re.match(r"\d+:\s+([^:]+):", line.strip())
            if header:
                current_name = header.group(1)
            elif current_name and current_name in target_names and re.search(
                r"Soft blocked: yes|Hard blocked: yes", line, re.I
            ):
                blocked_names.append(current_name)
        blocked = bool(blocked_names)
        checks.append(self._check("Driver", "RFKILL", "FAIL" if blocked else "PASS", {
            "blocked": blocked,
            "targets": sorted(target_names),
            "blocked_targets": sorted(set(blocked_names)),
        }))

        dmesg = self._run(["dmesg"], 5).stdout
        firmware_lines = [line.strip() for line in dmesg.splitlines() if re.search(r"mt7921|firmware", line, re.I)][-10:]
        firmware_error = any(re.search(r"failed|error|timeout", line, re.I) for line in firmware_lines)
        checks.append(self._check("Driver", "Firmware initialization", "FAIL" if firmware_error else "PASS" if firmware_lines else "WARN", {
            "evidence": firmware_lines,
        }))
        return checks

    def interfaces(self) -> dict[str, Any]:
        result = self._run(["iw", "dev"], 5).stdout
        wifi = []
        current_phy = None
        for line in result.splitlines():
            phy_match = re.search(r"phy#(\d+)", line)
            if phy_match:
                current_phy = f"phy{phy_match.group(1)}"
            interface_match = re.search(r"\bInterface\s+(\S+)", line)
            if interface_match:
                interface = interface_match.group(1)
                driver_output = self._run(["ethtool", "-i", interface], 5).stdout
                driver_match = re.search(r"^driver:\s*(\S+)", driver_output, re.M)
                wifi.append({
                    "interface": interface,
                    "phy": current_phy,
                    "driver": driver_match.group(1) if driver_match else None,
                })
        bluetooth = []
        hciconfig = self._run(["hciconfig"], 5).stdout
        bluetooth.extend(re.findall(r"^(hci\d+):", hciconfig, re.M))
        return {"wifi": wifi, "bluetooth": bluetooth}

    def capabilities(self, wifi_interface: str | None = None) -> list[dict[str, Any]]:
        checks = []
        discovered = self.interfaces()
        interface = wifi_interface or (discovered["wifi"][0]["interface"] if discovered["wifi"] else None)
        if not interface:
            return [self._check("Capabilities", "Wi-Fi interface", "WARN", {"reason": "no wireless interface found"})]
        phy = next((item["phy"] for item in discovered["wifi"] if item["interface"] == interface), None)
        if not phy:
            return [self._check("Capabilities", "Wi-Fi PHY", "WARN", {"interface": interface})]
        info = self._run(["iw", "phy", phy, "info"], 8).stdout
        bands = []
        if re.search(r"2412 MHz|2\.4 GHz", info):
            bands.append("2.4 GHz")
        if re.search(r"5180 MHz|5745 MHz|5 GHz", info):
            bands.append("5 GHz")
        if re.search(r"5955 MHz|6 GHz", info):
            bands.append("6 GHz")
        modes = [mode for mode in ("managed", "AP", "monitor", "mesh point", "P2P-client") if mode in info]
        checks.append(self._check("Capabilities", "Supported bands", "PASS" if bands else "WARN", {
            "interface": interface, "phy": phy, "bands": bands,
        }))
        checks.append(self._check("Capabilities", "Operating modes", "PASS" if modes else "WARN", {
            "modes": modes,
        }))
        return checks

    def run_basic(self, wifi_interface: str | None = None) -> dict[str, Any]:
        checks = self.usb_hardware() + self.driver_health() + self.capabilities(wifi_interface)
        summary = {status.lower(): sum(1 for check in checks if check["status"] == status) for status in ("PASS", "WARN", "FAIL")}
        summary["total"] = len(checks)
        return {
            "timestamp": datetime.now().isoformat(),
            "tool": "k7bat-ac1200-diagnostics",
            "interface": wifi_interface,
            "checks": checks,
            "summary": summary,
        }

    @staticmethod
    def write_json(report: dict[str, Any], path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return destination

    @staticmethod
    def write_markdown(report: dict[str, Any], path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        summary = report["summary"]
        lines = [
            "# AC1200 Diagnostic Report",
            "",
            f"Timestamp: `{report['timestamp']}`",
            f"Summary: PASS {summary['pass']} | WARN {summary['warn']} | FAIL {summary['fail']}",
            "",
            "| Category | Test | Status | Details |",
            "| --- | --- | --- | --- |",
        ]
        for check in report["checks"]:
            details = json.dumps(check["details"], sort_keys=True).replace("|", "\\|")
            lines.append(f"| {check['category']} | {check['test']} | {check['status']} | `{details}` |")
        destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Hacker Gadgets AC1200 diagnostics")
    parser.add_argument("--wifi-iface", help="Wi-Fi interface to inspect")
    parser.add_argument("--json", dest="json_path", help="Write a JSON report")
    parser.add_argument("--markdown", dest="markdown_path", help="Write a Markdown report")
    args = parser.parse_args()
    if not shutil.which("iw") and not shutil.which("lsusb"):
        print("No Linux wireless/USB utilities found; run this on the uConsole.")
        return 2
    report = AC1200Diagnostics().run_basic(args.wifi_iface)
    print(json.dumps(report["summary"], indent=2))
    if args.json_path:
        AC1200Diagnostics.write_json(report, args.json_path)
    if args.markdown_path:
        AC1200Diagnostics.write_markdown(report, args.markdown_path)
    return 1 if report["summary"]["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())