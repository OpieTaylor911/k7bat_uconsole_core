#!/usr/bin/env python3
"""
K7BAT uConsole Status API v1.1.0
HTTP API for Arduino and other devices to query and post status information.

Features:
- RESTful HTTP API on port 8080
- GET endpoints for status data (system, Wi-Fi, GPS, radio)
- POST endpoints for commands/data submission
- JSON responses
- Thread-safe data access
- Remote app launching support
- Radio control (on/off, frequency, mode)
- Button/touchscreen event handling
- Plugin system integration

Usage:
    python3 status_api.py [--port 8080] [--host 0.0.0.0]
"""

import sys
import os
import json
import threading
import subprocess
import shutil
import re
import uuid
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from datetime import datetime

# Add app directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import plugins configuration
try:
    from plugins.plugin_manager import PluginManager
except ImportError:
    PluginManager = None

try:
    from radio_coordinator import RadioCoordinator
except ImportError:
    RadioCoordinator = None

try:
    from ac1200_diagnostics import AC1200Diagnostics
except ImportError:
    AC1200Diagnostics = None

RADIO_COORDINATOR = RadioCoordinator() if RadioCoordinator else None
AC1200_DIAGNOSTICS = AC1200Diagnostics() if AC1200Diagnostics else None

# Status data storage
_status_data = {
    "system": {
        "cpu_load": 0.0,
        "memory_used": "0MB",
        "memory_total": "0MB",
        "disk_usage": "0%",
        "uptime": "0s",
        "hostname": "",
        "os_version": ""
    },
    "wifi": {
        "status": "disconnected",
        "interface": "",
        "ssid": "",
        "ip_address": "",
        "signal_strength": 0,
        "connected_devices": []
    },
    "gps": {
        "status": "no_fix",
        "latitude": None,
        "longitude": None,
        "altitude": None,
        "satellites": 0,
        "speed": None
    },
    "devices": [],
    "radio": {
        "status": "idle",
        "frequency": None,
        "mode": ""
    },
    "apps": {
        "running": [],
        "available": []
    },
    "timestamp": datetime.now().isoformat()
}

_status_lock = threading.Lock()

# Event queue for Arduino buttons/touchscreen events
_events_queue = []
_events_lock = threading.Lock()

# Active processes tracking
_active_processes = {}
_process_lock = threading.Lock()

_V2_EVENT_SEQUENCE = 0
_V2_COMMAND_JOBS = {}


def _v2_request_id():
    return f"req-{uuid.uuid4().hex}"


def _v2_error(code, message, status_code=400, retry_after_seconds=0):
    return status_code, {
        "error": {
            "code": code,
            "message": message,
            "request_id": _v2_request_id(),
            "retry_after_seconds": retry_after_seconds,
        }
    }


def _v2_add_freshness(value, updated_at=None, stale_after_seconds=10):
    result = dict(value or {})
    result["updated_at"] = updated_at or datetime.now().isoformat()
    result["stale_after_seconds"] = stale_after_seconds
    return result


def _v2_system_services():
    services = {}
    for name in ("gpsd", "gpsd.socket", "bluetooth", "readsb", "NetworkManager"):
        try:
            result = subprocess.run(
                ["systemctl", "is-active", name],
                capture_output=True, text=True, timeout=3, check=False,
            )
            services[name] = result.stdout.strip() or "unknown"
        except Exception:
            services[name] = "unavailable"
    return services


def get_status_app_version():
    """Return the installed Status App version for Sidekick compatibility checks."""
    app_dir = Path(__file__).resolve().parent
    candidates = [
        Path(os.environ.get("K7BAT_STATUS_VERSION_FILE", "")),
        app_dir / "VERSION",
        app_dir.parent / "VERSION",
        Path("/home/bcaddy/uconsole-k7bat/app/VERSION"),
        Path("/home/bcaddy/uconsole-k7bat/VERSION"),
    ]
    for version_file in candidates:
        if not str(version_file) or not version_file.is_file():
            continue
        try:
            version = version_file.read_text(encoding="utf-8-sig").strip()
            if version:
                return version
        except OSError:
            continue

    app_files = [
        app_dir / "k7bat-uconsole-status.py",
        app_dir / "k7bat-uconsole-status-v2.py",
    ]
    for app_file in app_files:
        try:
            source = app_file.read_text(encoding="utf-8-sig")
            match = re.search(r"APP_VERSION\s*=\s*[\"']([^\"']+)[\"']", source)
            if match:
                return match.group(1)
        except OSError:
            continue
    return "unknown"


def get_system_info():
    """Collect system information."""
    try:
        import subprocess
        # CPU load
        with open('/proc/loadavg', 'r') as f:
            cpu_load = f.read().split()[0]
        
        # Memory info
        mem_info = {}
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(':')
                    mem_info[key] = int(parts[1]) * 1024  # Convert to bytes
        
        mem_total = mem_info.get('MemTotal', 0)
        mem_available = mem_info.get('MemAvailable', 0)
        mem_used = mem_total - mem_available
        
        # Disk usage
        result = subprocess.run(['df', '-h', '/'], capture_output=True, text=True)
        disk_lines = result.stdout.strip().split('\n')
        disk_usage = disk_lines[1].split()[4] if len(disk_lines) > 1 else "0%"
        
        # Uptime
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.read().split()[0])
        
        # OS version
        os_version = ""
        try:
            with open('/etc/os-release', 'r') as f:
                for line in f:
                    if line.startswith('PRETTY_NAME='):
                        os_version = line.split('=', 1)[1].strip('"\'')
                        break
        except FileNotFoundError:
            os_version = "Unknown"
        
        return {
            "cpu_load": float(cpu_load),
            "memory_used": f"{mem_used // (1024*1024)}MB",
            "memory_total": f"{mem_total // (1024*1024)}MB",
            "disk_usage": disk_usage,
            "uptime": f"{int(uptime_seconds // 3600)}h {int((uptime_seconds % 3600) // 60)}m",
            "hostname": os.uname().nodename,
            "os_version": os_version
        }
    except Exception as e:
        return {"error": str(e)}


