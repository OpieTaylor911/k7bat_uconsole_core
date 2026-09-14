import json
import subprocess
import unittest
from tempfile import TemporaryDirectory

from ac1200_diagnostics import AC1200Diagnostics


class FakeRunner:
    def __init__(self, outputs):
        self.outputs = outputs

    def __call__(self, args, timeout):
        key = tuple(args)
        return subprocess.CompletedProcess(args, 0, self.outputs.get(key, ""), "")


class AC1200DiagnosticsTests(unittest.TestCase):
    def make_diagnostics(self, speed="5000M"):
        outputs = {
            ("lsusb",): "Bus 003 Device 002: ID 0e8d:7961 MediaTek Wireless_Device\n",
            ("lsusb", "-t"): f"    |__ Port 1: Dev 2, If 0, Class=Vendor Specific, Driver=mt7921u, {speed}\n    |__ Port 1: Dev 2, If 1, Class=Wireless, Driver=btusb, {speed}\n",
            ("lsmod",): "mt7921u 123 0\nmt7921_common 123 1 mt7921u\nmt76_connac_lib 123 1 mt7921_common\nmt76 123 1 mt76_connac_lib\nbtusb 123 0\nmac80211 123 1 mt76\n",
            ("rfkill", "list"): "0: phy1: Wireless LAN\n\tSoft blocked: no\n\tHard blocked: no\n",
            ("dmesg",): "mt7921u: firmware initialized\n",
            ("iw", "dev"): "phy#1\n\tInterface wlan1\n\t\ttype managed\n",
            ("iw", "phy", "phy1", "info"): "Band 1: 2.4 GHz\n\t* 2412 MHz\nBand 2: 5 GHz\n\t* 5180 MHz\n\t* monitor\n\t* AP\n",
            ("hciconfig",): "hci1: Type: Primary Bus: USB\n",
        }
        return AC1200Diagnostics(FakeRunner(outputs))

    def test_basic_report_detects_board_and_capabilities(self):
        report = self.make_diagnostics().run_basic()
        self.assertEqual(report["summary"]["fail"], 0)
        self.assertEqual(report["summary"]["total"], 8)
        bands = next(check for check in report["checks"] if check["test"] == "Supported bands")
        self.assertEqual(bands["details"]["bands"], ["2.4 GHz", "5 GHz"])

    def test_usb_fallback_is_warning(self):
        report = self.make_diagnostics("480M").run_basic()
        speed = next(check for check in report["checks"] if check["test"] == "USB link speed")
        self.assertEqual(speed["status"], "WARN")
        self.assertEqual(speed["details"]["speed"], "480M")

    def test_report_writers_create_machine_and_human_files(self):
        report = self.make_diagnostics().run_basic()
        with TemporaryDirectory() as directory:
            json_path = AC1200Diagnostics.write_json(report, f"{directory}/report.json")
            markdown_path = AC1200Diagnostics.write_markdown(report, f"{directory}/report.md")
            self.assertEqual(json.loads(json_path.read_text())["tool"], "k7bat-ac1200-diagnostics")
            self.assertIn("# AC1200 Diagnostic Report", markdown_path.read_text())


if __name__ == "__main__":
    unittest.main()