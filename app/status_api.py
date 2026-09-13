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
import hashlib
import hmac
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
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
    with _events_lock:
        _events_queue.append({
            "type": event_type,
            "data": data or {},
            "timestamp": datetime.now().isoformat()
        })


def get_events():
    """Get and clear all pending events."""
    with _events_lock:
        events = list(_events_queue)
        _events_queue.clear()
        return events


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

_V2_DB_PATH = Path(os.environ.get(
    "K7BAT_API_DB",
    Path.home() / ".config" / "k7bat-uconsole-status" / "devices.sqlite3",
))
_V2_DB_LOCK = threading.Lock()


def _v2_db_connect():
    _V2_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(_V2_DB_PATH), timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _v2_init_db():
    with _V2_DB_LOCK, _v2_db_connect() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                mac_address TEXT NOT NULL,
                board TEXT NOT NULL,
                firmware TEXT NOT NULL,
                display_json TEXT NOT NULL,
                features_json TEXT NOT NULL,
                api_key_hash TEXT,
                created_at TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE TABLE IF NOT EXISTS enrollments (
                enrollment_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                mac_address TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                expires_at REAL NOT NULL,
                used_at TEXT
            );
            CREATE TABLE IF NOT EXISTS command_jobs (
                command_id TEXT PRIMARY KEY,
                device_id TEXT,
                state TEXT NOT NULL,
                request_json TEXT NOT NULL,
                result_json TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT NOT NULL,
                device_id TEXT,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)


