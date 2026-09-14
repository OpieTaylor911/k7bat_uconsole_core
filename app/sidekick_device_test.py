#!/usr/bin/env python3
"""Sidekick device smoke test for the v2 Companion API."""

import argparse
import json
import sys
import time
from typing import Any, Dict, Tuple

import requests


def request_json(method: str, url: str, payload: Dict[str, Any] | None = None, timeout: int = 10) -> Tuple[int, Any]:
    try:
        if method.upper() == "GET":
            response = requests.get(url, timeout=timeout)
        elif method.upper() == "POST":
            response = requests.post(url, json=payload or {}, timeout=timeout)
        else:
            raise ValueError(f"Unsupported method: {method}")
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        return response.status_code, body
    except requests.RequestException as exc:
        return 0, {"error": str(exc)}


def print_section(title: str) -> None:
    print(f"\n=== {title} ===")


def run_device_test(base_url: str, device_id: str, name: str, board: str, profile: str = "FIELD") -> int:
    base_url = base_url.rstrip("/")
    status = 0
    steps: Dict[str, Tuple[str, str, Dict[str, Any] | None]] = {
        "register": ("POST", f"{base_url}/api/v2/device/register", {
            "device_id": device_id,
            "name": name,
            "board": board,
            "firmware": "2.0.0-test",
            "display": {
                "native_width": 320,
                "native_height": 480,
                "ui_width": 480,
                "ui_height": 320,
                "touch": True,
            },
            "features": {
                "wifi": True,
                "bluetooth": True,
                "micro_sd": True,
                "ota": True,
                "rgb_led": True,
                "audio": True,
            },
        }),
        "status": ("GET", f"{base_url}/api/v2/status", None),
        "profile": ("GET", f"{base_url}/api/v2/profile", None),
        "screens": ("GET", f"{base_url}/api/v2/screens", None),
        "heartbeat": ("POST", f"{base_url}/api/v2/device/heartbeat", {"device_id": device_id}),
        "command": ("POST", f"{base_url}/api/v2/command", {"target": "profile", "command": "switch", "value": profile}),
    }

    for label, (method, url, payload) in steps.items():
        print_section(f"Step: {label}")
        code, body = request_json(method, url, payload)
        print(f"URL: {url}")
        print(f"HTTP {code}")
        print(json.dumps(body, indent=2)[:1200])
        print()
        if code != 200:
            status = 1
            print(f"FAILED at {label}: HTTP {code}")
            break
        time.sleep(0.2)

    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="Exercise the K7BAT Sidekick v2 Companion API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080", help="Base API URL")
    parser.add_argument("--device-id", default="sidekick-test-001", help="Device identifier to register")
    parser.add_argument("--name", default="K7BAT Sidekick Test", help="Human-friendly device name")
    parser.add_argument("--board", default="CYD_3248S035R", help="Board identifier")
    parser.add_argument("--profile", default="MESHTASTIC", help="Profile to request via command")
    args = parser.parse_args()

    print(f"Testing Sidekick v2 API at {args.base_url}")
    return run_device_test(args.base_url, args.device_id, args.name, args.board, args.profile)


if __name__ == "__main__":
    sys.exit(main())
