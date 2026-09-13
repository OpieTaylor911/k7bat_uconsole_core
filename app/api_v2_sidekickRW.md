# K7BAT Companion Architecture

## Overview

This project defines a paired architecture:

- The K7BAT uConsole is the authoritative system of record.
- Sidekick devices are companion terminals that render normalized data and issue approved commands.
- The uConsole exposes a Companion API to Sidekick devices.
- High-frequency data moves over WebSocket events, while REST API endpoints are used for bootstrap, configuration, commands, and fallback polling.

The key design principle is simple: Sidekick does not need to understand app-specific protocols. The uConsole normalizes state from Linux services and exposes a common API contract to the display hardware.

---

## 1. System Role

The uConsole owns:

- Linux applications and services
- GPS and location state
- ADS-B and aircraft state
- Meshtastic messages and node state
- APRS, packet, radio, SDR, and satellite integrations
- Network and security tools
- Profiles and application lifecycle
- Device registry and authorization
- Firmware metadata and OTA policy

Sidekick devices are responsible for:

- Displaying device-friendly status and telemetry
- Touch navigation and profile selection
- Approved commands
- Alerts and message banners
- Device-specific dashboards
- Offline or degraded-state handling

The uConsole remains authoritative for all shared state.

---

## 2. Architecture

```text
Linux application or service
        |
        v
Integration adapter
        |
        v
Normalized Companion data model
        |
        +--> REST API for bootstrap, queries, commands, fallback
        |
        +--> WebSocket event broker for live updates
        |
        v
Sidekick devices
```

Adapter code should be separated from transport code. Adapters may talk to gpsd, readsb, tar1090, Meshtastic, Hamlib, NetworkManager, systemd, or other Linux services, but the API layer must consume normalized domain data.

---

## 3. API Versioning and Migration Strategy

Use the versioned base path:

```text
/api/v2/
```

During the migration window, the v2 API is the superset and compatibility surface. It must include all legacy v1 endpoints and their behavior while adding the Sidekick companion contract. This allows older clients to continue working while new Sidekick devices adopt the v2 model.

Compatibility policy:

- `/api/v1` is not required; the existing `/api` namespace remains authoritative for older devices.
- `/api/v2` must include all v1 routes as compatibility aliases until all devices are migrated.
- New device work should target `/api/v2` and the WebSocket `/ws/v2` surface.
- Legacy behavior must not silently change; compatibility aliases should preserve status payloads and command semantics.
- After migration is complete, the v1 routes can be deprecated and removed in a later release.

### Companion device pairing model

For first-time Sidekick onboarding, the uConsole exposes a short-lived enrollment flow before any API key is issued.

This is the current live behavior implemented in the API server:

```text
POST /api/v2/device/enroll/start
POST /api/v2/device/enroll/confirm
```

Flow:

1. uConsole creates a pending pairing record keyed by MAC address and/or device_id.
2. A 6-digit code is returned to the UI or to the device requesting enrollment.
3. The Sidekick presents that code to the user.
4. The Sidekick submits the code back with its `device_id` and `mac_address`.
5. The uConsole validates and issues a first-use API key.

This gives us a safe migration path while the project remains in testing, without requiring a permanent DB-backed auth layer yet.

---

## 4. REST API Contract

### v2 Companion read endpoints

```text
GET /api/v2/status
GET /api/v2/system
GET /api/v2/gps
GET /api/v2/adsb
GET /api/v2/meshtastic
GET /api/v2/radio
GET /api/v2/network
GET /api/v2/security
GET /api/v2/apps
GET /api/v2/events
GET /api/v2/profile
GET /api/v2/screens
GET /api/v2/device/{device_id}
```

### v2 Companion write endpoints

```text
POST /api/v2/device/register
POST /api/v2/device/heartbeat
POST /api/v2/device/enroll/start
POST /api/v2/device/enroll/confirm
POST /api/v2/profile
POST /api/v2/screen
POST /api/v2/command
```

### Sidekick enrollment request/response

Start enrollment:

```json
{
  "device_id": "sidekick-enroll-001",
  "name": "Sidekick Pairing Test",
  "mac_address": "AA:BB:CC:DD:EE:FF",
  "board": "CYD_3248S035R"
}
```

Server response:

```json
{
  "status": "pending",
  "device_id": "sidekick-enroll-001",
  "mac_address": "aa:bb:cc:dd:ee:ff",
  "code": "482913",
  "expires_in_seconds": 300
}
```

Confirm enrollment:

```json
{
  "device_id": "sidekick-enroll-001",
  "mac_address": "AA:BB:CC:DD:EE:FF",
  "code": "482913"
}
```

