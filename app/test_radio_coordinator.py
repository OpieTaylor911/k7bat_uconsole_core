import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from radio_coordinator import RadioCoordinator
import status_api


class FakeRunner:
    def __init__(self, outputs=None):
        self.outputs = outputs or {}
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append(list(args))
        command = tuple(args)
        stdout = self.outputs.get(command, "")
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")


class RadioCoordinatorTests(unittest.TestCase):
    def test_parses_aio_rail_states(self):
        runner = FakeRunner({
            ("aiov2_ctl", "--status"): "GPS GPIO27: ON\nSDR GPIO22: OFF\nLORA GPIO23: ON\nUSB GPIO24: OFF\n",
        })
        coordinator = RadioCoordinator(runner, lock_path="/tmp/test-k7bat-sdr.lock")

        with patch("radio_coordinator.shutil.which", return_value="/usr/bin/aiov2_ctl"):
            self.assertEqual(
                coordinator.rail_states(),
                {"GPS": True, "SDR": False, "LORA": True, "USB": False},
            )

    def test_exclusive_session_restores_running_services(self):
        runner = FakeRunner({
            ("systemctl", "is-active", "readsb"): "active\n",
            ("systemctl", "is-active", "tar1090"): "inactive\n",
            ("systemctl", "is-active", "sdrpp"): "inactive\n",
            ("aiov2_ctl", "--status"): "SDR GPIO22: ON\n",
            ("lsusb",): "Bus 001 Device 002: ID 0bda:2838 Realtek RTL2838\n",
        })
        with TemporaryDirectory() as directory:
            coordinator = RadioCoordinator(
                runner,
                lock_path=Path(directory) / "sdr.lock",
                poll_interval=0,
            )
            with patch("radio_coordinator.shutil.which", return_value="/usr/bin/tool"):
                with coordinator.exclusive_session():
                    self.assertIn(["systemctl", "stop", "readsb", "tar1090"], runner.calls)
                self.assertIn(["systemctl", "start", "readsb", "tar1090"], runner.calls)


class GPSStatusTests(unittest.TestCase):
    def test_rejects_fix_without_enough_satellites(self):
        gps_samples = "\n".join([
            json.dumps({"class": "TPV", "mode": 3, "lat": 49.1, "lon": -123.1}),
            json.dumps({"class": "SKY", "satellites": [{"used": True}, {"used": False}]}),
        ])
        completed = subprocess.CompletedProcess(["gpspipe"], 0, stdout=gps_samples, stderr="")
        with patch("status_api.shutil.which", return_value="/usr/bin/gpspipe"):
            with patch("status_api.subprocess.run", return_value=completed):
                result = status_api.get_gps_status()

        self.assertEqual(result["status"], "untrusted_fix")
        self.assertEqual(result["satellites_used"], 1)


if __name__ == "__main__":
    unittest.main()