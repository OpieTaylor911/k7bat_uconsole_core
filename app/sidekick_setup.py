#!/usr/bin/env python3
"""Enroll a Sidekick and persist the returned uConsole API key."""

import argparse
import json
from pathlib import Path
import urllib.error
import urllib.request


def post_json(url, payload):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def main():
    parser = argparse.ArgumentParser(description="Pair a Sidekick with the K7BAT uConsole.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--mac-address", required=True)
    parser.add_argument("--code", required=True, help="Six-digit code shown on the uConsole")
    parser.add_argument("--config", type=Path, required=True, help="Sidekick config JSON path")
    args = parser.parse_args()

    try:
        response = post_json(
            f"{args.base_url.rstrip('/')}/api/v2/device/enroll/confirm",
            {
                "device_id": args.device_id,
                "mac_address": args.mac_address,
                "code": args.code,
            },
        )
    except (OSError, urllib.error.HTTPError, ValueError) as exc:
        parser.error(f"Enrollment failed: {exc}")

    api_key = response.get("api_key")
    if response.get("status") != "paired" or not api_key:
        parser.error(json.dumps(response))

    args.config.parent.mkdir(parents=True, exist_ok=True)
    config = {}
    if args.config.exists():
        config = json.loads(args.config.read_text(encoding="utf-8"))
    config.update({
        "api_url": args.base_url.rstrip("/"),
        "device_id": args.device_id,
        "mac_address": args.mac_address,
        "api_key": api_key,
    })
    args.config.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    try:
        args.config.chmod(0o600)
    except OSError:
        pass
    print(json.dumps({"status": "paired", "device_id": args.device_id, "config": str(args.config)}))


if __name__ == "__main__":
    main()