Server response:

```json
{
  "status": "paired",
  "device_id": "sidekick-enroll-001",
  "api_key": "k7bat_sidekick_enroll_001_1720000000000",
  "profile": "FIELD",
  "screen": "HOME"
}
```

The `api_key` should be stored locally on the Sidekick for subsequent authenticated calls against protected endpoints.

The reference setup command in `app/sidekick_setup.py` performs the confirm request and writes `api_url`, `device_id`, `mac_address`, and `api_key` to the Sidekick config JSON. The uConsole Settings action displays the pair code; the Sidekick setup tool never needs to display or generate a second code.

Example Sidekick setup command:

```bash
python3 sidekick_setup.py \\
  --base-url http://uconsole:8080 \\
  --device-id sidekick-001 \\
  --mac-address AA:BB:CC:DD:EE:FF \\
  --code 583165 \\
  --config /etc/k7bat-sidekick/config.json
```

### Legacy compatibility aliases kept during migration

```text
GET /api/health
GET /api/version
GET /api/status
GET /api/status/system
GET /api/status/wifi
GET /api/status/gps
GET /api/status/radio
GET /api/apps/list
POST /api/command
POST /api/data
POST /api/event
POST /api/events
POST /api/apps/launch
POST /api/apps/stop
POST /api/radio/toggle
POST /api/radio/frequency
POST /api/arduino/ping

GET /api/v2/health
GET /api/v2/version
GET /api/v2/status/system
GET /api/v2/status/wifi
GET /api/v2/status/gps
GET /api/v2/status/radio
GET /api/v2/status/apps
POST /api/v2/command
```

### REST usage rules

Use REST for:

- Device registration
- Initial connection bootstrap
- Full snapshot reads
- Device info and screen assignments
- Profile and screen reads
- Commands
- Heartbeats
- Recovery after reconnect
- Reduced-rate polling fallback

Use WebSocket for high-frequency or event-oriented updates such as:

- `gps.update`
- `adsb.aircraft_count`
- `meshtastic.message`
- `profile.changed`
- `system.battery`
- `system.temperature`

---

## 5. Compatibility Matrix

The v2 API is intentionally a superset of the v1 API during the migration period.

| Capability | v1 route | v2 route | Status |
| --- | --- | --- | --- |
| Health check | `GET /api/health` | `GET /api/v2/health` | Supported |
| API version | `GET /api/version` | `GET /api/v2/version` | Supported |
| Full status | `GET /api/status` | `GET /api/v2/status` | Supported |
| System status | `GET /api/status/system` | `GET /api/v2/status/system` | Supported |
| Wi-Fi status | `GET /api/status/wifi` | `GET /api/v2/status/wifi` | Supported |
| GPS status | `GET /api/status/gps` | `GET /api/v2/status/gps` | Supported |
| Radio status | `GET /api/status/radio` | `GET /api/v2/status/radio` | Supported |
| App list | `GET /api/apps/list` | `GET /api/v2/apps` | Supported |
| Command dispatch | `POST /api/command` | `POST /api/v2/command` | Supported |
| Device registration | n/a | `POST /api/v2/device/register` | Supported |
| Device heartbeat | n/a | `POST /api/v2/device/heartbeat` | Supported |
| Pairing start | n/a | `POST /api/v2/device/enroll/start` | Supported |
| Pairing confirm | n/a | `POST /api/v2/device/enroll/confirm` | Supported |
| Profile control | n/a | `POST /api/v2/profile` | Supported |
| Screen control | n/a | `POST /api/v2/screen` | Supported |
| Event stream | n/a | `GET /ws/v2` | Planned |

This migration model means Sidekick clients can start on v2 immediately while old Arduino or embedded devices continue to call the v1 routes without breakage.

---

## 6. Poll vs. Push Split

Not every endpoint should be polled at a fixed interval. Use a hybrid model.

### Poll (REST)

```text
/api/v2/device/{device_id}
/api/v2/screens
/api/v2/profile        (GET)
/api/v2/device/register
/api/v2/device/heartbeat
/api/v2/command        (POST)
```

### Push (WebSocket)

```text
gps.update
adsb.aircraft_count
meshtastic.message
system.battery
system.temperature
profile.changed
```

A Sidekick should:

1. Boot or reconnect.
2. Fetch `/api/v2/status` once for a full snapshot.
3. Connect to `/ws/v2`.
4. Receive live event deltas for high-frequency fields.
5. Fall back to reduced-rate polling if the WebSocket is unavailable.

Heartbeat still remains on REST so a dead socket can be detected independently of WebSocket ping/pong behavior.

