#!/usr/bin/env python3
"""Quick test to verify the installation works."""

import asyncio
import sys


async def main():
    print("Testing iOS Simulator MCP Server installation...\n")

    # Test imports
    print("1. Testing imports...")
    try:
        from ios_simulator_mcp import __version__
        from ios_simulator_mcp.server import mcp, server
        from ios_simulator_mcp.simulator import SimulatorManager
        from ios_simulator_mcp.wda_client import WDAClient
        print(f"   ✓ All imports successful (version {__version__})")
        print(f"   ✓ MCP export compatibility: {server is mcp}")
    except ImportError as e:
        print(f"   ✗ Import error: {e}")
        return 1

    # Test simulator manager
    print("\n2. Testing SimulatorManager...")
    try:
        manager = SimulatorManager()
        devices = await manager.list_devices()
        print(f"   ✓ Found {len(devices)} simulators")

        booted = [d for d in devices if d.is_booted]
        if booted:
            print(f"   ✓ {len(booted)} simulator(s) currently booted:")
            for d in booted:
                print(f"     - {d.name} (iOS {d.ios_version})")
                print(f"       UDID: {d.udid}")
        else:
            print("   ⚠ No simulators currently booted")
            print("     Run: xcrun simctl boot <UDID>")
    except Exception as e:
        print(f"   ✗ SimulatorManager error: {e}")
        return 1

    # Test tool definitions
    print("\n3. Testing tool definitions...")
    try:
        tools = await mcp.get_tools()
        print(f"   ✓ {len(tools)} tools defined")
    except Exception as e:
        print(f"   ✗ Tool registry error: {e}")
        return 1

    # Check WDA connectivity (if simulator is booted)
    if booted:
        print("\n4. Testing WDA connectivity...")
        client = WDAClient()
        is_running = await client.health_check()
        if is_running:
            print("   ✓ WebDriverAgent is running on port 8100")
        else:
            print("   ⚠ WebDriverAgent not responding on port 8100")
            print("     Run: ./scripts/start_wda.sh <UDID>")
        await client.close()

    print("\n" + "=" * 50)
    print("Installation test complete!")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
