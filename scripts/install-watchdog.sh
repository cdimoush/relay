#!/usr/bin/env bash
# install-watchdog.sh — Instructions and automation for systemd watchdog setup.
#
# This script documents the manual steps needed to enable the systemd watchdog
# for the relay service. Run each step carefully.

set -euo pipefail

RELAY_DIR="/home/ubuntu/relay"

cat <<'INSTRUCTIONS'
=== Relay Systemd Watchdog Setup ===

The watchdog ensures systemd restarts relay if it becomes unresponsive.
Two changes are required:

1. INSTALL THE NEW SERVICE FILE:

   sudo cp /home/ubuntu/relay/scripts/relay.service.watchdog /etc/systemd/system/relay.service
   sudo systemctl daemon-reload

   This changes Type=simple to Type=notify and adds WatchdogSec=60.
   systemd will kill and restart relay if it doesn't ping within 60 seconds.

2. ADD sd-notify PING LOOP TO main.py:

   In src/relay/main.py, add the following near the top:

       import os
       import asyncio

       async def watchdog_ping():
           """Ping systemd watchdog every 30 seconds."""
           try:
               import sdnotify
               n = sdnotify.SystemdNotifier()
               n.notify("READY=1")
               while True:
                   n.notify("WATCHDOG=1")
                   await asyncio.sleep(30)
           except ImportError:
               pass  # sdnotify not installed, skip watchdog

   Then in your main async entry point, start it as a background task:

       asyncio.create_task(watchdog_ping())

   Install the sdnotify package:

       /home/ubuntu/relay/.venv/bin/pip install sdnotify

3. RESTART THE SERVICE:

   sudo systemctl daemon-reload
   sudo systemctl restart relay

4. VERIFY:

   systemctl show relay --property=WatchdogUSec
   # Should show WatchdogUSec=60000000 (60 seconds in microseconds)

INSTRUCTIONS

echo ""
echo "To auto-install the service file only (step 1), run:"
echo "  $0 --install-service"

if [[ "${1:-}" == "--install-service" ]]; then
    echo ""
    echo "Installing watchdog service file..."
    sudo cp "${RELAY_DIR}/scripts/relay.service.watchdog" /etc/systemd/system/relay.service
    sudo systemctl daemon-reload
    echo "Service file installed. You still need to add the watchdog ping to main.py."
    echo "Then run: sudo systemctl restart relay"
fi
