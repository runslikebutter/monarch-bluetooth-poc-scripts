#!/usr/bin/env python3
# scan_bmx_beacons.py
#
# Listen for BLE advertisements whose *complete/local name* starts with "BMX_"
# and print MAC, RSSI, and Tx‑Power (if the packet carries it).
#
# PAIRING LOGIC:
# - iPhone advertises "BMX_*" name for 10 seconds when user taps "pair"
# - This script attempts ONE pairing request per device/session
# - Multiple prevention mechanisms:
#   1. Track by MAC address (attempted_devices)
#   2. Track by device name (attempted_device_names) 
#   3. Minimum 15-second interval between ANY pairing attempts
#   4. Extended 12-second blocking period during pairing sequence
#
#   sudo python3 scan_bmx_beacons.py
#
# Requires Bleak ≥ 0.22 on Linux with BlueZ.

import asyncio
import subprocess
import time
import json
import re
import os
from datetime import datetime

from bleak import BleakScanner, AdvertisementData
import websockets  # pip install websockets

# at the top
DEBUG = False                    # flip to True when you really need the raw log

# WebSocket server configuration
WEBSOCKET_PORT = 8771

# WebSocket client management
connected_clients: set[websockets.WebSocketServerProtocol] = set()

async def register_client(ws):
    """Register a new WebSocket client."""
    connected_clients.add(ws)
    print(f"🔵 WebSocket CLIENT +1 (now {len(connected_clients)}) {ws.remote_address}")

async def unregister_client(ws):
    """Unregister a WebSocket client."""
    connected_clients.discard(ws)
    print(f"🔴 WebSocket CLIENT -1 (now {len(connected_clients)})")

async def websocket_handler(ws):
    """Handle WebSocket connections."""
    await register_client(ws)
    try:
        async for msg in ws:  # we don't expect messages, but log anyway
            print(f"📥 From WebSocket client: {msg}")
    finally:
        await unregister_client(ws)

async def broadcast_message(message):
    """Broadcast a message to all connected WebSocket clients."""
    if not connected_clients:
        return
    
    payload = json.dumps(message)
    dead = set()
    for ws in connected_clients:
        try:
            await ws.send(payload)
        except websockets.exceptions.ConnectionClosed:
            dead.add(ws)
    
    # Remove dead connections
    for ws in dead:
        await unregister_client(ws)

def extract_tenant_id(device_name):
    """Extract tenant ID from device name like 'BMX_P123456' -> '123456'."""
    if device_name and device_name.startswith("BMX_P"):
        return device_name[5:]  # Remove "BMX_P" prefix
    return None


def read_tenants_and_macs():
    """Read the tenants-and-macs.json file or return default structure."""
    json_file = "tenants-and-macs.json"
    try:
        if os.path.exists(json_file):
            with open(json_file, 'r') as f:
                data = json.load(f)
                # Ensure the structure is correct
                if 'tenantsAndMacs' not in data:
                    data = {"tenantsAndMacs": []}
                return data
        else:
            # File doesn't exist, return default structure
            return {"tenantsAndMacs": []}
    except (json.JSONDecodeError, IOError) as e:
        print(f"⚠️ Error reading {json_file}: {e}")
        return {"tenantsAndMacs": []}