def _v2_hash_secret(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _v2_audit(event, device_id=None, details=None):
    with _V2_DB_LOCK, _v2_db_connect() as connection:
        connection.execute(
            "INSERT INTO audit_log(event, device_id, details_json, created_at) VALUES (?, ?, ?, ?)",
            (event, device_id, json.dumps(details or {}), datetime.now().isoformat()),
        )


def _v2_load_device(device_id):
    with _V2_DB_LOCK, _v2_db_connect() as connection:
        row = connection.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()
    return dict(row) if row else None


def _v2_public_device(row):
    if not row:
        return None
    return {
        "device_id": row["device_id"],
        "name": row["name"],
        "mac_address": row["mac_address"],
        "board": row["board"],
        "firmware": row["firmware"],
        "display": json.loads(row["display_json"]),
        "features": json.loads(row["features_json"]),
        "created_at": row["created_at"],
        "last_seen": row["last_seen"],
        "status": "revoked" if row["revoked_at"] else "active",
    }


def _v2_auth_device(handler, device_id=None):
    authorization = handler.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return False, {"error": {"code": "unauthorized", "message": "A valid Sidekick API key is required."}}
    supplied_hash = _v2_hash_secret(authorization[7:].strip())
    target_id = device_id or handler.headers.get("X-Device-ID")
    if not target_id:
        return False, {"error": {"code": "missing_device_id", "message": "device_id is required for authenticated calls."}}
    row = _v2_load_device(target_id)
    if not row or row["revoked_at"] or not hmac.compare_digest(row["api_key_hash"] or "", supplied_hash):
        _v2_audit("auth.failure", target_id, {})
        return False, {"error": {"code": "unauthorized", "message": "A valid Sidekick API key is required."}}
    return True, row


def _v2_db_has_devices():
    with _V2_DB_LOCK, _v2_db_connect() as connection:
        return connection.execute("SELECT 1 FROM devices LIMIT 1").fetchone() is not None


def _v2_create_command_job(data, result, device_id=None):
    command_id = result.get("command_id")
    if not command_id:
        return result
    now = datetime.now().isoformat()
    with _V2_DB_LOCK, _v2_db_connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO command_jobs(command_id, device_id, state, request_json, result_json, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (command_id, device_id, result.get("state", "queued"), json.dumps(data),
             json.dumps(result), now, now if result.get("state") == "completed" else None),
        )
    _v2_audit("command.queued", device_id, {"command_id": command_id})
    return result


def _v2_get_job(command_id):
    with _V2_DB_LOCK, _v2_db_connect() as connection:
        row = connection.execute("SELECT * FROM command_jobs WHERE command_id = ?", (command_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["request"] = json.loads(result.pop("request_json"))
    result["result"] = json.loads(result.pop("result_json")) if result.get("result_json") else None
    return result


_v2_init_db()


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
        "system": _normalize_v2_system_status(),
        "gps": {
            "state": gps.get("status", "no_fix"),
            "latitude": gps.get("latitude"),
            "longitude": gps.get("longitude"),
            "altitude_m": gps.get("altitude"),
            "grid": "UNKNOWN",
            "satellites": gps.get("satellites", 0),
            "speed_kph": gps.get("speed"),
            "device": gps.get("device"),
            "reason": gps.get("reason", ""),
        },
        "adsb": {
            "state": "unavailable",
            "aircraft": 0,
            "messages": 0,
        },
        "meshtastic": {
            "state": "disconnected",
            "nodes": 0,
            "online": 0,
            "channel": "unknown",
        },
        "radio": {
            "state": radio.get("status", "idle"),
            "enabled": bool(radio.get("enabled", False)),
            "frequency": radio.get("frequency"),
            "mode": radio.get("mode") or "",
        },
        "network": _normalize_v2_network_status(),
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
    now = datetime.now().isoformat()
    with _V2_DB_LOCK, _v2_db_connect() as connection:
        connection.execute(
            """INSERT INTO devices
            (device_id, name, mac_address, board, firmware, display_json, features_json, created_at, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
            name=excluded.name, mac_address=excluded.mac_address, board=excluded.board,
            firmware=excluded.firmware, display_json=excluded.display_json,
            features_json=excluded.features_json, last_seen=excluded.last_seen""",
            (device_id, device["name"], _normalize_mac_address(data.get("mac_address")),
             device["board"], device["firmware"], json.dumps(device["display"]),
             json.dumps(device["features"]), now, now),
        )
    _v2_audit("device.registered", device_id, {"board": device["board"]})

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

    if target not in {"profile", "screen", "system"}:
        return False, {"error": {"code": "unsupported_target", "message": f"Unsupported target: {target}"}}
    if not command:
        return False, {"error": {"code": "missing_command", "message": "command is required."}}

    if target == "profile" and command == "switch":
        if value not in _V2_ALLOWED_PROFILES:
            return False, {"error": {"code": "profile_not_allowed", "message": "The requested profile is not available to this device."}}
        _V2_PROFILE_STATE["profile"] = value
        command_id = f"cmd-{uuid.uuid4().hex}"
        response = {
            "accepted": True,
            "command_id": command_id,
            "state": "queued",
            "profile": value,
        }
        return True, response

    if target == "screen" and command == "switch":
        if value not in _V2_ALLOWED_SCREENS:
            return False, {"error": {"code": "screen_not_allowed", "message": "The requested screen is not available to this device."}}
        _V2_PROFILE_STATE["screen"] = value
        command_id = f"cmd-{uuid.uuid4().hex}"
        return True, {
            "accepted": True,
            "command_id": command_id,
            "state": "queued",
            "screen": value,
        }

    return True, {
        "accepted": True,
        "command_id": f"cmd-{uuid.uuid4().hex}",
        "state": "queued",
        "target": target,
        "command": command,
        "value": value,
    }


def _normalize_mac_address(mac):
    if not mac:
        return ""
    return mac.strip().lower().replace('-', ':').replace(' ', ':')


def _v2_create_enrollment_code(device_id, mac_address, name=None):
    key = _normalize_mac_address(mac_address) or device_id
    code = f"{secrets.randbelow(1000000):06d}"
    enrollment_id = uuid.uuid4().hex
    expires_at = time.time() + 300
    _V2_PAIRING_REGISTRY[key] = {
        "enrollment_id": enrollment_id,
        "device_id": device_id,
        "name": name or device_id,
        "mac_address": _normalize_mac_address(mac_address),
        "code": code,
        "expires_at": expires_at,
        "status": "pending",
    }
    with _V2_DB_LOCK, _v2_db_connect() as connection:
        connection.execute(
            "INSERT INTO enrollments(enrollment_id, device_id, mac_address, code_hash, expires_at) VALUES (?, ?, ?, ?, ?)",
            (enrollment_id, device_id, _normalize_mac_address(mac_address), _v2_hash_secret(code), expires_at),
        )
    _v2_audit("enrollment.started", device_id, {"enrollment_id": enrollment_id})
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
        with _V2_DB_LOCK, _v2_db_connect() as connection:
            row = connection.execute(
                "SELECT * FROM enrollments WHERE device_id = ? AND mac_address = ? ORDER BY expires_at DESC LIMIT 1",
                (device_id, mac_address),
            ).fetchone()
        if not row:
            return False, {"error": {"code": "no_pending_pairing", "message": "No pending enrollment found for this device."}}
        pending = dict(row)
        pending.update({
            "code": code,
            "status": "paired" if row["used_at"] else "pending",
            "expires_at": row["expires_at"],
        })
    if pending["device_id"] != device_id:
        return False, {"error": {"code": "device_mismatch", "message": "Enrollment code does not match device_id."}}
    if pending.get("code") != code and not hmac.compare_digest(pending.get("code_hash", ""), _v2_hash_secret(code)):
        return False, {"error": {"code": "invalid_code", "message": "Enrollment code is invalid or expired."}}
    if pending["status"] != "pending" or time.time() >= pending["expires_at"]:
        return False, {"error": {"code": "invalid_code", "message": "Enrollment code is invalid or expired."}}

    api_key = f"k7bat_{secrets.token_urlsafe(32)}"
    issued_at = datetime.now().isoformat()
    _V2_API_KEYS[device_id] = {
        "api_key": api_key,
        "mac_address": mac_address,
        "device_id": device_id,
        "issued_at": issued_at,
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
    with _V2_DB_LOCK, _v2_db_connect() as connection:
        connection.execute(
            "UPDATE enrollments SET used_at = ? WHERE enrollment_id = ?",
            (issued_at, pending.get("enrollment_id") or pending["enrollment_id"]),
        )
        connection.execute(
            """INSERT INTO devices
            (device_id, name, mac_address, board, firmware, display_json, features_json,
             api_key_hash, created_at, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET api_key_hash=excluded.api_key_hash,
            mac_address=excluded.mac_address, last_seen=excluded.last_seen, revoked_at=NULL""",
            (device_id, pending.get("name") or device_id, mac_address, "sidekick", "unknown",
             "{}", "{}", _v2_hash_secret(api_key), issued_at, issued_at),
        )
    _v2_audit("enrollment.confirmed", device_id, {"enrollment_id": pending["enrollment_id"]})
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
                "database": _V2_DB_PATH.exists(),
                "integrations": {
                    "gps": _status_data.get("gps", {}).get("reason") != "gpsd unavailable",
                    "radio": RADIO_COORDINATOR is not None,
                },
            })
            return

        if path == '/api/v2/devices':
            with _V2_DB_LOCK, _v2_db_connect() as connection:
                rows = connection.execute("SELECT * FROM devices ORDER BY created_at DESC").fetchall()
            self.send_json_response({"devices": [_v2_public_device(row) for row in rows]})
            return

        if path.startswith('/api/v2/command/'):
            command_id = path.rsplit('/', 1)[-1]
            job = _v2_get_job(command_id)
            if not job:
                self.send_json_response({"error": {"code": "command_not_found", "message": "Command job not found."}}, 404)
                return
            if job.get("device_id"):
                authenticated, auth_result = _v2_auth_device(self, job["device_id"])
                if not authenticated:
                    self.send_json_response(auth_result, 401)
                    return
            self.send_json_response(job)
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

        elif path == '/api/v2/events':
            self.send_json_response({"events": get_events(), "count": 0})

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
            device = _v2_load_device(device_id) or _V2_DEVICE_REGISTRY.get(device_id)
            if not device:
                self.send_json_response({"error": {"code": "device_not_found", "message": f"Device '{device_id}' has not registered."}}, 404)
                return
            authenticated, auth_result = _v2_auth_device(self, device_id)
            if not authenticated:
                self.send_json_response(auth_result, 401)
                return
            if path.endswith('/config'):
                self.send_json_response({
                    "device_id": device_id,
                    "api_url": f"http://{self.server.server_address[0]}:{self.server.server_address[1]}",
                    "heartbeat_seconds": 30,
                    "profile": _V2_PROFILE_STATE.get("profile", "FIELD"),
                    "screen": _V2_PROFILE_STATE.get("screen", "HOME"),
                    "allowed_screens": list(_V2_ALLOWED_SCREENS),
                    "features": {"websocket": False, "commands": True, "ota": False},
                })
            else:
                self.send_json_response({"device": _v2_public_device(device) if isinstance(device, dict) and "device_id" in device else device})

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
                "version": "1.0.0",
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
        else:
            data = {}
        
        parsed_path = urlparse(self.path)
        path = parsed_path.path

        if path.startswith('/api/v2/device/') and path.endswith('/revoke'):
            device_id = path.split('/')[-2]
            authenticated, auth_result = _v2_auth_device(self, device_id)
            if not authenticated:
                self.send_json_response(auth_result, 401)
                return
            with _V2_DB_LOCK, _v2_db_connect() as connection:
                connection.execute("UPDATE devices SET revoked_at = ? WHERE device_id = ?", (datetime.now().isoformat(), device_id))
            _v2_audit("api_key.revoked", device_id)
            self.send_json_response({"status": "revoked", "device_id": device_id})
            return

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
            code = _v2_create_enrollment_code(device_id, mac_address, data.get("name"))
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
            persisted = _v2_load_device(device_id)
            if persisted and persisted.get("api_key_hash"):
                authenticated, auth_result = _v2_auth_device(self, device_id)
                if not authenticated:
                    self.send_json_response(auth_result, 401)
                    return
            device['last_seen'] = datetime.now().isoformat()
            with _V2_DB_LOCK, _v2_db_connect() as connection:
                connection.execute("UPDATE devices SET last_seen = ? WHERE device_id = ?", (device['last_seen'], device_id))
            self.send_json_response({"accepted": True, "device_id": device_id, "profile": _V2_PROFILE_STATE.get("profile", "FIELD"), "screen": _V2_PROFILE_STATE.get("screen", "HOME")})

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

            device_id = data.get("device_id")
            if device_id:
                authenticated, auth_result = _v2_auth_device(self, device_id)
                if not authenticated:
                    self.send_json_response(auth_result, 401)
                    return
            success, payload = _v2_handle_command(data)
            if not success:
                self.send_json_response(payload, 400)
                return
            payload = _v2_create_command_job(data, payload, device_id)
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
