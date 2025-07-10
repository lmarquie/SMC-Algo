#!/bin/bash

# Test script for the Elixir order monitor

echo "🧪 Testing Elixir Order Monitor..."

# Load .env file if it exists
if [[ -f ".env" ]]; then
    echo "📄 Loading environment variables from .env file..."
    export $(grep -v '^#' .env | xargs)
fi

# Check if environment variables are set
if [[ -z "$HYPERLIQUID_API_KEY" || -z "$HYPERLIQUID_ACCOUNT_ADDRESS" ]]; then
    echo "❌ Environment variables not set"
    echo "Please edit the .env file and set:"
    echo "  HYPERLIQUID_API_KEY=your_api_key_here"
    echo "  HYPERLIQUID_ACCOUNT_ADDRESS=your_account_address_here"
    exit 1
fi

echo "✅ Environment variables set"

# Start the monitor in background
echo "🚀 Starting order monitor..."
mix run --no-halt &
MONITOR_PID=$!

# Wait a moment for it to start
sleep 5

# Check if it's still running
if kill -0 $MONITOR_PID 2>/dev/null; then
    echo "✅ Monitor is running (PID: $MONITOR_PID)"
    echo "📋 You can now run your Python trading bot"
    echo "🛑 To stop the monitor: kill $MONITOR_PID"
else
    echo "❌ Monitor failed to start"
    exit 1
fi
