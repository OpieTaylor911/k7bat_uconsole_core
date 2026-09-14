# K7BAT uConsole Status App

![K7BAT logo](assets/k7bat-logo.png)

Field computer dashboard and Sidekick companion API for ClockworkPi uConsole systems. The Status App is designed for small-screen field operation: system health, GPS, network, radios, launchers, plugins, diagnostics, and Sidekick device provisioning are available from one GTK3 interface.

![Status App screen](assets/screenshots/status-screen.png)

![Status App dashboard capture](assets/screenshots/status-dashboard.png)

The current release is K7BAT Status App `v2.0.4`, tagged in Git as `v2.0.4`.

## Features

### Status App

- GTK3 dashboard optimized for ClockworkPi uConsole displays.
- Status tabs for system, network, GPS, plugins, and TaskManager details.
- Main-page launcher controls for GPS navigation, Sidekick Manager, SDR, radio, and installed tools.
- Minimize control to return to the desktop without stopping the application.
- Exit and fullscreen controls, including keyboard-friendly operation.
- Live CPU temperature, memory, disk, battery, GPS, Wi-Fi, Ethernet, Bluetooth, and service state reporting.
- TaskManager view with uptime, load averages, memory/disk details, service states, and top processes sorted by CPU.
- Hardware-aware Power & Radios controls for GPS, SDR, LoRa, USB/AC1200, and Bluetooth where supported.
- GPS quality data including fix, satellites, position, speed, track, DOP, and trend information.
- Network details including active link, Wi-Fi trend, failover, hotspot watchdog, Bluetooth controller, and interface state.
- Profile presets, alert thresholds, touch mode, high contrast mode, day/night theme, snapshots, and mission recording.

### Launchers and plugins

- Built-in launchers are enabled only when their dependencies are detected.
- Plugin manager supports bundled and user-installed Python or shell plugins.
- Sidekick Manager opens as a separate window for ESP32 provisioning.
- Sidekick Setup provisions Wi-Fi, uConsole server address, firmware, and the Sidekick API token over USB serial.
- Remote Assist can create a diagnostics bundle for support.
- Optional tools include Navit, Pure Maps, Organic Maps, OSM Scout, SDR++, GQRX, Wireshark, Kismet, PyGPSClient, and other field utilities.

### Companion API

- Legacy `/api` endpoints remain available during migration.
- Versioned Sidekick contract under `/api/v2`.
- Full normalized status snapshot and domain endpoints for system, GPS, network, radio, ADS-B, Meshtastic, apps, profiles, and screens.
- Device registration and heartbeat.
- Six-digit device enrollment:
	- `POST /api/v2/device/enroll/start`
	- `POST /api/v2/device/enroll/confirm`
- API-key authentication for device operations.
- API version discovery through `GET /api/v2/version`, including `status_app_version`.
- Readiness check through `GET /api/v2/ready`.
- Device config, revocation, command jobs, and audit records in the expanded API implementation.
- REST polling fallback; `/ws/v2` is reserved for the future live event broker.

See [app/api_v2_sidekickRW.md](app/api_v2_sidekickRW.md) for the complete Sidekick contract, enrollment flow, LVGL state machine, and PlatformIO implementation guide.

## Architecture

```text
uConsole Linux services
				|
				v
K7BAT normalized status model
				|
				+-- GTK3 Status App
				+-- REST /api and /api/v2
				+-- Sidekick Setup over USB serial
				+-- ESP32 Sidekick over Wi-Fi
```

The uConsole remains authoritative. Sidekick devices display telemetry and issue approved commands; they do not manage Linux services directly.

## Supported platform

- ClockworkPi uConsole
- Debian 13 Trixie recommended
- Raspberry Pi CM4 or CM5
- labwc/Wayland desktop; X11-capable environments are also supported
- Optional HackerGadgets AIO V2 and AC1200 hardware
- Python 3, GTK3, PyGObject, gpsd, NetworkManager, BlueZ, and standard Linux process tools

Optional hardware and applications are detected dynamically. The dashboard remains usable when GPS, SDR, AIO, or secondary adapters are unavailable.

## Installation

On the uConsole:

```bash
git clone https://github.com/OpieTaylor911/k7bat_uconsole_core.git
cd k7bat_uconsole_core
git checkout v2.0.4
chmod +x install.sh scripts/*.sh
sudo ./install.sh
```

The installer:

1. Detects the active desktop user.
2. Installs required Debian packages, including GTK3, PyGObject, gpsd, BlueZ, networking, and process tooling.
3. Installs the Status App and API under `/home/bcaddy/uconsole-k7bat`.
4. Installs the `k7bat-uconsole-status` launcher and desktop entry.
5. Installs optional plugin and widget assets.
6. Preserves existing gpsd configuration and only applies conservative GPS setup when valid NMEA data is detected.

Launch the dashboard:

```bash
k7bat-uconsole-status
```

Launch Sidekick Manager:

```bash
k7bat-sidekick-setup
```

Upgrade an existing installation by running the installer again:

```bash
sudo ./install.sh
```

Uninstall:

```bash
sudo ./uninstall.sh
```

## Sidekick enrollment

1. Start the Status API on the uConsole.
2. Open **Sidekick Manager** or use the Sidekick Setup launcher.
3. Begin enrollment on the uConsole to obtain a six-digit pairing code.
4. Enter the code in the Sidekick setup flow.
5. The uConsole confirms the device and issues its API key.
6. The Sidekick stores the key and uses it for authenticated API calls.

API version check:

```bash
curl http://uconsole:8080/api/v2/version
```

Expected fields include:

```json
{
	"api_version": "2.0",
	"status_app_version": "2.0.4"
}
```

For ESP32/LVGL/PlatformIO implementation details, see the [Sidekick API guide](app/api_v2_sidekickRW.md).

## API smoke test

Run the local contract tests:

```powershell
Push-Location app
python -m unittest test_status_api_v2.py
Pop-Location
```

Exercise a live API:

```bash
python3 app/sidekick_device_test.py \
	--base-url http://127.0.0.1:8080 \
	--device-id sidekick-test-001 \
	--name "K7BAT Sidekick Test" \
	--board CYD_3248S035R
```

## Diagnostics

```bash
cgps -s
iw dev
nmcli device status
ethtool -i wlan1
```

Optional packages for additional launchers:

```bash
sudo apt install navit wireshark kismet gqrx-sdr
```

## Repository layout

```text
app/                         Core Status App, API, tests, and plugins
app/api_v2_sidekickRW.md    Sidekick REST/LVGL/PlatformIO contract
app/sidekick_device_test.py Sidekick API smoke test
assets/                     Desktop launcher assets
docs/                        API, Sidekick, plugin, and release documentation
assets/screenshots/         Status App screenshots
install.sh                  Debian/uConsole installer
k7bat-uconsole-status       Local launcher wrapper
```

## License

MIT. See [LICENSE](LICENSE).

Created by **K7BAT** for the ClockworkPi/uConsole community.
