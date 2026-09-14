#!/bin/bash
# K7BAT uConsole Status API Startup Script

echo "Starting K7BAT uConsole Status API..."

# Change to app directory
cd "$(dirname "$0")"

# Start the API server in background
nohup python3 status_api.py --port 8080 > /tmp/status-api.log 2>&1 &

echo "Status API started on port 8080"
echo "PID: $!"

# Wait a moment for server to start
sleep 1

# Check if server is running
if curl -s http://localhost:8080/api/health > /dev/null; then
    echo "✓ Status API is running and healthy"
else
    echo "✗ Failed to start Status API. Check /tmp/status-api.log for details."
    exit 1
fi
