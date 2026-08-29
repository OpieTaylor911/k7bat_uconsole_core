<<<<<<< HEAD
# k7bat uConsole Core (v2.0.0+)

This is the main application repository for k7bat uConsole Status App version 2.0.0 and onward.

## Repository Structure

```
k7bat_uconsole_core/
├── app/                       # Main application code
│   ├── k7bat-uconsole-status.py
│   └── plugins/               # Core plugin system (minimal)
│       ├── plugin_base.py
│       └── plugin_manager.py
├── assets/                    # Application assets
├── scripts/                   # Deployment and utility scripts
└── install.sh                 # Installation script
```

## Key Changes from v1.x

- **Separate Plugin Repository**: Plugins moved to k7bat_uconsole_plugins repository
- **Individual Plugin Versions**: Each plugin has its own version tracking
- **Plugin Manager**: Unified plugin loading with version reporting
- **Cleaner Architecture**: Core app + plugins as separate releases

## Plugin Integration

The core application includes a minimal plugin system that can load plugins from:

1. Built-in plugins (in `app/plugins/`)
2. External plugins (from k7bat_uconsole_plugins repository)

Plugin loading is handled by the shared `plugin_manager.py` module.

## Release Strategy

- Core releases: Major version updates (v2.x, v3.x)
- Plugin releases: Independent versioning per plugin
- Plugin compatibility: Core specifies minimum required plugin API version

## Installation

See install.sh for installation instructions. The installer will:
1. Install core application to `/opt/k7bat-uconsole-status`
2. Copy plugin system files
3. Check for plugin dependencies (if using plugins)

## License

MIT License - See LICENSE file for details.
=======
# k7bat_uconsole_core
Field Computer Core Application for Field Operations
>>>>>>> 1b3437add97168dc95ab11cec24b42b78ec8de2a