def write_tenants_and_macs(data):
    """Write the tenants-and-macs data to the JSON file."""
    json_file = "tenants-and-macs.json"
    try:
        with open(json_file, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except IOError as e:
        print(f"⚠️ Error writing {json_file}: {e}")
        return False


def update_tenant_mac_mapping(tenant_id, identity_mac):
    """Update the tenant ID and MAC mapping in the JSON file."""
    # Read current data
    data = read_tenants_and_macs()
    
    # Look for existing entry with the same MAC
    existing_entry = None
    for entry in data["tenantsAndMacs"]:
        if entry.get("mac") == identity_mac:
            existing_entry = entry
            break
    
    if existing_entry:
        # Update existing entry
        old_tenant_id = existing_entry.get("id")
        existing_entry["id"] = tenant_id
        print(f"📝 Updated MAC {identity_mac}: tenant {old_tenant_id} → {tenant_id}")
    else:
        # Add new entry
        new_entry = {"id": tenant_id, "mac": identity_mac}
        data["tenantsAndMacs"].append(new_entry)
        print(f"➕ Added new mapping: tenant {tenant_id} → MAC {identity_mac}")
    
    # Write back to file
    if write_tenants_and_macs(data):
        print(f"💾 Successfully updated tenants-and-macs.json")
        return True
    else:
        print(f"❌ Failed to write tenants-and-macs.json")
        return False


def interesting(line: str) -> bool:
    """Keep only the messages you want to surface"""
    keywords = (
        "Confirm passkey",
        "Pairing successful",
        "Paired: yes",
        "has been paired",
        "Failed to pair",
        "Connection successful",
        "Failed to connect",
        "Device not available",
    )
    return any(k in line for k in keywords)


# Global flag to track if we're currently in a connection sequence
connection_in_progress = False

# Track devices we've already attempted to connect to - using both MAC and name
attempted_devices = set()
attempted_device_names = set()

# Track the timestamp of the last connection attempt
last_connection_time = 0


def reset_attempted_devices():
    """Clear the list of attempted devices - useful for testing"""
    global attempted_devices, attempted_device_names, last_connection_time
    attempted_devices.clear()
    attempted_device_names.clear()
    last_connection_time = 0
    ts = datetime.now().isoformat(timespec="seconds")
    print(f"{ts}  Cleared attempted devices list")


async def pair_device_only(mac_address, beacon_name):
    """Execute only the pairing part of the bluetooth connection."""
    
    def run_pair_only(timeout=45):
        """Run bluetoothctl pair command only."""
        successful_pair_tenant_id = None
        identity_mac = None
        
        try:
            process = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
                universal_newlines=True
            )
            
            # Send initial setup commands
            setup_commands = [
                "power on",
                "agent on", 
                "default-agent"
            ]
            
            for cmd in setup_commands:
                process.stdin.write(cmd + "\n")
                process.stdin.flush()
                time.sleep(0.5)  # Small delay between commands
            
            # 1. Pair (this triggers one popup on the iPhone)
            process.stdin.write(f"pair {mac_address}\n")
            process.stdin.flush()
            
            # Wait until we see "Pairing successful" before continuing
            output_lines = []
            start_time = time.time()
            
            while time.time() - start_time < timeout:
                if process.poll() is not None:
                    break
                    
                # Try to read output
                try:
                    # Try to use select for non-blocking read (Unix/Linux)
                    try:
                        import select
                        if select.select([process.stdout], [], [], 1.0)[0]:
                            line = process.stdout.readline()
                            if line:
                                output_lines.append(line.strip())

                                # show nothing unless it's in our allow‑list … or DEBUG is on
                                if DEBUG or interesting(line):
                                    print(f"  > {line.strip()}")
                                
                                # Check for identity MAC in Device lines with Paired: yes
                                if "Device " in line and "Paired: yes" in line:
                                    print(f"🔎 Found Device Paired line: {line.strip()}")
                                    # Extract MAC from line like: "[CHG] Device 70:22:FE:03:C1:41 Paired: yes"
                                    # Skip the problematic [CHG] part and just match Device ... Paired: yes
                                    mac_match = re.search(r'Device ([A-Fa-f0-9:]{17}) Paired: yes', line)
                                    if mac_match:
                                        identity_mac = mac_match.group(1)
                                        print(f"🔍 Identity MAC detected: {identity_mac}")
                                    else:
                                        print(f"⚠️ Device Paired line found but regex didn't match: {line.strip()}")
                                
                                # Check for passkey confirmation first
                                if "Confirm passkey" in line:
                                    print("📱 Passkey confirmation detected!")
                                    process.stdin.write("yes\n")   # answer once
                                    process.stdin.flush()
                                    print("  → Sent 'yes' to confirm passkey")
                                    continue                       # keep reading
                                
                                # Check for pairing results
                                if "Pairing successful" in line or "Paired: yes" in line or "has been paired" in line:
                                    print("✓ Pairing successful detected! (First block)")
                                    # Store tenant ID for later broadcasting
                                    tenant_id = extract_tenant_id(beacon_name)
                                    if tenant_id:
                                        successful_pair_tenant_id = tenant_id
                                        print(f"📡 Will broadcast successful pair for tenant: {tenant_id}")
                                    break
                                elif "Failed to pair" in line:
                                    print("⚠ Pairing failed detected!")
                                    break
                                elif "Device not available" in line:
                                    print("⚠ Device not available!")
                                    break
                    except ImportError:
                        # Fallback for systems without select (like Windows)
                        time.sleep(0.5)
                        line = process.stdout.readline()
                        if line:
                            output_lines.append(line.strip())

                            # show nothing unless it's in our allow‑list … or DEBUG is on
                            if DEBUG or interesting(line):
                                print(f"  > {line.strip()}")
                            
                            # Check for identity MAC in Device lines with Paired: yes
                            if "Device " in line and "Paired: yes" in line:
                                print(f"🔎 Found Device Paired line: {line.strip()}")
                                # Extract MAC from line like: "[CHG] Device 70:22:FE:03:C1:41 Paired: yes"
                                # Skip the problematic [CHG] part and just match Device ... Paired: yes
                                mac_match = re.search(r'Device ([A-Fa-f0-9:]{17}) Paired: yes', line)
                                if mac_match:
                                    identity_mac = mac_match.group(1)
                                    print(f"🔍 Identity MAC detected: {identity_mac}")
                                else:
                                    print(f"⚠️ Device Paired line found but regex didn't match: {line.strip()}")
                            
                            # Check for passkey confirmation first
                            if "Confirm passkey" in line:
                                print("📱 Passkey confirmation detected!")
                                process.stdin.write("yes\n")   # answer once
                                process.stdin.flush()
                                print("  → Sent 'yes' to confirm passkey")
                                continue                       # keep reading
                            
                            # Check for pairing results (same logic as above)
                            if "Pairing successful" in line or "Paired: yes" in line or "has been paired" in line:
                                print("✓ Pairing successful detected! (Second block)")
                                # Store tenant ID for later broadcasting
                                tenant_id = extract_tenant_id(beacon_name)
                                if tenant_id:
                                    successful_pair_tenant_id = tenant_id
                                    print(f"📡 Will broadcast successful pair for tenant: {tenant_id}")
                                break
                            elif "Failed to pair" in line:
                                print("⚠ Pairing failed detected!")
                                break
                            elif "Device not available" in line:
                                print("⚠ Device not available!")
                                break
                except:
                    time.sleep(0.1)
            
            # Send exit command
            try:
                process.stdin.write("exit\n")
                process.stdin.flush()
            except:
                pass  # Process may have already exited
            
            # Wait for process to finish
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            
            return "\n".join(output_lines), stderr, process.returncode, successful_pair_tenant_id, identity_mac
            
        except Exception as e:
            return "", str(e), -1, None, None

    ts = datetime.now().isoformat(timespec="seconds")
    print(f"{ts}  Starting pairing for {beacon_name} ({mac_address})")
    
    stdout, stderr, returncode, successful_pair_tenant_id, identity_mac = run_pair_only()
    
    ts = datetime.now().isoformat(timespec="seconds")
    print(f"{ts}  Pairing phase completed")
    
    if stderr:
        print(f"{ts}  Stderr:")
        print(stderr)
    
    # Check for specific pairing results in the output
    if "Pairing successful" in stdout or "Paired: yes" in stdout or "has been paired" in stdout:
        print(f"{ts}  ✓ Successfully paired with {beacon_name}")
        
        # Log the tenant ID to identity MAC mapping
        if successful_pair_tenant_id and identity_mac:
            print(f"🗝️  PAIRING MAPPING: Tenant {successful_pair_tenant_id} → Identity MAC {identity_mac}")
            print(f"📋 Original revolving MAC: {mac_address}")
            print(f"🔗 Resolved identity MAC: {identity_mac}")
        elif successful_pair_tenant_id:
            print(f"⚠️  Tenant {successful_pair_tenant_id} paired but identity MAC not captured")
            
    elif "Failed to pair" in stdout:
        print(f"{ts}  ⚠ Failed to pair with {beacon_name}")
    elif "Device not available" in stdout:
        print(f"{ts}  ⚠ Device {beacon_name} not available")
    elif "Device not found" in stdout:
        print(f"{ts}  ⚠ Device {beacon_name} not found")
    else:
        print(f"{ts}  ⚠ Pairing attempt completed with unclear result for {beacon_name}")
        print(f"{ts}  Return code: {returncode}")

    return successful_pair_tenant_id, identity_mac