---

## 7. WebSocket Event Model

WebSocket endpoint:

```text
/ws/v2
```

Event envelope:

```json
{
  "event": "gps.update",
  "timestamp": "2026-09-09T12:00:00Z",
  "sequence": 1042,
  "data": {
    "latitude": 45.5231,
    "longitude": -122.6765,
    "altitude_m": 81,
    "satellites": 11
  }
}
```

Rules:

- `event` is required.
- `data` is required.
- `timestamp` should be UTC.
- `sequence` should increase for events from the same source or broker.
- Event names use lowercase dot notation.
- Event payloads should remain compact and focused.
- Do not send secrets or bearer tokens in event payloads.

Examples:

```json
{
  "event": "adsb.aircraft_count",
  "data": {
    "count": 38
  }
}
```

```json
{
  "event": "meshtastic.message",
  "data": {
    "from": "K7XYZ",
    "text": "At checkpoint"
  }
}
```

```json
{
  "event": "profile.changed",
  "data": {
    "profile": "MESHTASTIC"
  }
}
```

---

## 8. Example Aggregate Status

```json
{
  "api_version": "2.0",
  "server": "clockworkpi",
  "profile": "FIELD",
  "system": {
    "state": "ok",
    "battery_percent": 82,
    "temperature_c": 47.2,
    "uptime_seconds": 42180
  },
  "gps": {
    "state": "fix",
    "latitude": 45.5231,
    "longitude": -122.6765,
    "altitude_m": 81,
    "grid": "CN85",
    "satellites": 11,
    "speed_kph": 0.0
  },
  "adsb": {
    "state": "running",
    "aircraft": 37,
    "messages": 128943
  },
  "meshtastic": {
    "state": "connected",
    "nodes": 17,
    "online": 9,
    "channel": "LongFast"
  }
}
```

---

## 9. Device Registration

The first-time connection flow for a device is intentionally split into register + enroll:

- `POST /api/v2/device/register` registers the device identity and capabilities.
- `POST /api/v2/device/enroll/start` begins the pairing process and returns a one-time 6-digit code.
- `POST /api/v2/device/enroll/confirm` verifies the code and returns the first API key.

This keeps the v2 API usable for early device testing while still allowing a future stricter device auth model.

Example request:

```json
{
  "device_id": "sidekick-35-001",
  "name": "K7BAT Sidekick 3.5",
  "board": "CYD_3248S035R",
  "firmware": "2.0.0",
  "display": {
    "native_width": 320,
    "native_height": 480,
    "ui_width": 480,
    "ui_height": 320,
    "touch": true
  },
  "features": {
    "wifi": true,
    "bluetooth": true,
    "micro_sd": true,
    "ota": true,
    "rgb_led": true,
    "audio": true
  }
}
```

Example response:

```json
{
  "accepted": true,
  "profile": "FIELD",
  "screen": "HOME",
  "allowed_screens": [
    "HOME",
    "SYSTEM",
    "GPS",
    "ADSB",
    "MESHTASTIC"
  ],
  "heartbeat_seconds": 30
}
```

---

## 10. Sidekick auth and persisted tokens

The current model is intentionally simple:

- The uConsole tracks pairing state for each device using its MAC and device_id.
- The pairing code is valid for a short window (currently 300 seconds).
- The API key returned after confirmation should be stored by the Sidekick and reused for follow-up calls.
- This is ready for testing and device onboarding, but it is still intentionally lightweight until the full persistent registry is moved into a real storage layer.

Planned hardening:

- persist device registry to a file or database instead of in-memory state
- enforce API-key checks on protected device endpoints
- revoke stale or invalid tokens
- add per-device permissions and profile restrictions

---

## 11. Profiles

Profiles define operating context.

At first connection, a Sidekick registers itself.

Example request:

```json
{
  "device_id": "sidekick-35-001",
  "name": "K7BAT Sidekick 3.5",
  "board": "CYD_3248S035R",
  "firmware": "2.0.0",
  "display": {
    "native_width": 320,
    "native_height": 480,
    "ui_width": 480,
    "ui_height": 320,
    "touch": true
  },
  "features": {
    "wifi": true,
    "bluetooth": true,
    "micro_sd": true,
    "ota": true,
    "rgb_led": true,
    "audio": true
  }
}
```

Example response:

```json
{
  "accepted": true,
  "profile": "FIELD",
  "screen": "HOME",
  "allowed_screens": [
    "HOME",
    "SYSTEM",
    "GPS",
    "ADSB",
    "MESHTASTIC"
  ],
  "heartbeat_seconds": 30
}
```

---

## 10. Profiles

