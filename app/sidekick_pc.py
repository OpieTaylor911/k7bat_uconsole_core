#!/usr/bin/env python3
"""SideKickPC: Windows setup tool for K7BAT Sidekick devices."""

import json
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


class SideKickPC(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("K7BAT SideKickPC")
        self.geometry("760x620")
        self.minsize(680, 520)
        self.events = queue.Queue()
        self.api_key = ""
        self._busy = False
        self._build_ui()
        self.refresh_ports()
        self.after(100, self._drain_events)

    def _build_ui(self):
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="K7BAT SideKickPC", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(root, text="Enroll a Sidekick with the uConsole, then provision Wi-Fi and API settings over USB.").pack(anchor="w", pady=(0, 12))

        api = ttk.LabelFrame(root, text="uConsole API enrollment", padding=10)
        api.pack(fill="x", pady=(0, 10))
        self.api_url = self._field(api, "API base URL", "http://192.168.254.226:8080", 0)
        self.device_id = self._field(api, "Device ID", "sidekick-pc-001", 1)
        self.device_name = self._field(api, "Device name", "K7BAT Sidekick", 2)
        self.mac_address = self._field(api, "MAC address", "AA:BB:CC:DD:EE:90", 3)
        ttk.Label(api, text="Pair code").grid(row=4, column=0, sticky="w", pady=3)
        self.pair_code = ttk.Entry(api, width=30)
        self.pair_code.grid(row=4, column=1, sticky="ew", pady=3)
        buttons = ttk.Frame(api)
        buttons.grid(row=5, column=1, sticky="w", pady=(6, 0))
        ttk.Button(buttons, text="Start enrollment", command=self.start_enrollment).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Confirm pair code", command=self.confirm_enrollment).pack(side="left")
        self.api_key_label = ttk.Label(api, text="API key: not acquired")
        self.api_key_label.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
        api.columnconfigure(1, weight=1)

        serial_frame = ttk.LabelFrame(root, text="Sidekick serial provisioning", padding=10)
        serial_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(serial_frame, text="COM port").grid(row=0, column=0, sticky="w", pady=3)
        self.port_combo = ttk.Combobox(serial_frame, state="readonly", width=34)
        self.port_combo.grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Button(serial_frame, text="Refresh ports", command=self.refresh_ports).grid(row=0, column=2, padx=(6, 0))
        self.ssid = self._field(serial_frame, "Wi-Fi SSID", "", 1)
        self.password = self._field(serial_frame, "Wi-Fi password", "", 2, show="*")
        self.server = self._field(serial_frame, "Server endpoint", "192.168.254.226:8080", 3)
        actions = ttk.Frame(serial_frame)
        actions.grid(row=4, column=1, sticky="w", pady=(8, 0))
        ttk.Button(actions, text="Probe firmware", command=self.probe_firmware).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Push configuration", command=self.push_configuration).pack(side="left")
        serial_frame.columnconfigure(1, weight=1)

        log_frame = ttk.LabelFrame(root, text="Activity", padding=6)
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, height=12, state="disabled", wrap="none", font=("Consolas", 9))
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)
        self.status = ttk.Label(root, text="Ready")
        self.status.pack(anchor="w", pady=(8, 0))

    def _field(self, parent, label, value, row, show=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        entry = ttk.Entry(parent, show=show or "")
        entry.insert(0, value)
        entry.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
        return entry

    def _log(self, text):
        self.events.put(("log", text))

    def _set_status(self, text):
        self.events.put(("status", text))

    def _drain_events(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == "log":
                self.log_text.configure(state="normal")
                self.log_text.insert("end", value.rstrip() + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
            elif kind == "status":
                self.status.configure(text=value)
            elif kind == "key":
                self.api_key = value
                self.api_key_label.configure(text=f"API key: acquired ({value[:8]}...{value[-4:]})")
            elif kind == "busy":
                self._busy = value
        self.after(100, self._drain_events)

    def _run_worker(self, label, function):
        if self._busy:
            messagebox.showinfo("Busy", "Another SideKickPC operation is already running.")
            return
        self.events.put(("busy", True))
        self._set_status(label)
        threading.Thread(target=self._worker_wrapper, args=(function,), daemon=True).start()

    def _worker_wrapper(self, function):
        try:
            function()
        except Exception as exc:
            self._log(f"ERROR: {exc}")
            self._set_status(f"Error: {exc}")
        finally:
            self.events.put(("busy", False))

    def _post_json(self, path, payload):
        request = Request(
            self.api_url.get().strip().rstrip("/") + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8", "replace"))

    def start_enrollment(self):
        def work():
            payload = {
                "device_id": self.device_id.get().strip(),
                "name": self.device_name.get().strip(),
                "mac_address": self.mac_address.get().strip(),
                "board": "SideKickPC",
            }
            result = self._post_json("/api/v2/device/enroll/start", payload)
            self.pair_code.delete(0, "end")
            self.pair_code.insert(0, result.get("code", ""))
            self._log(f"Enrollment started; code expires in {result.get('expires_in_seconds', '?')} seconds.")
            self._set_status("Enter the pairing code shown on the uConsole, then confirm.")
        self._run_worker("Starting enrollment...", work)

    def confirm_enrollment(self):
        def work():
            result = self._post_json("/api/v2/device/enroll/confirm", {
                "device_id": self.device_id.get().strip(),
                "mac_address": self.mac_address.get().strip(),
                "code": self.pair_code.get().strip(),
            })
            key = result.get("api_key", "")
            if result.get("status") != "paired" or not key:
                raise RuntimeError(f"Enrollment was not accepted: {result}")
            self.events.put(("key", key))
            self._log("Enrollment confirmed and API key acquired. The key is masked in this app.")
            self._set_status("Paired. Select a COM port and push configuration.")
        self._run_worker("Confirming enrollment...", work)

    def refresh_ports(self):
        if list_ports is None:
            self.port_combo["values"] = []
            self._set_status("Install pyserial first: python -m pip install pyserial")
            return
        ports = [port.device for port in list_ports.comports()]
        self.port_combo["values"] = ports
        if ports:
            self.port_combo.current(0)
            self._set_status(f"Found {len(ports)} serial port(s).")
        else:
            self._set_status("No COM ports found.")

    def _open_serial(self, port):
        options = {"baudrate": 115200, "timeout": 0.25, "write_timeout": 5}
        device = serial.Serial(port=None, **options)
        device.port = port
        device._dtr_state = False
        device._rts_state = False
        device.open()
        device.dtr = False
        device.rts = False
        time.sleep(3)
        return device

    def _probe_and_configure(self, configure):
        if serial is None:
            raise RuntimeError("pyserial is not installed")
        port = self.port_combo.get().strip()
        if not port:
            raise RuntimeError("Select a COM port")
        if configure and not self.api_key:
            raise RuntimeError("Acquire an API key through enrollment first")
        for attempt in range(1, 4):
            try:
                self._log(f"Opening {port}, attempt {attempt}/3...")
                with self._open_serial(port) as device:
                    device.reset_input_buffer()
                    device.write(b"GETVERSION\r\n")
                    device.flush()
                    self._log(">>> GETVERSION\\r\\n")
                    deadline = time.time() + 2
                    lines = []
                    while time.time() < deadline:
                        raw = device.readline()
                        if raw:
                            line = raw.decode(errors="replace").strip()
                            lines.append(line)
                            self._log(f"<<< {line}")
                    if not any(line.startswith("VERSION=") or line.startswith("BOARD=") for line in lines):
                        raise RuntimeError("No firmware response")
                    if configure:
                        commands = [
                            f"SETWIFI={self.ssid.get()}|{self.password.get()}\r\n",
                            f"TOKEN={self.api_key}\r\n",
                            f"SERVER={self.server.get().strip()}\r\n",
                        ]
                        for command in commands:
                            device.write(command.encode())
                            device.flush()
                            self._log(f">>> {command.split('=', 1)[0]}=***\\r\\n")
                            time.sleep(0.5)
                    return
            except (serial.SerialException, OSError, RuntimeError) as exc:
                self._log(f"Attempt {attempt}: {exc}")
                time.sleep(1.5)
        raise RuntimeError("Serial provisioning failed after three attempts")

    def probe_firmware(self):
        self._run_worker("Probing firmware...", lambda: (self._probe_and_configure(False), self._set_status("Firmware responded.")))

    def push_configuration(self):
        self._run_worker("Pushing Wi-Fi, API key, and server endpoint...", lambda: (self._probe_and_configure(True), self._set_status("Sidekick configuration pushed.")))


if __name__ == "__main__":
    if serial is None:
        raise SystemExit("pyserial is required. Install with: python -m pip install pyserial")
    app = SideKickPC()
    app.mainloop()