#!/usr/bin/env python3
"""
Plugin Manager for k7bat uConsole Plugins

This module provides plugin loading and management capabilities.
"""

import os
import sys
from typing import Dict, List, Any, Optional
from pathlib import Path


class PluginManager:
    """Manages plugin discovery, loading, and version tracking."""
    
    def __init__(self, plugins_dir: Optional[str] = None):
        """
        Initialize the plugin manager.
        
        Args:
            plugins_dir: Directory containing plugin folders. Defaults to 'plugins' subdirectory.
        """
        if plugins_dir is None:
            plugins_dir = Path(__file__).parent / "plugins"
        self.plugins_dir = Path(plugins_dir)
        self.loaded_plugins: Dict[str, Any] = {}
        self.plugin_versions: Dict[str, str] = {}
        
    def discover_plugins(self) -> List[Dict[str, Any]]:
        """
        Discover available plugins in the plugins directory.
        
        Returns:
            List of plugin information dictionaries
        """
        plugins = []
        
        if not self.plugins_dir.exists():
            return plugins
            
        for plugin_dir in self.plugins_dir.iterdir():
            if plugin_dir.is_dir() and (plugin_dir / "plugin_config.py").exists():
                plugins.append({
                    "name": plugin_dir.name,
                    "path": str(plugin_dir),
                    "has_config": True
                })
                
        return plugins
    
    def load_plugin(self, plugin_name: str) -> Optional[Any]:
        """
        Load a specific plugin by name.
        
        Args:
            plugin_name: Name of the plugin to load
            
        Returns:
            Plugin instance or None if loading fails
        """
        plugin_dir = self.plugins_dir / plugin_name
        
        if not plugin_dir.exists():
            print(f"Plugin directory not found: {plugin_name}")
            return None
            
        # Import plugin_config to get metadata
        sys.path.insert(0, str(plugin_dir))
        
        try:
            config_module = __import__("plugin_config")
            
            # Get plugin version
            version = getattr(config_module, "PLUGIN_VERSION", "0.0.0")
            self.plugin_versions[plugin_name] = version
            
            # Import and instantiate the plugin class
            module_name = getattr(config_module, "PLUGIN_MODULE", f"{plugin_name}_ui")
            plugin_module = __import__(module_name)
            
            # Get the main plugin class (usually has 'Plugin' in the name)
            for attr_name in dir(plugin_module):
                if "plugin" in attr_name.lower() and attr_name != "PluginManager":
                    plugin_class = getattr(plugin_module, attr_name)
                    if isinstance(plugin_class, type):
                        self.loaded_plugins[plugin_name] = plugin_class()
                        return self.loaded_plugins[plugin_name]
                        
        except Exception as e:
            print(f"Error loading plugin {plugin_name}: {e}")
            return None
        finally:
            sys.path.pop(0)
            
        return None
    
    def load_all_plugins(self) -> Dict[str, Any]:
        """
        Load all available plugins.
        
        Returns:
            Dictionary of loaded plugin instances
        """
        for plugin_info in self.discover_plugins():
            self.load_plugin(plugin_info["name"])
            
        return self.loaded_plugins
    
    def get_plugin_version(self, plugin_name: str) -> str:
        """
        Get the version of a specific plugin.
        
        Args:
            plugin_name: Name of the plugin
            
        Returns:
            Plugin version string
        """
        if plugin_name in self.plugin_versions:
            return self.plugin_versions[plugin_name]
            
        # Try to load and get version
        if plugin_name not in self.loaded_plugins:
            self.load_plugin(plugin_name)
            
        if plugin_name in self.loaded_plugins:
            plugin = self.loaded_plugins[plugin_name]
            if hasattr(plugin, 'version'):
                return getattr(plugin, 'version', '0.0.0')
                
        return "0.0.0"
    
    def get_all_versions(self) -> Dict[str, str]:
        """
        Get versions of all loaded plugins.
        
        Returns:
            Dictionary mapping plugin names to versions
        """
        if not self.plugin_versions:
            self.load_all_plugins()
            
        return self.plugin_versions