Profiles define operating context.

Initial examples:

```text
GENERAL
FIELD
HAM
HF_VOICE
HF_DIGITAL
APRS
PACKET
WINLINK
MESHTASTIC
SATELLITE
SDR
ADSB
EMCOMM
SECURITY
LOW_POWER
```

Profiles can control:

- Enabled services
- Available screens
- Default screen
- Telemetry frequency
- Theme and context
- Allowed commands
- Device assignments

Profile changes must be validated by the uConsole before being broadcast to clients.

---

## 12. Commands

Commands must be explicit, validated, authorized, and logged.

Example request:

```json
{
  "target": "profile",
  "command": "switch",
  "value": "MESHTASTIC"
}
```

Example response:

```json
{
  "accepted": true,
  "command_id": "cmd-123",
  "state": "queued"
}
```

The API should reject commands that are not allowed for the active profile or for the authenticated device.

---

## 13. Error Handling

Use structured JSON errors:

```json
{
  "error": {
    "code": "profile_not_allowed",
    "message": "The requested profile is not available to this device.",
    "request_id": "req-123"
  }
}
```

Do not return stack traces, internal paths, credentials, or raw service data to Sidekick clients.

---

## 14. Reliability and Fallback

The WebSocket is a performance optimization for live state, not the only path to fresh data.

The fallback rules should be:

1. If WebSocket is healthy, use it for high-frequency updates.
2. If WebSocket is unavailable, fall back to reduced-rate REST polling.
3. If a device reconnects, fetch the snapshot endpoint first.
4. If a source integration is stale, mark the data as stale rather than selling it as current.

The goal is to keep the Sidekick useful even when local network or service connectivity is degraded.

---

## 15. Current implementation status

The reference Python API now includes:

- SQLite persistence at `~/.config/k7bat-uconsole-status/devices.sqlite3` by default.
- Cryptographically random six-digit enrollment codes.
- Five-minute enrollment expiry and single-use confirmation.
- Hashed API-key storage. Raw keys are returned only from enrollment confirmation.
- Bearer authentication for device-scoped reads, config, heartbeat, commands, and revocation.
- Device listing, config, revoke, readiness, command-job, and audit storage primitives.

The API key is sent by the Sidekick as:

```http
Authorization: Bearer k7bat_...
X-Device-ID: sidekick-001
```

Do not send API keys in query strings, screen data, WebSocket event payloads, logs, or telemetry.

### Device management routes

```text
GET  /api/v2/ready
GET  /api/v2/devices
GET  /api/v2/device/{device_id}
GET  /api/v2/device/{device_id}/config
POST /api/v2/device/{device_id}/revoke
GET  /api/v2/command/{command_id}
```

The current command implementation remains intentionally conservative: it records a command job and returns a command ID. A future worker can transition jobs from `queued` to `running` to `completed` without changing the Sidekick protocol.

---

## 16. LVGL and PlatformIO implementation guide

This section is the implementation contract for a Sidekick built with ESP32, LVGL, and PlatformIO. Keep transport, persisted credentials, and UI state as separate modules.

### Recommended PlatformIO layout

```text
sidekick/
  platformio.ini
  include/
    app_config.h
  src/
    main.cpp
    api_client.cpp
    api_client.h
    credential_store.cpp
    credential_store.h
    pairing_screen.cpp
    pairing_screen.h
    dashboard_screen.cpp
    dashboard_screen.h
    telemetry_model.cpp
    telemetry_model.h
```

Suggested `platformio.ini` baseline:

```ini
[env:sidekick]
platform = espressif32
board = esp32-s3-devkitc-1
framework = arduino
monitor_speed = 115200
lib_deps =
  lvgl/lvgl@^9.2.0
  bblanchon/ArduinoJson@^7.0.4
  https://github.com/adafruit/Adafruit_TinyUSB_Arduino.git
build_flags =
  -D LV_CONF_INCLUDE_SIMPLE
  -D LV_LVGL_H_INCLUDE_SIMPLE
```

Use the actual Sidekick board definition and display/touch libraries for the selected hardware. The API client should not depend on LVGL; this keeps networking testable without a display.

### Persistent credential storage

Store the following in NVS/Preferences or a protected LittleFS file:

```json
{
  "api_url": "http://uconsole:8080",
  "device_id": "sidekick-001",
  "mac_address": "AA:BB:CC:DD:EE:FF",
  "api_key": "k7bat_..."
}
```

Recommended Arduino storage wrapper:

