#!/usr/bin/env python3
"""GTK launcher for the external SDRDecoder satellite ground-station app."""

from __future__ import annotations

import subprocess
import threading
import urllib.request
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib


SDRDECODER_DIR = Path.home() / ".config" / "k7bat-uconsole-status" / "plugins" / "sdrdecoder"
SERVER = SDRDECODER_DIR / "server.py"
WEB_URL = "http://127.0.0.1:8888"


class SDRDecoderWindow(Gtk.Window):
    """Control window for the separately installed SDRDecoder web app."""

    def __init__(self, parent_app=None):
        super().__init__(title="SDRDecoder Ground Station")
        self.set_default_size(620, 420)
        if parent_app:
            self.set_transient_for(parent_app)
        self.server_process = None
        self.connect("destroy", self._on_destroy)
        self._build_ui()
        self._refresh_state()

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_margin_top(12)
        root.set_margin_bottom(12)
        root.set_margin_start(12)
        root.set_margin_end(12)

        title = Gtk.Label()
        title.set_markup("<b><big>SDRDecoder Ground Station</big></b>")
        title.set_xalign(0)
        root.pack_start(title, False, False, 0)

        description = Gtk.Label(label="External satellite decoder plugin. Live capture requires exclusive RTL-SDR access.")
        description.set_line_wrap(True)
        description.set_xalign(0)
        root.pack_start(description, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for label, callback in (
            ("Start Web Console", self.start_server),
            ("Open Web Console", self.open_console),
            ("Stop Server", self.stop_server),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", callback)
            actions.pack_start(button, False, False, 0)
        root.pack_start(actions, False, False, 0)

        modes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for label, mode in (("Synthetic NOAA", "generate_sample"), ("Upcoming Passes", "passes"), ("Live NOAA", "noaa_apt")):
            button = Gtk.Button(label=label)
            button.connect("clicked", self.run_cli, mode)
            modes.pack_start(button, False, False, 0)
        root.pack_start(modes, False, False, 0)

        self.status = Gtk.Label(label="Checking plugin installation...")
        self.status.set_xalign(0)
        self.status.set_line_wrap(True)
        root.pack_start(self.status, False, False, 0)

        output_scroll = Gtk.ScrolledWindow()
        output_scroll.set_vexpand(True)
        self.output = Gtk.TextView()
        self.output.set_editable(False)
        self.output.set_monospace(True)
        output_scroll.add(self.output)
        root.pack_start(output_scroll, True, True, 0)
        self.add(root)

    def _append_output(self, text):
        buffer = self.output.get_buffer()
        end = buffer.get_end_iter()
        buffer.insert(end, text + "\n")

    def _refresh_state(self):
        if SERVER.exists():
            self.status.set_text(f"Installed: {SDRDECODER_DIR}")
        else:
            self.status.set_text("SDRDecoder plugin is not installed. Run the installer or clone it into the plugin directory.")

    def start_server(self, _button=None):
        if not SERVER.exists():
            self._refresh_state()
            return
        if self.server_process and self.server_process.poll() is None:
            self.status.set_text(f"Web console already running at {WEB_URL}")
            return
        self.server_process = subprocess.Popen(
            ["/usr/bin/python3", str(SERVER), "--host", "127.0.0.1", "--port", "8888"],
            cwd=str(SDRDECODER_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.status.set_text(f"Starting web console at {WEB_URL}")
        threading.Thread(target=self._read_server_output, daemon=True).start()

    def _read_server_output(self):
        if not self.server_process or not self.server_process.stdout:
            return
        for line in self.server_process.stdout:
            GLib.idle_add(self._append_output, line.rstrip())

    def stop_server(self, _button=None):
        if self.server_process and self.server_process.poll() is None:
            self.server_process.terminate()
            self.status.set_text("SDRDecoder server stopped")
        else:
            self.status.set_text("SDRDecoder server is not running")

    def open_console(self, _button=None):
        try:
            urllib.request.urlopen(WEB_URL, timeout=2).close()
            subprocess.Popen(["xdg-open", WEB_URL])
            self.status.set_text(f"Opened {WEB_URL}")
        except Exception as exc:
            self.status.set_text(f"Web console is not reachable: {exc}")

    def run_cli(self, _button, mode):
        cli = SDRDECODER_DIR / "satdec" / "cli.py"
        if not cli.exists():
            self._refresh_state()
            return
        command = ["/usr/bin/python3", str(cli), "--mode", mode, "--output", str(SDRDECODER_DIR / "output")]
        if mode == "noaa_apt":
            command.extend(["--input", "live", "--freq", "137.9125", "--duration", "30"])
        self.status.set_text(f"Running SDRDecoder {mode}...")

        def worker():
            result = subprocess.run(command, cwd=str(SDRDECODER_DIR), capture_output=True, text=True, timeout=90, check=False)
            GLib.idle_add(self._append_output, result.stdout or result.stderr)
            GLib.idle_add(self.status.set_text, f"SDRDecoder {mode} exited with code {result.returncode}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_destroy(self, _window):
        self.stop_server()
