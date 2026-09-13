#!/usr/bin/env python3
"""Tests for the Sidekick v2 Companion API contract."""

import json
import threading
import time
import unittest
import uuid

import requests

import status_api


class V2StatusApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = status_api.ThreadedHTTPServer(("127.0.0.1", 0), status_api.StatusAPIHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        status_api._V2_PROFILE_STATE = {
            "profile": "FIELD",
            "screen": "HOME",
            "screens": list(status_api._V2_ALLOWED_SCREENS),
        }
        status_api._V2_DEVICE_REGISTRY.clear()

    def test_status_snapshot_includes_v2_contract(self):
        response = requests.get(f"http://127.0.0.1:{self.port}/api/v2/status", timeout=5)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("api_version"), "2.0")
        self.assertIn("system", payload)
        self.assertIn("gps", payload)
        self.assertIn("adsb", payload)
        self.assertIn("meshtastic", payload)
        self.assertIn("network", payload)

    def test_device_registration_accepts_sidekick(self):
        payload = {
            "device_id": "sidekick-35-001",
            "name": "K7BAT Sidekick 3.5",
            "board": "CYD_3248S035R",
            "firmware": "2.0.0",
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
        }

        response = requests.post(
            f"http://127.0.0.1:{self.port}/api/v2/device/register",
            json=payload,
            timeout=5,
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body.get("accepted"))
        self.assertEqual(body.get("profile"), "FIELD")
        self.assertIn("HOME", body.get("allowed_screens", []))

    def test_command_payload_is_validated(self):
        response = requests.post(
            f"http://127.0.0.1:{self.port}/api/v2/command",
            json={"target": "profile", "command": "switch", "value": "MESHTASTIC"},
            timeout=5,
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body.get("accepted"))
        self.assertEqual(body.get("state"), "queued")

    def test_v1_compatibility_routes_exist_under_v2(self):
        health = requests.get(f"http://127.0.0.1:{self.port}/api/v2/health", timeout=5)
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json().get("status"), "healthy")

        version = requests.get(f"http://127.0.0.1:{self.port}/api/v2/version", timeout=5)
        self.assertEqual(version.status_code, 200)
        self.assertIn("K7BAT uConsole Status API", version.json().get("name", ""))

        status = requests.get(f"http://127.0.0.1:{self.port}/api/v2/status/system", timeout=5)
        self.assertEqual(status.status_code, 200)
        self.assertIn("system", status.json())

        command = requests.post(
            f"http://127.0.0.1:{self.port}/api/v2/command",
            json={"command": "update_status"},
            timeout=5,
        )
        self.assertEqual(command.status_code, 200)
        self.assertEqual(command.json().get("status"), "ok")

    def test_sidekick_enrollment_code_issues_api_key(self):
        start = requests.post(
            f"http://127.0.0.1:{self.port}/api/v2/device/enroll/start",
            json={
                "device_id": "sidekick-enroll-001",
                "name": "Sidekick Pairing Test",
                "mac_address": "AA:BB:CC:DD:EE:FF",
                "board": "CYD_3248S035R",
            },
            timeout=5,
        )
        self.assertEqual(start.status_code, 200)
        body = start.json()
        self.assertEqual(body.get("status"), "pending")
        self.assertTrue(body.get("code"))
        self.assertEqual(len(body.get("code", "")), 6)

        confirm = requests.post(
            f"http://127.0.0.1:{self.port}/api/v2/device/enroll/confirm",
            json={
                "device_id": "sidekick-enroll-001",
                "mac_address": "AA:BB:CC:DD:EE:FF",
                "code": body["code"],
            },
            timeout=5,
        )
        self.assertEqual(confirm.status_code, 200)
        self.assertEqual(confirm.json().get("status"), "paired")
        self.assertTrue(confirm.json().get("api_key"))

    def test_paired_device_requires_key_and_supports_config_and_revoke(self):
        suffix = uuid.uuid4().hex[:8]
        device_id = f"sidekick-auth-{suffix}"
        mac_address = f"AA:BB:CC:DD:{suffix[:2]}:{suffix[2:4]}"
        start = requests.post(
            f"http://127.0.0.1:{self.port}/api/v2/device/enroll/start",
            json={"device_id": device_id, "name": "Auth Test", "mac_address": mac_address},
            timeout=5,
        )
        self.assertEqual(start.status_code, 200)
        confirm = requests.post(
            f"http://127.0.0.1:{self.port}/api/v2/device/enroll/confirm",
            json={"device_id": device_id, "mac_address": mac_address, "code": start.json()["code"]},
            timeout=5,
        )
        self.assertEqual(confirm.status_code, 200)
        api_key = confirm.json()["api_key"]
        headers = {"Authorization": f"Bearer {api_key}", "X-Device-ID": device_id}

        unauthenticated = requests.post(
            f"http://127.0.0.1:{self.port}/api/v2/device/heartbeat",
            json={"device_id": device_id},
            timeout=5,
        )
        self.assertEqual(unauthenticated.status_code, 401)

        heartbeat = requests.post(
            f"http://127.0.0.1:{self.port}/api/v2/device/heartbeat",
            headers=headers,
            json={"device_id": device_id},
            timeout=5,
        )
        self.assertEqual(heartbeat.status_code, 200)

        config = requests.get(
            f"http://127.0.0.1:{self.port}/api/v2/device/{device_id}/config",
            headers=headers,
            timeout=5,
        )
        self.assertEqual(config.status_code, 200)
        self.assertNotIn("api_key", config.json())

        revoke = requests.post(
            f"http://127.0.0.1:{self.port}/api/v2/device/{device_id}/revoke",
            headers=headers,
            timeout=5,
        )
        self.assertEqual(revoke.status_code, 200)
        revoked = requests.post(
            f"http://127.0.0.1:{self.port}/api/v2/device/heartbeat",
            headers=headers,
            json={"device_id": device_id},
            timeout=5,
        )
        self.assertEqual(revoked.status_code, 401)

    def test_readiness_and_command_job(self):
        ready = requests.get(f"http://127.0.0.1:{self.port}/api/v2/ready", timeout=5)
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json().get("status"), "ready")

        command = requests.post(
            f"http://127.0.0.1:{self.port}/api/v2/command",
            json={"target": "profile", "command": "switch", "value": "FIELD"},
            timeout=5,
        )
        self.assertEqual(command.status_code, 200)
        command_id = command.json()["command_id"]
        job = requests.get(f"http://127.0.0.1:{self.port}/api/v2/command/{command_id}", timeout=5)
        self.assertEqual(job.status_code, 200)
        self.assertEqual(job.json().get("command_id"), command_id)


if __name__ == "__main__":
    unittest.main()