```cpp
Preferences preferences;

bool loadCredentials(Credentials& credentials) {
  preferences.begin("k7bat", true);
  credentials.apiUrl = preferences.getString("api_url", "");
  credentials.deviceId = preferences.getString("device_id", "");
  credentials.apiKey = preferences.getString("api_key", "");
  preferences.end();
  return credentials.apiUrl.length() > 0 && credentials.deviceId.length() > 0 && credentials.apiKey.length() > 0;
}

void saveCredentials(const Credentials& credentials) {
  preferences.begin("k7bat", false);
  preferences.putString("api_url", credentials.apiUrl);
  preferences.putString("device_id", credentials.deviceId);
  preferences.putString("api_key", credentials.apiKey);
  preferences.end();
}
```

Never render the full API key on the LVGL display after pairing. Show only `Paired` and the last four characters if a diagnostic screen needs confirmation.

### Pairing state machine

```text
BOOT
  |-- credentials present --> REGISTER --> SNAPSHOT --> DASHBOARD
  |
  `-- credentials absent ---> PAIRING_SCREEN
                                |
                                `-- user enters code from uConsole
                                      |
                                      `-- CONFIRM --> SAVE_KEY --> REGISTER
```

The Sidekick setup tool or an on-device LVGL pairing screen submits:

```http
POST /api/v2/device/enroll/confirm
Content-Type: application/json
```

```json
{
  "device_id": "sidekick-001",
  "mac_address": "AA:BB:CC:DD:EE:FF",
  "code": "583165"
}
```

On HTTP 200 with `status == "paired"`, save `api_key` immediately, then call register. On HTTP 400, show the structured error and let the user retry. Do not retry the code indefinitely; expire the local form after five minutes.

### HTTP client behavior

Every authenticated request should add:

```cpp
http.addHeader("Authorization", String("Bearer ") + credentials.apiKey);
http.addHeader("X-Device-ID", credentials.deviceId);
http.addHeader("Accept", "application/json");
```

Recommended request timeouts:

```text
pairing confirmation: 10 seconds
register:             10 seconds
status snapshot:       5 seconds
heartbeat:             3 seconds
command:               5 seconds
```

Treat these responses as state transitions:

| HTTP | Meaning | LVGL behavior |
| --- | --- | --- |
| 200 | Success | Update model and screen |
| 400 | Invalid request/code | Show recoverable error |
| 401 | Key missing/invalid/revoked | Clear key and return to pairing |
| 404 | Device/job not found | Refresh registration/state |
| 429 | Rate limited | Back off and show delayed status |
| 500/503 | uConsole unavailable | Keep cached screen and retry later |

### LVGL screen responsibilities

Keep screens thin. `api_client.cpp` owns HTTP and JSON; `telemetry_model.cpp` owns normalized values; LVGL callbacks only dispatch events and update widgets.

Pairing screen controls:

- device ID field, prefilled from firmware identity
- read-only MAC address
- six-digit code field
- Pair button
- connection/error label
- timeout/reset action

Dashboard controls:

- connection indicator
- profile and active screen
- battery and temperature cards
- GPS fix/satellite card
- radio/network cards
- last update age
- command result banner

Use a timer for refresh, not a blocking HTTP call inside an LVGL event callback:

```cpp
lv_timer_create([](lv_timer_t* timer) {
  auto* client = static_cast<ApiClient*>(timer->user_data);
  client->pollSnapshotInWorker();
}, 5000, &apiClient);
```

Use a FreeRTOS task or the board's asynchronous HTTP facility for network calls. Marshal only compact result messages back to the LVGL thread.

### Boot and fallback behavior

1. Connect to Wi-Fi.
2. If no credentials exist, show pairing screen.
3. If credentials exist, call register and then `/api/v2/status`.
4. Start a 30-second heartbeat timer.
5. Poll status every 5 seconds until `/ws/v2` is implemented for the target deployment.
6. On 401, erase only the API key and return to pairing.
7. On timeout, keep the last good snapshot and mark telemetry stale.
8. Never reboot repeatedly because the API is unavailable.

### WebSocket migration path

Start with REST polling. Add WebSocket only after the LVGL model handles snapshot and stale-state transitions correctly. The WebSocket task should feed the same `TelemetryModel` update functions used by REST; it must not update LVGL widgets directly.

### PlatformIO test checklist

Before connecting real hardware:

1. Test `ApiClient` with a local HTTP fixture for 200, 400, 401, timeout, and malformed JSON.
2. Verify credentials survive power loss and are not printed in serial logs.
3. Verify a revoked key returns to the pairing screen.
4. Verify stale telemetry is visibly marked after the polling timeout.
5. Verify the LVGL UI remains responsive during network failure.
6. Test the exact board display rotation, touch calibration, and heap usage.

