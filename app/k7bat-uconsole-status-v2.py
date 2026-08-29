#!/usr/bin/env python3
\"\"\"
K7BAT uConsole Status App v2.0.0
GTK3 dashboard with modernized UI using reusable widgets.

Features:
- Centralized GTK CSS theme
- Reusable widget components (MetricCard, StatusCard, DeviceRow)
- Sidebar navigation system
- Dashboard layout redesign
\"\"\"

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from widgets.cards import MetricCard, StatusCard, DeviceRow, SectionHeader, ActionButton

APP_NAME = \"K7BAT uConsole Status App\"
APP_VERSION = \"2.0.0\"

class ModernApp(Gtk.Window):
    def __init__(self):
        super().__init__(title=APP_NAME)
        self.set_default_size(960, 540)
        self.connect('destroy', Gtk.main_quit)
        
        # Load theme
        self.load_theme()
        
        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        
        # Sidebar navigation
        self.stack = Gtk.Stack()
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        
        sidebar = self.create_navigation()
        
        # Dashboard page
        dashboard = self.create_dashboard()
        self.stack.add_titled(dashboard, 'dashboard', 'Dashboard')
        
        main_box.pack_start(sidebar, False, False, 0)
        main_box.pack_start(self.stack, True, True, 0)
        
        self.add(main_box)
        self.show_all()
    
    def load_theme(self):
        \"\"\"Load the centralized GTK CSS theme.\"\"\"
        screen = Gdk.Screen.get_default()
        provider = Gtk.CssProvider()
        
        theme_path = os.path.join(os.path.dirname(__file__), 'styles', 'theme.css')
        if os.path.exists(theme_path):
            try:
                provider.load_from_path(theme_path)
                Gtk.StyleContext.add_provider_for_screen(
                    screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )
            except Exception as e:
                print(f'Failed to load theme: {e}')
    
    def create_navigation(self):
        \"\"\"Create sidebar navigation.\"\"\"
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        sidebar.set_size_request(160, -1)
        sidebar.get_style_context().add_class('sidebar')
        
        # Logo/header
        logo = Gtk.Label(label='📊 K7BAT Status')
        logo.modify_font(Gdk.Pango.FontDescription.from_string('bold 12pt'))
        logo.set_margin_bottom(8)
        sidebar.pack_start(logo, False, False, 0)
        
        # Navigation buttons
        pages = [
            ('dashboard', 'Dashboard', '🏠'),
            ('system', 'System', '💻'),
            ('radio', 'Radio', '📡'),
            ('gps', 'GPS/Nav', '📍'),
            ('adsb', 'ADS-B', '✈️'),
            ('network', 'Network', '🌐'),
        ]
        
        for page_id, label, icon in pages:
            btn = Gtk.Button(label=f'{icon}  {label}')
            btn.set_halign(Gtk.Align.START)
            btn.get_style_context().add_class('sidebar-item')
            btn.connect('clicked', lambda b, pid=page_id: self.stack.set_visible_child_name(pid))
            sidebar.pack_start(btn, False, False, 0)
        
        return sidebar
    
    def create_dashboard(self):
        \"\"\"Create modern dashboard page.\"\"\"
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_hexpand(True)
        vbox.set_vexpand(True)
        vbox.set_margin_all(16)
        
        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        title = Gtk.Label(label='System Dashboard')
        title.modify_font(Gdk.Pango.FontDescription.from_string('bold 14pt'))
        title.get_style_context().add_class('title')
        header.pack_start(title, True, True, 0)
        
        timestamp = Gtk.Label(label=f'Updated: {__import__(\"datetime\").datetime.now().strftime(\"%H:%M:%S\")}')
        timestamp.set_margin_start(16)
        timestamp.modify_font(Gdk.Pango.FontDescription.from_string('9pt'))
        timestamp.set_opacity(0.7)
        header.pack_end(timestamp, False, False, 0)
        
        vbox.pack_start(header, False, False, 0)
        
        # Top row: CPU and Memory metrics
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        
        cpu_card = MetricCard(label='CPU Load', value='24%', subtitle='Average over 60s')
        memory_card = MetricCard(label='Memory', value='2.3/8 GB', subtitle='Available: 5.7GB')
        
        top_row.pack_start(cpu_card, True, True, 0)
        top_row.pack_start(memory_card, True, True, 0)
        vbox.pack_start(top_row, False, False, 0)
        
        # Status section
        status_header = SectionHeader('System Status')
        vbox.pack_start(status_header, False, False, 0)
        
        wifi_status = StatusCard(title='Wi-Fi', status='Connected - wlan0', icon='📶')
        gps_status = StatusCard(title='GPS', status='3D Fix - 12 satellites', icon='📍')
        
        vbox.pack_start(wifi_status, False, False, 0)
        vbox.pack_start(gps_status, False, False, 0)
        
        # Device list
        device_header = SectionHeader('Connected Devices')
        vbox.pack_start(device_header, False, False, 0)
        
        devices_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        
        devices_box.pack_start(DeviceRow('USB Storage', '128GB'), False, False, 0)
        devices_box.pack_start(DeviceRow('GPS Module', 'u-blox 8'), False, False, 0)
        devices_box.pack_start(DeviceRow('SDR Transceiver', 'RTL-SDR v3'), False, False, 0)
        
        vbox.pack_start(devices_box, False, False, 0)
        
        return vbox


if __name__ == '__main__':
    Gtk.init([])
    ModernApp()
    Gtk.main()