async def trust_and_connect_device(mac_address, beacon_name):
    """Execute trust and connect commands after pairing."""
    
    def run_trust_and_connect(timeout=45):
        """Run bluetoothctl trust and connect commands."""
        try:
            process = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
                universal_newlines=True
            )
            
            # Send initial setup commands
            setup_commands = [
                "power on",
                "agent on", 
                "default-agent"
            ]
            
            for cmd in setup_commands:
                process.stdin.write(cmd + "\n")
                process.stdin.flush()
                time.sleep(0.5)  # Small delay between commands
            
            # 1. Trust so BlueZ will remember the bond
            process.stdin.write(f"trust {mac_address}\n")
            process.stdin.flush()
            time.sleep(0.5)
            
            # 2. Connect
            process.stdin.write(f"connect {mac_address}\n")
            process.stdin.flush()
            
            # Wait for connection response
            output_lines = []
            start_time = time.time()
            
            while time.time() - start_time < timeout:
                if process.poll() is not None:
                    break
                    
                # Try to read output
                try:
                    # Try to use select for non-blocking read (Unix/Linux)
                    try:
                        import select
                        if select.select([process.stdout], [], [], 1.0)[0]:
                            line = process.stdout.readline()
                            if line:
                                output_lines.append(line.strip())

                                # show nothing unless it's in our allow‑list … or DEBUG is on
                                if DEBUG or interesting(line):
                                    print(f"  > {line.strip()}")
                                
                                # Check for connection results
                                if "Connection successful" in line:
                                    print("✓ Connection successful detected!")
                                    # Exit bluetoothctl immediately to stop extra output
                                    process.stdin.write("exit\n")
                                    process.stdin.flush()
                                    break
                                elif "Failed to connect" in line:
                                    print("⚠ Connection failed detected!")
                                    # Exit bluetoothctl immediately
                                    process.stdin.write("exit\n")
                                    process.stdin.flush()
                                    break
                                elif "Device not available" in line:
                                    print("⚠ Device not available!")
                                    # Exit bluetoothctl immediately
                                    process.stdin.write("exit\n")
                                    process.stdin.flush()
                                    break
                    except ImportError:
                        # Fallback for systems without select (like Windows)
                        time.sleep(0.5)
                        line = process.stdout.readline()
                        if line:
                            output_lines.append(line.strip())

                            # show nothing unless it's in our allow‑list … or DEBUG is on
                            if DEBUG or interesting(line):
                                print(f"  > {line.strip()}")
                            
                            # Check for connection results (same logic as above)
                            if "Connection successful" in line:
                                print("✓ Connection successful detected!")
                                # Exit bluetoothctl immediately to stop extra output
                                process.stdin.write("exit\n")
                                process.stdin.flush()
                                break
                            elif "Failed to connect" in line:
                                print("⚠ Connection failed detected!")
                                # Exit bluetoothctl immediately
                                process.stdin.write("exit\n")
                                process.stdin.flush()
                                break
                            elif "Device not available" in line:
                                print("⚠ Device not available!")
                                # Exit bluetoothctl immediately
                                process.stdin.write("exit\n")
                                process.stdin.flush()
                                break
                except:
                    time.sleep(0.1)
            
            # Send exit command (if not already sent)
            try:
                process.stdin.write("exit\n")
                process.stdin.flush()
            except:
                pass  # Process may have already exited
            
            # Wait for process to finish
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            
            return "\n".join(output_lines), stderr, process.returncode
            
        except Exception as e:
            return "", str(e), -1

    ts = datetime.now().isoformat(timespec="seconds")
    print(f"{ts}  Starting trust and connect for {beacon_name} ({mac_address})")
    
    stdout, stderr, returncode = run_trust_and_connect()
    
    ts = datetime.now().isoformat(timespec="seconds")
    print(f"{ts}  Trust and connect phase completed")
    
    if stderr:
        print(f"{ts}  Stderr:")
        print(stderr)
    
    # Check for specific connection results in the output
    if "Connection successful" in stdout:
        print(f"{ts}  ✓ Successfully connected to {beacon_name}")
    elif "Failed to connect" in stdout:
        print(f"{ts}  ⚠ Failed to connect to {beacon_name}")
    elif "Device not available" in stdout:
        print(f"{ts}  ⚠ Device {beacon_name} not available")
    elif "Device not found" in stdout:
        print(f"{ts}  ⚠ Device {beacon_name} not found")
    else:
        print(f"{ts}  ⚠ Connection attempt completed with unclear result for {beacon_name}")
        print(f"{ts}  Return code: {returncode}")


