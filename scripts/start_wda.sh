#!/bin/bash
# Start WebDriverAgent on an iOS Simulator
#
# Usage: ./start_wda.sh [SIMULATOR_UDID]
#
# If no UDID is provided, shows a list of available simulators to choose from.
# Press Ctrl+C to stop WDA and automatically uninstall it from the simulator.
#
# Prerequisites:
#   - Xcode installed
#   - WebDriverAgent cloned and configured

set -e

UDID="${1:-}"
# WebDriverAgent now lives in the appium-ios monorepo; ~/WebDriverAgent is the
# old standalone repo, still used when it is the only one present.
if [ -z "${WDA_PATH:-}" ]; then
    if [ -d "$HOME/appium-ios/packages/webdriveragent" ] || [ ! -d "$HOME/WebDriverAgent" ]; then
        WDA_PATH="$HOME/appium-ios/packages/webdriveragent"
    else
        WDA_PATH="$HOME/WebDriverAgent"
    fi
fi
WDA_PORT="${WDA_PORT:-8100}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# WebDriverAgent bundle ID
WDA_BUNDLE_ID="com.facebook.WebDriverAgentRunner.xctrunner"

# Cleanup function to uninstall WDA on exit
cleanup() {
    echo ""
    echo -e "${YELLOW}Stopping WebDriverAgent...${NC}"

    if [ -n "$UDID" ]; then
        echo -e "${YELLOW}Uninstalling WebDriverAgent from simulator...${NC}"
        xcrun simctl uninstall "$UDID" "$WDA_BUNDLE_ID" 2>/dev/null || true
        echo -e "${GREEN}WebDriverAgent uninstalled.${NC}"
    fi

    exit 0
}

# Trap Ctrl+C (SIGINT) and other termination signals
trap cleanup SIGINT SIGTERM

usage() {
    echo "Usage: $0 [SIMULATOR_UDID]"
    echo ""
    echo "If no UDID is provided, shows a list of available simulators to choose from."
    echo "Press Ctrl+C to stop and uninstall WDA from the simulator."
    echo ""
    echo "Environment variables:"
    echo "  WDA_PATH  Path to WebDriverAgent project (default: ~/appium-ios/packages/webdriveragent)"
    echo "  WDA_PORT  Port to run WDA on (default: 8100)"
    echo ""
    echo "Example:"
    echo "  $0                                           # Interactive selection"
    echo "  $0 XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX      # Direct UDID"
    exit 1
}

# If no UDID provided, show list of devices and let user choose
if [ -z "$UDID" ]; then
    echo -e "${YELLOW}No UDID provided. Checking available simulators...${NC}"
    echo ""

    # Get list of booted simulators first
    BOOTED_DEVICES=$(xcrun simctl list devices -j | python3 -c "
import json, sys
data = json.load(sys.stdin)
devices = []
for runtime, device_list in data.get('devices', {}).items():
    if 'iOS' in runtime:
        ios_version = runtime.split('.')[-1].replace('-', '.')
        for d in device_list:
            if d.get('isAvailable', False):
                status = 'Booted' if d.get('state') == 'Booted' else 'Shutdown'
                devices.append((d['udid'], d['name'], ios_version, status, d.get('state') == 'Booted'))
# Sort: booted first, then by name
devices.sort(key=lambda x: (not x[4], x[1]))
for i, (udid, name, ios, status, _) in enumerate(devices, 1):
    marker = ' *' if status == 'Booted' else ''
    print(f'{i}) {name} (iOS {ios}) [{status}]{marker}')
    print(f'   {udid}')
" 2>/dev/null)

    if [ -z "$BOOTED_DEVICES" ]; then
        echo -e "${RED}No simulators found. Please create one in Xcode.${NC}"
        exit 1
    fi

    echo "Available simulators (* = currently booted):"
    echo ""
    echo "$BOOTED_DEVICES"
    echo ""

    # Count devices
    DEVICE_COUNT=$(echo "$BOOTED_DEVICES" | grep -c "^[0-9]") || DEVICE_COUNT=0

    read -p "Select simulator (1-$DEVICE_COUNT): " SELECTION

    # Extract UDID from selection
    UDID=$(xcrun simctl list devices -j | python3 -c "
import json, sys
data = json.load(sys.stdin)
devices = []
for runtime, device_list in data.get('devices', {}).items():
    if 'iOS' in runtime:
        for d in device_list:
            if d.get('isAvailable', False):
                devices.append((d['udid'], d['name'], d.get('state') == 'Booted'))
devices.sort(key=lambda x: (not x[2], x[1]))
selection = int(sys.argv[1]) - 1
if 0 <= selection < len(devices):
    print(devices[selection][0])
" "$SELECTION" 2>/dev/null)

    if [ -z "$UDID" ]; then
        echo -e "${RED}Invalid selection${NC}"
        exit 1
    fi

    echo ""
    echo -e "${GREEN}Selected: $UDID${NC}"
    echo ""
fi

# Check if WDA exists
if [ ! -d "$WDA_PATH" ]; then
    echo -e "${YELLOW}WebDriverAgent not found at $WDA_PATH${NC}"
    echo ""
    echo "To install WebDriverAgent:"
    echo "  git clone https://github.com/appium/appium-ios.git ~/appium-ios"
    echo ""
    echo "Or set WDA_PATH to your WebDriverAgent location:"
    echo "  WDA_PATH=/path/to/WebDriverAgent $0 $UDID"
    exit 1
fi

# Check if simulator is booted
BOOTED=$(xcrun simctl list devices | grep "$UDID" | grep -c "Booted" || true)
if [ "$BOOTED" -eq 0 ]; then
    echo -e "${YELLOW}Simulator $UDID is not booted. Booting now...${NC}"
    xcrun simctl boot "$UDID" || true
    sleep 3
fi

echo -e "${GREEN}Starting WebDriverAgent on simulator $UDID...${NC}"
echo "WDA Path: $WDA_PATH"
echo "WDA Port: $WDA_PORT"
echo ""

cd "$WDA_PATH"

# Build and run WebDriverAgent
# Note: This will run until interrupted (Ctrl+C), then cleanup will uninstall WDA
xcodebuild -project WebDriverAgent.xcodeproj \
    -scheme WebDriverAgentRunner \
    -destination "platform=iOS Simulator,id=$UDID" \
    -derivedDataPath build \
    USE_PORT="$WDA_PORT" \
    test

# If xcodebuild exits normally (shouldn't happen), run cleanup
cleanup