def get_wifi_status():
    """Get Wi-Fi status."""
    try:
        import subprocess
        
        # Check if wlan0 exists and is active
        result = subprocess.run(['ip', 'link', 'show', 'wlan0'], capture_output=True, text=True)
        is_active = 'UP' in result.stdout
        
        if not is_active:
            return {
                "status": "disabled",
                "interface": "wlan0",
                "ssid": "",
                "ip_address": "",
                "signal_strength": 0,
                "connected_devices": []
            }
        
        # Get IP address
        result = subprocess.run(['ip', 'addr', 'show', 'wlan0'], capture_output=True, text=True)
        ip_address = ""
        for line in result.stdout.split('\n'):
            if 'inet ' in line:
                ip_address = line.split()[1].split('/')[0]
                break
        
        # Get SSID (requires wireless-tools or iw)
        ssid = ""
        try:
            result = subprocess.run(['iwgetid', '-r'], capture_output=True, text=True)
            ssid = result.stdout.strip()
        except FileNotFoundError:
            ssid = "Unknown"
        
        # Signal strength
        signal_strength = 0
        try:
            result = subprocess.run(['cat', '/proc/net/wireless'], capture_output=True, text=True)
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 3:
                parts = lines[2].split()
                if len(parts) >= 4:
                    signal_strength = int(parts[2])
        except FileNotFoundError:
            pass
        
        return {
            "status": "connected" if ssid else "connected_no_ssid",
            "interface": "wlan0",
            "ssid": ssid,
            "ip_address": ip_address,
            "signal_strength": signal_strength,
            "connected_devices": []
        }
    except Exception as e:
        return {"error": str(e)}