# OLD bluetooth_operations function removed - replaced by pair_device_only + trust_and_connect_device
# This fixes the async/await issue and allows immediate broadcasting after pairing


def handle_detection(device, adv: AdvertisementData):
    global connection_in_progress, attempted_devices, attempted_device_names, last_connection_time
    
    name = adv.local_name or device.name or ""
    if not name.startswith("BMX_P"):
        return

    # If we're already in a connection sequence, ignore this packet
    if connection_in_progress:
        return

    mac = device.address
    current_time = time.time()
    
    # Check if we've already attempted this device by MAC or name
    if mac in attempted_devices or name in attempted_device_names:
        return  # Skip - we already tried this device
    
    # Prevent rapid repeated attempts - minimum 15 seconds between any connection attempts
    # This should cover the iPhone's 10-second advertising window plus some buffer
    if current_time - last_connection_time < 15:
        ts = datetime.now().isoformat(timespec="seconds")
        print(f"{ts}  Skipping connection attempt - too soon since last attempt")
        return
    
    rssi = device.rssi                       # dBm
    tx = adv.tx_power if adv.tx_power is not None else "N/A"

    ts = datetime.now().isoformat(timespec="seconds")
    print(f"{ts}  {name:10}  MAC: {mac}  RSSI: {rssi:>4} dBm  Tx: {tx}")
    
    # Add device to both attempted lists
    attempted_devices.add(mac)
    attempted_device_names.add(name)
    last_connection_time = current_time
    
    # Start the connection sequence asynchronously
    asyncio.create_task(start_connection_sequence(name, mac))


