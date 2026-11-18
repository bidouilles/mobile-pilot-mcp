#!/bin/bash
# Setup script for Mobile Pilot MCP Server
#
# This script will:
# 1. Check prerequisites
# 2. Create a virtual environment
# 3. Install dependencies
# 4. Provide next steps

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== Mobile Pilot MCP Server Setup ===${NC}"
echo ""

# Check Python version
echo -n "Checking Python version... "
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
    PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

    if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 11 ]; then
        echo -e "${GREEN}Python $PYTHON_VERSION${NC}"
    else
        echo -e "${RED}Python $PYTHON_VERSION (requires 3.11+)${NC}"
        exit 1
    fi
else
    echo -e "${RED}Python 3 not found${NC}"
    exit 1
fi

# Check Xcode Command Line Tools
echo -n "Checking Xcode Command Line Tools... "
if xcode-select -p &> /dev/null; then
    echo -e "${GREEN}Installed${NC}"
else
    echo -e "${YELLOW}Not installed${NC}"
    echo "Installing Xcode Command Line Tools..."
    xcode-select --install
    echo "Please run this script again after installation completes."
    exit 1
fi

# Check xcrun simctl
echo -n "Checking simctl... "
if xcrun simctl help &> /dev/null; then
    echo -e "${GREEN}Available${NC}"
else
    echo -e "${RED}Not available${NC}"
    echo "Make sure Xcode is properly installed."
    exit 1
fi

cd "$PROJECT_DIR"

# Create virtual environment
echo ""
echo "Creating virtual environment..."
if [ -d "venv" ]; then
    echo -e "${YELLOW}Virtual environment already exists${NC}"
else
    python3 -m venv venv
    echo -e "${GREEN}Virtual environment created${NC}"
fi

# Activate and install
echo "Installing dependencies..."
source venv/bin/activate
pip install --upgrade pip > /dev/null
pip install -e . > /dev/null
echo -e "${GREEN}Dependencies installed${NC}"

# Check for WebDriverAgent
echo ""
echo -n "Checking for WebDriverAgent... "
if [ -d "$HOME/appium-ios/packages/webdriveragent" ]; then
    echo -e "${GREEN}Found at ~/appium-ios/packages/webdriveragent${NC}"
    WDA_FOUND=true
elif [ -d "$HOME/WebDriverAgent" ]; then
    echo -e "${GREEN}Found at ~/WebDriverAgent (old standalone repo)${NC}"
    WDA_FOUND=true
else
    echo -e "${YELLOW}Not found${NC}"
    WDA_FOUND=false
fi

# Summary
echo ""
echo -e "${GREEN}=== Setup Complete ===${NC}"
echo ""
echo "Next steps:"
echo ""
echo "1. Start a simulator:"
echo "   ${BLUE}xcrun simctl list devices${NC}"
echo "   ${BLUE}xcrun simctl boot <UDID>${NC}"
echo "   ${BLUE}open -a Simulator${NC}"
echo ""

if [ "$WDA_FOUND" = false ]; then
    echo "2. Install WebDriverAgent (required for UI automation):"
    echo "   ${BLUE}git clone https://github.com/appium/appium-ios.git ~/appium-ios${NC}"
    echo "   Then run:"
    echo "   ${BLUE}./scripts/start_wda.sh <UDID>${NC}"
    echo ""
    echo "3. Run the MCP server:"
else
    echo "2. Start WebDriverAgent:"
    echo "   ${BLUE}./scripts/start_wda.sh <UDID>${NC}"
    echo ""
    echo "3. Run the MCP server:"
fi

echo "   ${BLUE}./scripts/run_server.sh${NC}"
echo ""
echo "4. Configure your MCP client (Claude Code, Cursor, etc.):"
echo "   Add to your MCP settings:"
echo '   {'
echo '     "mcpServers": {'
echo '       "mobile-pilot": {'
echo '         "command": "'$PROJECT_DIR'/venv/bin/mobile-pilot-mcp"'
echo '       }'
echo '     }'
echo '   }'
echo ""