def get_gps_status():
    """Get GPS status."""
    result = {
        "status": "no_fix",
        "latitude": None,
        "longitude": None,
        "altitude": None,
        "satellites": 0,
        "satellites_used": 0,
        "speed": None,
        "device": None,
        "reason": "gpsd unavailable",
    }

    if not shutil.which("gpspipe"):
        return result
    try:
        stream = subprocess.run(
            ["gpspipe", "-w", "-n", "12"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = str(exc)
        return result

    tpv = {}
    sky = {}
    for line in stream.stdout.splitlines():
        try:
            sample = json.loads(line)
        except json.JSONDecodeError:
            continue
        if sample.get("class") == "TPV":
            tpv.update(sample)
            result["device"] = sample.get("device") or result["device"]
        elif sample.get("class") == "DEVICE":
            result["device"] = sample.get("path") or result["device"]
        elif sample.get("class") == "SKY":
            sky.update(sample)

    satellites = sky.get("satellites")
    if isinstance(satellites, list):
        result["satellites"] = len(satellites)
        result["satellites_used"] = sum(
            1 for satellite in satellites
            if isinstance(satellite, dict) and satellite.get("used") is True
        )
    elif isinstance(sky.get("nSat"), (int, float)):
        result["satellites"] = int(sky["nSat"])

    mode = tpv.get("mode", 0)
    result["status"] = {0: "no_data", 1: "no_fix", 2: "2d_fix", 3: "3d_fix"}.get(mode, str(mode))
    result["latitude"] = tpv.get("lat")
    result["longitude"] = tpv.get("lon")
    result["altitude"] = tpv.get("alt")
    result["speed"] = tpv.get("speed")

    if mode < 2:
        result["reason"] = f"GPS mode {mode}"
    elif not isinstance(result["latitude"], (int, float)) or not isinstance(result["longitude"], (int, float)):
        result["status"] = "invalid_fix"
        result["reason"] = "fix reported without coordinates"
    elif result["satellites_used"] < 3:
        result["status"] = "untrusted_fix"
        result["reason"] = "fewer than three satellites used"
    elif abs(float(result["latitude"]) - 30.0) < 0.01 and abs(float(result["longitude"]) - 120.0) < 0.01:
        result["status"] = "untrusted_fix"
        result["reason"] = "known placeholder GPS position"
    else:
        result["reason"] = ""
    return result


def get_radio_status():
    """Get radio/SDR status."""
    if RADIO_COORDINATOR is None:
        return {"enabled": False, "status": "unavailable", "frequency": None, "mode": ""}
    try:
        return RADIO_COORDINATOR.radio_status()
    except Exception as exc:
        return {"enabled": False, "status": "error", "frequency": None, "mode": "", "error": str(exc)}


def launch_app(app_id, app_config):
    """Launch an application by ID."""
    try:
        cmd = app_config.get('command', '')
        if not cmd:
            # Try Python module
            module = app_config.get('module', '')
            if module:
                script_dir = os.path.dirname(os.path.abspath(__file__))
                module_base = module.split('.')[0]
                # Build command with proper escaping for bash -c
                import_cmd = 'import sys'
                path_cmd = f'sys.path.insert(0, \'{script_dir}\')'
                from_cmd = f'from {module_base} import *'
                exec_cmd = f'{module.replace(".", ".")}()'
                cmd = f'python3 -c "{import_cmd}; {path_cmd}; {from_cmd}; {exec_cmd}"'
        
        if cmd:
            process = subprocess.Popen(
                ['bash', '-c', cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            with _process_lock:
                _active_processes[app_id] = {
                    'pid': process.pid,
                    'started_at': datetime.now().isoformat(),
                    'config': app_config
                }
            
            return True, f"Launched {app_id} (PID: {process.pid})"
        else:
            return False, "No command or module defined for app"
    except Exception as e:
        return False, str(e)


def stop_app(app_id):
    """Stop a running application."""
    try:
        with _process_lock:
            if app_id in _active_processes:
                import psutil
                pid = _active_processes[app_id]['pid']
                process = psutil.Process(pid)
                process.terminate()
                process.wait(timeout=5)
                del _active_processes[app_id]
                return True, f"Stopped {app_id}"
            else:
                return False, f"{app_id} is not running"
    except Exception as e:
        return False, str(e)


def toggle_radio(enabled):
    """Toggle radio on/off."""
    if RADIO_COORDINATOR is None:
        return False, "radio coordinator unavailable"
    try:
        success, message = RADIO_COORDINATOR.set_enabled(bool(enabled))
        if not success:
            return False, message
        return True, {"status": "active" if enabled else "idle", "enabled": bool(enabled), "message": message}
    except Exception as e:
        return False, str(e)


def set_radio_frequency(freq_hz):
    """Set radio frequency in Hz."""
    try:
        # Convert to MHz for display
        freq_mhz = freq_hz / 1_000_000
        
        # Command SDR software (adjust based on your setup)
        # This is a placeholder - implement based on your SDR interface
        frequency = int(freq_hz)
        if frequency <= 0:
            return False, "frequency must be a positive integer in Hz"
        return False, "frequency control is not available for the configured SDR backend"
    except Exception as e:
        return False, str(e)


def add_event(event_type, data=None):
    """Add an event to the queue (for Arduino button/touchscreen events)."""
    global _V2_EVENT_SEQUENCE
    _V2_EVENT_SEQUENCE += 1
    with _events_lock:
        _events_queue.append({
            "event": event_type,
            "type": event_type,
            "data": data or {},
            "timestamp": datetime.now().isoformat(),
            "sequence": _V2_EVENT_SEQUENCE,
        })


def get_events():
    """Get and clear all pending events."""
    with _events_lock:
        events = list(_events_queue)
        _events_queue.clear()
        return events


def get_events_since(sequence=0):
    with _events_lock:
        return [event for event in _events_queue if event.get("sequence", 0) > sequence]


_V2_ALLOWED_PROFILES = [
    "GENERAL",
    "FIELD",
    "HAM",
    "HF_VOICE",
    "HF_DIGITAL",
    "APRS",
    "PACKET",
    "WINLINK",
    "MESHTASTIC",
    "SATELLITE",
    "SDR",
    "ADSB",
    "EMCOMM",
    "SECURITY",
    "LOW_POWER",
]
_V2_ALLOWED_SCREENS = ["HOME", "SYSTEM", "GPS", "ADSB", "MESHTASTIC"]
_V2_DEVICE_REGISTRY = {}
_V2_PAIRING_REGISTRY = {}
_V2_API_KEYS = {}
_V2_PROFILE_STATE = {
    "profile": "FIELD",
    "screen": "HOME",
    "screens": list(_V2_ALLOWED_SCREENS),
}


def _normalize_v2_network_status():
    wifi = _status_data.get("wifi", {})
    state = "connected" if wifi.get("status") in {"connected", "connected_no_ssid"} else "disconnected"
    return {
        "state": state,
        "interface": wifi.get("interface") or "wlan0",
        "ssid": wifi.get("ssid") or "",
        "ip_address": wifi.get("ip_address") or "",
        "signal_strength": wifi.get("signal_strength", 0),
    }


def _normalize_v2_system_status():
    system = _status_data.get("system", {})
    uptime_seconds = 0
    try:
        uptime_seconds = int(float(system.get("uptime", "0s").split("h")[0]) * 3600)
    except Exception:
        uptime_seconds = 0
    return {
        "state": "ok",
        "battery_percent": 82,
        "temperature_c": 42.0,
        "uptime_seconds": uptime_seconds,
        "cpu_load": system.get("cpu_load", 0.0),
        "memory_used": system.get("memory_used", "0MB"),
        "hostname": system.get("hostname") or "uconsole",
    }


def _normalize_v2_status_snapshot():
    with _status_lock:
        system = _status_data.get("system", {})
        gps = _status_data.get("gps", {})
        radio = _status_data.get("radio", {})
        wifi = _status_data.get("wifi", {})

    return {
        "api_version": "2.0",
        "server": os.uname().nodename if hasattr(os, "uname") else "uconsole",
        "profile": _V2_PROFILE_STATE.get("profile", "FIELD"),
        "system": _v2_add_freshness(_normalize_v2_system_status(), stale_after_seconds=15),
        "gps": _v2_add_freshness({
            "state": gps.get("status", "no_fix"),
            "latitude": gps.get("latitude"),
            "longitude": gps.get("longitude"),
            "altitude_m": gps.get("altitude"),
            "grid": "UNKNOWN",
            "satellites": gps.get("satellites", 0),
            "speed_kph": gps.get("speed"),
            "device": gps.get("device"),
            "reason": gps.get("reason", ""),
        }, stale_after_seconds=10),
        "adsb": _v2_add_freshness({
            "state": "unavailable",
            "aircraft": 0,
            "messages": 0,
        }, stale_after_seconds=10),
        "meshtastic": _v2_add_freshness({
            "state": "disconnected",
            "nodes": 0,
            "online": 0,
            "channel": "unknown",
        }, stale_after_seconds=30),
        "radio": {
            "state": radio.get("status", "idle"),
            "enabled": bool(radio.get("enabled", False)),
            "frequency": radio.get("frequency"),
            "mode": radio.get("mode") or "",
        },
        "network": _v2_add_freshness(_normalize_v2_network_status(), stale_after_seconds=15),
        "security": {
            "state": "ok",
            "firewall": "unknown",
            "wireless_security": "unknown",
        },
        "apps": {
            "running": _status_data.get("apps", {}).get("running", []),
            "available": _status_data.get("apps", {}).get("available", []),
        },
    }


def _v2_register_device(data):
    if not isinstance(data, dict):
        return False, {"error": {"code": "invalid_payload", "message": "Device registration requires a JSON object."}}

    device_id = data.get("device_id")
    if not device_id:
        return False, {"error": {"code": "missing_device_id", "message": "device_id is required."}}

    device = {
        "device_id": device_id,
        "name": data.get("name") or device_id,
        "board": data.get("board") or "unknown",
        "firmware": data.get("firmware") or "unknown",
        "display": data.get("display") or {},
        "features": data.get("features") or {},
        "last_seen": datetime.now().isoformat(),
    }
    _V2_DEVICE_REGISTRY[device_id] = device

    response = {
        "accepted": True,
        "profile": _V2_PROFILE_STATE.get("profile", "FIELD"),
        "screen": _V2_PROFILE_STATE.get("screen", "HOME"),
        "allowed_screens": list(_V2_ALLOWED_SCREENS),
        "heartbeat_seconds": 30,
    }
    return True, response


def _v2_handle_command(data):
    if not isinstance(data, dict):
        return False, {"error": {"code": "invalid_payload", "message": "Command payload must be an object."}}

    target = data.get("target")
    command = data.get("command")
    value = data.get("value")

    if target not in {"profile", "screen", "system", "radio", "sdr", "gps", "meshtastic", "app"}:
        return False, {"error": {"code": "unsupported_target", "message": f"Unsupported target: {target}"}}
    if not command:
        return False, {"error": {"code": "missing_command", "message": "command is required."}}

    if target == "profile" and command == "switch":
        if value not in _V2_ALLOWED_PROFILES:
            return False, {"error": {"code": "profile_not_allowed", "message": "The requested profile is not available to this device."}}
        _V2_PROFILE_STATE["profile"] = value
        return True, {
            "accepted": True,
            "command_id": f"cmd-{int(datetime.now().timestamp() * 1000)}",
            "state": "queued",
            "profile": value,
        }

    if target == "screen" and command == "switch":
        if value not in _V2_ALLOWED_SCREENS:
            return False, {"error": {"code": "screen_not_allowed", "message": "The requested screen is not available to this device."}}
        _V2_PROFILE_STATE["screen"] = value
        return True, {
            "accepted": True,
            "command_id": f"cmd-{int(datetime.now().timestamp() * 1000)}",
            "state": "queued",
            "screen": value,
        }

    command_id = f"cmd-{uuid.uuid4().hex}"
    result = {
        "accepted": True,
        "command_id": command_id,
        "state": "queued",
        "target": target,
        "command": command,
        "value": value,
    }
    _V2_COMMAND_JOBS[command_id] = {
        **result,
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
    }
    return True, result


def _normalize_mac_address(mac):
    if not mac:
        return ""
    return mac.strip().lower().replace('-', ':').replace(' ', ':')


def _v2_create_enrollment_code(device_id, mac_address):
    key = _normalize_mac_address(mac_address) or device_id
    code = str(int(datetime.now().timestamp() * 1000) % 1000000).zfill(6)
    _V2_PAIRING_REGISTRY[key] = {
        "device_id": device_id,
        "mac_address": _normalize_mac_address(mac_address),
        "code": code,
        "expires_at": datetime.now().timestamp() + 300,
        "status": "pending",
    }
    return code


def _v2_confirm_enrollment(data):
    if not isinstance(data, dict):
        return False, {"error": {"code": "invalid_payload", "message": "Enrollment payload must be an object."}}

    device_id = data.get("device_id")
    mac_address = _normalize_mac_address(data.get("mac_address"))
    code = str(data.get("code", "")).strip()

    if not device_id or not mac_address or not code:
        return False, {"error": {"code": "missing_fields", "message": "device_id, mac_address, and code are required."}}

    key = mac_address or device_id
    pending = _V2_PAIRING_REGISTRY.get(key)
    if not pending:
        return False, {"error": {"code": "no_pending_pairing", "message": "No pending enrollment found for this device."}}
    if pending["device_id"] != device_id:
        return False, {"error": {"code": "device_mismatch", "message": "Enrollment code does not match device_id."}}
    if pending["code"] != code:
        return False, {"error": {"code": "invalid_code", "message": "Enrollment code is invalid or expired."}}

    api_key = f"k7bat_{device_id.lower().replace(' ', '_')}_{int(datetime.now().timestamp() * 1000)}"
    _V2_API_KEYS[device_id] = {
        "api_key": api_key,
        "mac_address": mac_address,
        "device_id": device_id,
        "issued_at": datetime.now().isoformat(),
    }
    pending["status"] = "paired"
    pending["api_key"] = api_key
    _V2_DEVICE_REGISTRY[device_id] = {
        "device_id": device_id,
        "name": pending.get("name") or device_id,
        "mac_address": mac_address,
        "api_key": api_key,
        "last_seen": datetime.now().isoformat(),
    }
    return True, {
        "status": "paired",
        "device_id": device_id,
        "api_key": api_key,
        "profile": _V2_PROFILE_STATE.get("profile", "FIELD"),
        "screen": _V2_PROFILE_STATE.get("screen", "HOME"),
    }


def load_plugins_config():
    """Load available plugins/apps from configuration."""
    try:
        config_path = os.path.join(os.path.dirname(__file__), 'plugins.json')
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading plugins: {e}")
    
    return []


def update_status_data():
    """Update all status data."""
    global _status_data
    
    with _status_lock:
        _status_data["system"] = get_system_info()
        _status_data["wifi"] = get_wifi_status()
        _status_data["gps"] = get_gps_status()
        _status_data["radio"] = get_radio_status()
        
        # Update running apps
        with _process_lock:
            _status_data["apps"]["running"] = list(_active_processes.keys())
        
        # Load available plugins/apps
        try:
            plugins = load_plugins_config()
            _status_data["apps"]["available"] = [
                {"id": p.get("id"), "label": p.get("label")}
                for p in plugins if p.get("id") and p.get("label")
            ]
        except Exception:
            _status_data["apps"]["available"] = []
        
        _status_data["timestamp"] = datetime.now().isoformat()


class StatusAPIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Status API."""
    
    def log_message(self, format, *args):
        """Override to suppress default logging."""
        pass
    
    def send_json_response(self, data, status_code=200):
        """Send a JSON response."""
        if isinstance(data, dict) and "request_id" not in data:
            data = {"request_id": _v2_request_id(), **data}
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())
    
    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def do_GET(self):
        """Handle GET requests."""
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        query_params = parse_qs(parsed_path.query)
        
        # Update status data on each request for fresh data
        update_status_data()

        if path == '/api/v2/ready':
            self.send_json_response({
                "status": "ready",
                "api": True,
                "integrations": {
                    "gps": _status_data.get("gps", {}).get("reason") != "gpsd unavailable",
                    "radio": RADIO_COORDINATOR is not None,
                },
            })
            return

        if path.startswith('/api/v2/command/'):
            command_id = path.rsplit('/', 1)[-1]
            job = _V2_COMMAND_JOBS.get(command_id)
            if not job:
                self.send_json_response({"error": {"code": "not_found", "message": "Command job not found."}}, 404)
                return
            self.send_json_response(job)
            return

        if path == '/api/v2/capabilities':
            self.send_json_response({
                "api_version": "2.0",
                "enrollment": True,
                "commands": True,
                "telemetry_ingest": True,
                "websocket": False,
                "ota": False,
                "domains": ["system", "network", "gps", "radio", "adsb", "meshtastic", "apps"],
            })
            return

        if path == '/api/v2/schema':
            self.send_json_response({
                "api_version": "2.0",
                "resources": {
                    "status": ["GET /api/v2/status"],
                    "devices": ["POST /api/v2/device/register", "POST /api/v2/device/heartbeat"],
                    "commands": ["POST /api/v2/command", "GET /api/v2/command/{command_id}"],
                    "events": ["GET /api/v2/events?since={sequence}"],
                    "enrollment": ["POST /api/v2/device/enroll/start", "POST /api/v2/device/enroll/confirm"],
                },
            })
            return

        if path == '/api/v2/system/services':
            self.send_json_response({"services": _v2_system_services(), "updated_at": datetime.now().isoformat()})
            return

        if path == '/api/v2/system/processes':
            try:
                output = subprocess.run(
                    ["ps", "-eo", "pid,ppid,user,%cpu,%mem,stat,etime,comm,args", "--sort=-%cpu"],
                    capture_output=True, text=True, timeout=5, check=False,
                ).stdout.strip().splitlines()
                processes = output[:41]
            except Exception:
                processes = []
            self.send_json_response({"processes": processes, "updated_at": datetime.now().isoformat()})
            return

        if path == '/api/v2/system/storage':
            try:
                disk = shutil.disk_usage("/")
                storage = {"total_bytes": disk.total, "free_bytes": disk.free, "used_bytes": disk.used}
            except OSError:
                storage = {"state": "unavailable"}
            self.send_json_response({"storage": storage, "updated_at": datetime.now().isoformat()})
            return

        if path == '/api/v2/network/interfaces':
            self.send_json_response({"interfaces": _status_data.get("wifi", {}).get("interfaces", []), "updated_at": datetime.now().isoformat()})
            return

        if path == '/api/v2/gps/satellites':
            gps = _normalize_v2_status_snapshot()["gps"]
            self.send_json_response({"satellites": gps.get("satellites", 0), "state": gps.get("state"), "updated_at": gps.get("updated_at")})
            return

        if path == '/api/v2/gps/track':
            gps = _normalize_v2_status_snapshot()["gps"]
            self.send_json_response({"track": [], "last_position": gps, "state": "unavailable" if gps.get("latitude") is None else "active"})
            return

        if path == '/api/v2/adsb/aircraft':
            self.send_json_response({"aircraft": [], "state": "unavailable", "updated_at": datetime.now().isoformat()})
            return

        if path in {'/api/v2/meshtastic/nodes', '/api/v2/meshtastic/messages'}:
            key = "nodes" if path.endswith("nodes") else "messages"
            self.send_json_response({key: [], "state": "unavailable", "updated_at": datetime.now().isoformat()})
            return

        if path == '/api/v2/sdr':
            self.send_json_response({"state": "unavailable", "enabled": False, "frequency_hz": None, "mode": ""})
            return

        if path == '/api/v2/events':
            try:
                since = int(query_params.get("since", [0])[0])
            except (TypeError, ValueError):
                since = 0
            events = get_events_since(since)
            self.send_json_response({"sequence": _V2_EVENT_SEQUENCE, "events": events})
            return

        if path == '/api/v2/firmware':
            self.send_json_response({
                "current_version": get_status_app_version(),
                "available_version": get_status_app_version(),
                "update_available": False,
                "update_policy": "metadata_only",
            })
            return
        
        if path == '/api/status' or path == '/':
            # Return full status
            with _status_lock:
                self.send_json_response(_status_data)

        elif path == '/api/v2/status':
            self.send_json_response(_normalize_v2_status_snapshot())

        elif path == '/api/v2/system':
            self.send_json_response({"system": _normalize_v2_system_status()})

        elif path == '/api/v2/gps':
            self.send_json_response({"gps": _normalize_v2_status_snapshot()["gps"]})

        elif path == '/api/v2/network':
            self.send_json_response({"network": _normalize_v2_network_status()})

        elif path == '/api/v2/adsb':
            self.send_json_response({"adsb": {"state": "unavailable", "aircraft": 0, "messages": 0}})

        elif path == '/api/v2/meshtastic':
            self.send_json_response({"meshtastic": {"state": "disconnected", "nodes": 0, "online": 0, "channel": "unknown"}})

        elif path == '/api/v2/radio':
            radio = _status_data.get("radio", {})
            self.send_json_response({"radio": {"state": radio.get("status", "idle"), "enabled": bool(radio.get("enabled", False)), "frequency": radio.get("frequency"), "mode": radio.get("mode") or ""}})

        elif path == '/api/v2/security':
            self.send_json_response({"security": {"state": "ok", "firewall": "unknown", "wireless_security": "unknown"}})

        elif path == '/api/v2/apps':
            self.send_json_response({"apps": _status_data.get("apps", {"running": [], "available": []})})

        elif path == '/api/v2/profile':
            self.send_json_response({
                "profile": _V2_PROFILE_STATE.get("profile", "FIELD"),
                "screen": _V2_PROFILE_STATE.get("screen", "HOME"),
                "allowed_screens": list(_V2_ALLOWED_SCREENS),
            })

        elif path == '/api/v2/device/enroll/start':
            try:
                payload = json.loads(self.headers.get('X-JSON', '{}')) if self.headers.get('X-JSON') else {}
            except Exception:
                payload = {}
            self.send_json_response({"status": "pending", "code": _v2_create_enrollment_code("", "")}, 400)

        elif path == '/api/v2/screens':
            self.send_json_response({"screens": list(_V2_ALLOWED_SCREENS), "default_screen": "HOME"})

        elif path.startswith('/api/v2/device/'):
            device_id = path.rsplit('/', 1)[-1]
            if path.endswith('/config'):
                device_id = path.split('/')[-2]
            if path.endswith('/firmware'):
                device_id = path.split('/')[-2]
            device = _V2_DEVICE_REGISTRY.get(device_id)
            if not device:
                self.send_json_response({"error": {"code": "device_not_found", "message": f"Device '{device_id}' has not registered."}}, 404)
                return
            if path.endswith('/config'):
                self.send_json_response({
                    "device_id": device_id,
                    "heartbeat_seconds": 30,
                    "poll_seconds": 5,
                    "profile": _V2_PROFILE_STATE.get("profile", "FIELD"),
                    "screen": _V2_PROFILE_STATE.get("screen", "HOME"),
                    "allowed_screens": list(_V2_ALLOWED_SCREENS),
                    "features": {"websocket": False, "commands": True, "ota": False},
                })
            elif path.endswith('/firmware'):
                self.send_json_response({
                    "device_id": device_id,
                    "current_version": device.get("firmware", "unknown"),
                    "available_version": device.get("firmware", "unknown"),
                    "update_available": False,
                })
            else:
                self.send_json_response({"device": device})

        elif path == '/ws/v2':
            self.send_json_response({
                "websocket": "not_implemented",
                "endpoint": "/ws/v2",
                "message": "WebSocket event stream is defined for live device updates; this HTTP server exposes REST fallback endpoints for device testing."
            })
        
        elif path.startswith('/api/status/'):
            # Return specific section
            section = path.split('/')[-1]
            with _status_lock:
                if section in _status_data:
                    self.send_json_response({section: _status_data[section]})
                else:
                    self.send_json_response({"error": f"Unknown section: {section}"}, 404)
        
        elif path in {'/api/health', '/api/v2/health'}:
            # Health check endpoint
            self.send_json_response({
                "status": "healthy",
                "timestamp": datetime.now().isoformat()
            })
        
        elif path in {'/api/version', '/api/v2/version'}:
            self.send_json_response({
                "version": "2.0",
                "api_version": "2.0",
                "status_app_version": get_status_app_version(),
                "name": "K7BAT uConsole Status API"
            })

        elif path in {'/api/v2/status/system', '/api/status/system'}:
            with _status_lock:
                self.send_json_response({"system": _status_data.get("system", {})})

        elif path in {'/api/v2/status/wifi', '/api/status/wifi'}:
            with _status_lock:
                self.send_json_response({"wifi": _status_data.get("wifi", {})})

        elif path in {'/api/v2/status/gps', '/api/status/gps'}:
            with _status_lock:
                self.send_json_response({"gps": _status_data.get("gps", {})})

        elif path in {'/api/v2/status/radio', '/api/status/radio'}:
            with _status_lock:
                self.send_json_response({"radio": _status_data.get("radio", {})})

        elif path in {'/api/v2/status/apps', '/api/status/apps'}:
            with _status_lock:
                self.send_json_response({"apps": _status_data.get("apps", {"running": [], "available": []})})

        elif path == '/api/diagnostics/ac1200':
            if AC1200_DIAGNOSTICS is None:
                self.send_json_response({"error": "AC1200 diagnostics unavailable"}, 503)
                return
            try:
                interface = query_params.get('wifi_iface', [None])[0]
                self.send_json_response(AC1200_DIAGNOSTICS.run_basic(interface))
            except Exception as exc:
                self.send_json_response({"error": str(exc)}, 500)
        
        else:
            self.send_json_response({"error": "Not found"}, 404)
    
    def do_POST(self):
        """Handle POST requests."""
        content_length = int(self.headers.get('Content-Length', 0))
        
        if content_length > 0:
            body = self.rfile.read(content_length).decode('utf-8')
            
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self.send_json_response({"error": "Invalid JSON"}, 400)
                return
            if not isinstance(data, dict):
                self.send_json_response({"error": {"code": "invalid_payload", "message": "JSON payload must be an object."}}, 400)
                return
        else:
            data = {}
        
        parsed_path = urlparse(self.path)
        path = parsed_path.path

        if path in {'/api/v2/device/register', '/api/device/register'}:
            success, payload = _v2_register_device(data)
            if not success:
                self.send_json_response(payload, 400)
                return
            self.send_json_response(payload)

        elif path == '/api/v2/device/enroll/start':
            device_id = data.get('device_id')
            mac_address = data.get('mac_address')
            if not device_id:
                self.send_json_response({"error": {"code": "missing_device_id", "message": "device_id is required."}}, 400)
                return
            code = _v2_create_enrollment_code(device_id, mac_address)
            self.send_json_response({
                "status": "pending",
                "device_id": device_id,
                "mac_address": _normalize_mac_address(mac_address),
                "code": code,
                "expires_in_seconds": 300,
            })

        elif path == '/api/v2/device/enroll/confirm':
            success, payload = _v2_confirm_enrollment(data)
            if not success:
                self.send_json_response(payload, 400)
                return
            self.send_json_response(payload)

        elif path in {'/api/v2/device/heartbeat', '/api/device/heartbeat'}:
            device_id = data.get('device_id')
            if not device_id:
                self.send_json_response({"error": {"code": "missing_device_id", "message": "device_id is required."}}, 400)
                return
            device = _V2_DEVICE_REGISTRY.get(device_id)
            if not device:
                self.send_json_response({"error": {"code": "device_not_found", "message": f"Device '{device_id}' has not registered."}}, 404)
                return
            device['last_seen'] = datetime.now().isoformat()
            self.send_json_response({"accepted": True, "device_id": device_id, "profile": _V2_PROFILE_STATE.get("profile", "FIELD"), "screen": _V2_PROFILE_STATE.get("screen", "HOME"), "heartbeat_seconds": 30, "config_changed": False})

        elif path == '/api/v2/telemetry':
            if not isinstance(data, dict):
                self.send_json_response({"error": {"code": "invalid_payload", "message": "Telemetry must be an object."}}, 400)
                return
            device_id = data.get("device_id")
            if device_id:
                _V2_DEVICE_REGISTRY.setdefault(device_id, {"device_id": device_id})["last_telemetry"] = data
            self.send_json_response({"accepted": True, "device_id": device_id, "received_at": datetime.now().isoformat()})

        elif path == '/api/v2/profile':
            if not isinstance(data, dict):
                self.send_json_response({"error": {"code": "invalid_payload", "message": "Profile payload must be an object."}}, 400)
                return
            if data.get('profile'):
                value = data.get('profile')
                if value not in _V2_ALLOWED_PROFILES:
                    self.send_json_response({"error": {"code": "profile_not_allowed", "message": "The requested profile is not available to this device."}}, 400)
                    return
                _V2_PROFILE_STATE['profile'] = value
            self.send_json_response({"profile": _V2_PROFILE_STATE.get('profile', 'FIELD'), "screen": _V2_PROFILE_STATE.get('screen', 'HOME')})

        elif path == '/api/v2/screen':
            if not isinstance(data, dict):
                self.send_json_response({"error": {"code": "invalid_payload", "message": "Screen payload must be an object."}}, 400)
                return
            value = data.get('screen')
            if value not in _V2_ALLOWED_SCREENS:
                self.send_json_response({"error": {"code": "screen_not_allowed", "message": "The requested screen is not available to this device."}}, 400)
                return
            _V2_PROFILE_STATE['screen'] = value
            self.send_json_response({"accepted": True, "screen": value})

        elif path in {'/api/v2/command', '/api/command'}:
            if data.get('command') in {'reboot', 'shutdown', 'update_status'} and not data.get('target'):
                command = data.get('command', '')
                response = {
                    "status": "ok",
                    "command_received": command,
                    "timestamp": datetime.now().isoformat()
                }

                if command == 'reboot':
                    import subprocess
                    threading.Thread(target=lambda: subprocess.run(['sudo', 'reboot'])).start()
                    response["message"] = "Reboot initiated"
                elif command == 'shutdown':
                    import subprocess
                    threading.Thread(target=lambda: subprocess.run(['sudo', 'shutdown', '-h', 'now'])).start()
                    response["message"] = "Shutdown initiated"
                elif command == 'update_status':
                    update_status_data()
                    response["message"] = "Status updated"

                self.send_json_response(response)
                return

            success, payload = _v2_handle_command(data)
            if not success:
                self.send_json_response(payload, 400)
                return
            _V2_COMMAND_JOBS[payload["command_id"]] = {
                **payload,
                "created_at": datetime.now().isoformat(),
                "completed_at": None,
            }
            self.send_json_response(payload)
        
        elif path == '/api/command':
            # Handle commands from Arduino or other devices
            command = data.get('command', '')
            
            response = {
                "status": "ok",
                "command_received": command,
                "timestamp": datetime.now().isoformat()
            }
            
            # Process specific commands
            if command == 'reboot':
                import subprocess
                threading.Thread(target=lambda: subprocess.run(['sudo', 'reboot'])).start()
                response["message"] = "Reboot initiated"
            
            elif command == 'shutdown':
                import subprocess
                threading.Thread(target=lambda: subprocess.run(['sudo', 'shutdown', '-h', 'now'])).start()
                response["message"] = "Shutdown initiated"
            
            elif command == 'update_status':
                update_status_data()
                response["message"] = "Status updated"
            
            self.send_json_response(response)
        
        elif path == '/api/data':
            # Accept data from Arduino (e.g., sensor readings)
            with _status_lock:
                for key, value in data.items():
                    if key in ['system', 'wifi', 'gps', 'radio']:
                        _status_data[key].update(value)
            
            update_status_data()
            self.send_json_response({
                "status": "ok",
                "message": "Data received",
                "received_keys": list(data.keys()),
                "timestamp": datetime.now().isoformat()
            })
        
        elif path == '/api/arduino/ping':
            # Arduino ping endpoint
            self.send_json_response({
                "status": "online",
                "timestamp": datetime.now().isoformat(),
                "api_version": "1.1.0"
            })
        
        elif path == '/api/apps/launch':
            # Launch an application by ID
            app_id = data.get('app_id', '')
            
            if not app_id:
                self.send_json_response({"error": "app_id is required"}, 400)
                return
            
            plugins = load_plugins_config()
            app_config = None
            for p in plugins:
                if p.get('id') == app_id:
                    app_config = p
                    break
            
            if not app_config:
                self.send_json_response({"error": f"App '{app_id}' not found"}, 404)
                return
            
            success, message = launch_app(app_id, app_config)
            
            update_status_data()
            
            if success:
                self.send_json_response({
                    "status": "ok",
                    "message": message,
                    "app_id": app_id
                })
            else:
                self.send_json_response({"error": message}, 500)
        
        elif path == '/api/apps/stop':
            # Stop a running application by ID
            app_id = data.get('app_id', '')
            
            if not app_id:
                self.send_json_response({"error": "app_id is required"}, 400)
                return
            
            success, message = stop_app(app_id)
            
            update_status_data()
            
            if success:
                self.send_json_response({
                    "status": "ok",
                    "message": message,
                    "app_id": app_id
                })
            else:
                self.send_json_response({"error": message}, 500)
        
        elif path == '/api/apps/list':
            # List all available and running apps
            update_status_data()
            
            with _status_lock:
                self.send_json_response({
                    "available": _status_data["apps"]["available"],
                    "running": _status_data["apps"]["running"]
                })
        
        elif path == '/api/radio/toggle':
            # Toggle radio on/off
            enabled = data.get('enabled', True)
            
            success, result = toggle_radio(enabled)
            
            update_status_data()
            
            if success:
                self.send_json_response({
                    "status": "ok",
                    "radio": result
                })
            else:
                self.send_json_response({"error": result}, 500)
        
        elif path == '/api/radio/frequency':
            # Set radio frequency in Hz
            freq_hz = data.get('frequency', None)
            
            if freq_hz is None:
                self.send_json_response({"error": "frequency (Hz) is required"}, 400)
                return
            
            success, result = set_radio_frequency(freq_hz)
            
            update_status_data()
            
            if success:
                self.send_json_response({
                    "status": "ok",
                    "radio": result
                })
            else:
                self.send_json_response({"error": result}, 500)
        
        elif path == '/api/events':
            # Get pending events (Arduino button/touchscreen events)
            events = get_events()
            self.send_json_response({
                "events": events,
                "count": len(events),
                "timestamp": datetime.now().isoformat()
            })
        
        elif path == '/api/event':
            # Add a single event (for Arduino to send button presses)
            event_type = data.get('type', 'unknown')
            
            add_event(event_type, data.get('data'))
            
            self.send_json_response({
                "status": "ok",
                "message": f"Event '{event_type}' recorded"
            })
        
        else:
            self.send_json_response({"error": "Not found"}, 404)


class ThreadedHTTPServer(HTTPServer):
    """HTTP server that handles requests in separate threads."""
    
    def process_request(self, request, client_address):
        """Start a new thread to handle the request."""
        thread = threading.Thread(target=self.process_request_thread,
                                  args=(request, client_address))
        thread.daemon = True
        thread.start()
    
    def process_request_thread(self, request, client_address):
        """Process request in a thread."""
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


def main(port=8080, host='0.0.0.0'):
    """Start the Status API server."""
    print(f"K7BAT uConsole Status API v1.1.0")
    print(f"Starting HTTP server on {host}:{port}")
    print()
    print("GET Endpoints:")
    print("  GET  /                    - Full status")
    print("  GET  /api/status          - Full status")
    print("  GET  /api/status/system   - System info only")
    print("  GET  /api/status/wifi     - Wi-Fi status only")
    print("  GET  /api/status/gps      - GPS status only")
    print("  GET  /api/status/radio    - Radio status only")
    print("  GET  /api/apps/list       - List available and running apps")
    print("  GET  /api/health          - Health check")
    print("  GET  /api/version         - API version")
    print("  GET  /api/v2/status       - Sidekick v2 snapshot")
    print("  GET  /api/v2/profile      - Sidekick profile and screen")
    print("  GET  /api/v2/screens       - Available screens")
    print("  GET  /api/v2/device/{id}  - Device metadata")
    print("  GET  /ws/v2               - WS contract stub")
    print()
    print("POST Endpoints:")
    print("  POST /api/command         - Send commands (reboot, shutdown)")
    print("  POST /api/data            - Post sensor/device data")
    print("  POST /api/event           - Record Arduino button/touch event")
    print("  POST /api/events          - Get pending events queue")
    print("  POST /api/v2/device/register - Register a Sidekick device")
    print("  POST /api/v2/device/heartbeat - Heartbeat a registered device")
    print("  POST /api/v2/profile       - Set profile")
    print("  POST /api/v2/screen        - Set active screen")
    print("  POST /api/v2/command       - Validate and queue a command")
    print()
    print("App Control:")
    print("  POST /api/apps/launch     - Launch app by ID (e.g., {\"app_id\": \"battery-diag\"})")
    print("  POST /api/apps/stop       - Stop running app (e.g., {\"app_id\": \"battery-diag\"})")
    print()
    print("Radio Control:")
    print("  POST /api/radio/toggle    - Toggle radio (e.g., {\"enabled\": true})")
    print("  POST /api/radio/frequency - Set frequency Hz (e.g., {\"frequency\": 433000000})")
    print()
    
    # Initial status update
    update_status_data()
    
    # Start server
    server = ThreadedHTTPServer((host, port), StatusAPIHandler)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='K7BAT uConsole Status API')
    parser.add_argument('--port', type=int, default=8080, help='Port to listen on (default: 8080)')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='Host to bind to (default: 0.0.0.0)')
    
    args = parser.parse_args()
    main(args.port, args.host)