async def start_connection_sequence(beacon_name, mac_address):
    """Handle the connection sequence for a BMX beacon."""
    global connection_in_progress
    
    connection_in_progress = True
    
    try:
        # Wait 2 seconds before starting connection
        # (This will check to see if the tenant lives in the buidling... OR use the secret key from the server)
        await asyncio.sleep(2)
        
        # Log the connection start message
        ts = datetime.now().isoformat(timespec="seconds")
        print(f"{ts}  Connection sequence started - {beacon_name}")
        
        # 1. Pair first and get tenant ID + identity MAC if successful
        successful_pair_tenant_id, identity_mac = await pair_device_only(mac_address, beacon_name)
        
        # 2. Broadcast immediately if pairing succeeded!
        if successful_pair_tenant_id:
            message = {"successfulPair": successful_pair_tenant_id}
            await broadcast_message(message)
            print(f"📡 Broadcasting successful pair for tenant: {successful_pair_tenant_id}")
            
            # 3. Store the tenant ID to identity MAC mapping
            if identity_mac:
                print(f"💾 Storing mapping: tenantId={successful_pair_tenant_id}, tenantMac={identity_mac}")
                update_tenant_mac_mapping(successful_pair_tenant_id, identity_mac)
        
        # 4. Then trust and connect (regardless of whether pairing succeeded, in case it was already paired)
        await trust_and_connect_device(mac_address, beacon_name)
        
        # Extended cooldown period to ensure we don't retry during iPhone's advertising window
        # iPhone advertises for 10 seconds, so we wait 12 seconds total to be safe
        await asyncio.sleep(12)
        
        ts = datetime.now().isoformat(timespec="seconds")
        print(f"{ts}  Connection sequence completed for {beacon_name}")
        
    finally:
        # Reset the flag to allow new connection sequences
        connection_in_progress = False


async def main():
    print("🚪 BMX Beacon Scanner + WebSocket Server starting…")
    print(f"🌐 WebSocket server → ws://localhost:{WEBSOCKET_PORT}")
    print("🔍 Scanning for BMX_P* beacons...")
    
    # Test regex pattern at startup
    # Test the updated regex pattern (skipping problematic [CHG] part)
    test_line = "[CHG] Device 70:22:FE:03:C1:41 Paired: yes"
    test_match = re.search(r'Device ([A-Fa-f0-9:]{17}) Paired: yes', test_line)
    if test_match:
        print(f"✅ Updated regex test passed! Extracted MAC: {test_match.group(1)}")
    else:
        print(f"❌ Updated regex test failed on: {test_line}")
    
    # Show current tenants and MACs file status
    current_data = read_tenants_and_macs()
    print(f"📄 Current tenants-and-macs.json: {len(current_data['tenantsAndMacs'])} entries")
    print("=" * 50)
    
    # Start WebSocket server
    ws_server = await websockets.serve(websocket_handler, "localhost", WEBSOCKET_PORT)
    
    # Start BLE scanner
    scanner = BleakScanner(handle_detection, scanning_mode="active")
    scan_task = asyncio.create_task(scanner.start())
    
    try:
        # Wait for both scanner and websocket server
        await asyncio.gather(
            ws_server.wait_closed(),
            asyncio.Future()  # Run until Ctrl-C
        )
    finally:
        # Clean up
        scan_task.cancel()
        await scanner.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
