#!/usr/bin/env python3
# delete-paired-devices.py
#
# Deletes all paired Bluetooth devices using bluetoothctl
# Also clears the tenants-and-macs.json file, resetting it to default empty structure
# Equivalent to running: bluetoothctl -> paired-devices -> remove [MAC] for each device
#
# Usage: python3 delete-paired-devices.py

import subprocess
import sys
import json
import os
from datetime import datetime

def get_paired_devices():
    """Get all paired Bluetooth devices using bluetoothctl."""
    
    try:
        # Run bluetoothctl with paired-devices command
        process = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Send the paired-devices command and exit
        command_sequence = "paired-devices\nexit\n"
        stdout, stderr = process.communicate(input=command_sequence, timeout=10)
        
        if process.returncode == 0:
            # Parse the output to extract device information
            lines = stdout.strip().split('\n')
            paired_devices = []
            
            for line in lines:
                line = line.strip()
                if line.startswith('Device '):
                    # Format: Device AA:BB:CC:DD:EE:FF Device Name
                    parts = line.split(' ', 2)
                    if len(parts) >= 3:
                        mac_address = parts[1]
                        device_name = parts[2]
                        paired_devices.append((mac_address, device_name))
                    elif len(parts) == 2:
                        mac_address = parts[1]
                        device_name = "Unknown Device"
                        paired_devices.append((mac_address, device_name))
            
            return paired_devices, None
                
        else:
            error_msg = f"Error running bluetoothctl (return code: {process.returncode})"
            if stderr:
                error_msg += f"\nError output: {stderr}"
            return None, error_msg
                
    except subprocess.TimeoutExpired:
        process.kill()
        return None, "Command timed out"
    except FileNotFoundError:
        return None, "bluetoothctl not found. Make sure BlueZ is installed."
    except Exception as e:
        return None, f"Error: {e}"

def remove_device(mac_address, device_name):
    """Remove a single Bluetooth device using bluetoothctl."""
    
    ts = datetime.now().isoformat(timespec="seconds")
    print(f"{ts}  Removing {device_name} ({mac_address})...")
    
    try:
        # Run bluetoothctl with remove command
        process = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Send the remove command and exit
        command_sequence = f"remove {mac_address}\nexit\n"
        stdout, stderr = process.communicate(input=command_sequence, timeout=15)
        
        if process.returncode == 0:
            # Check if removal was successful
            if "Device has been removed" in stdout or "removed successfully" in stdout.lower():
                print(f"    ✓ Successfully removed {device_name}")
                return True
            elif "Device not available" in stdout or "not available" in stdout.lower():
                print(f"    ⚠ Device {device_name} was not available for removal")
                return False
            else:
                print(f"    ⚠ Removal status unclear for {device_name}")
                return False
        else:
            print(f"    ⚠ Error removing {device_name} (return code: {process.returncode})")
            if stderr:
                print(f"    Error output: {stderr}")
            return False
                
    except subprocess.TimeoutExpired:
        process.kill()
        print(f"    ⚠ Removal command timed out for {device_name}")
        return False
    except Exception as e:
        print(f"    ⚠ Error removing {device_name}: {e}")
        return False

def clear_tenants_and_macs_file():
    """Clear the tenants-and-macs.json file, resetting it to default empty structure."""
    
    json_file = "tenants-and-macs.json"
    default_structure = {"tenantsAndMacs": []}
    
    ts = datetime.now().isoformat(timespec="seconds")
    print(f"{ts}  Clearing tenants-and-macs.json file...")
    
    try:
        with open(json_file, 'w') as f:
            json.dump(default_structure, f, indent=2)
        print(f"    ✓ Successfully cleared {json_file}")
        return True
    except IOError as e:
        print(f"    ⚠ Error clearing {json_file}: {e}")
        return False

def delete_all_paired_devices():
    """Delete all paired Bluetooth devices."""
    
    ts = datetime.now().isoformat(timespec="seconds")
    print(f"{ts}  Finding paired Bluetooth devices...")
    print("=" * 50)
    
    # Get list of paired devices
    paired_devices, error = get_paired_devices()
    
    if error:
        print(f"⚠ {error}")
        return False
    
    if not paired_devices:
        print("No paired devices found.")
        print()
        # Still clear the tenants file even if no paired devices
        tenants_cleared = clear_tenants_and_macs_file()
        return tenants_cleared
    
    # Show what will be deleted
    print(f"Found {len(paired_devices)} paired device(s):")
    print()
    for i, (mac, name) in enumerate(paired_devices, 1):
        print(f"{i:2d}. {name}")
        print(f"    MAC: {mac}")
        print()
    

    
    print()
    print("=" * 50)
    print("Starting device removal...")
    print("=" * 50)
    
    # Remove each device
    success_count = 0
    for mac_address, device_name in paired_devices:
        if remove_device(mac_address, device_name):
            success_count += 1
    
    print()
    print("=" * 50)
    ts = datetime.now().isoformat(timespec="seconds")
    print(f"{ts}  Removal completed: {success_count}/{len(paired_devices)} devices successfully removed")
    
    # Clear the tenants-and-macs.json file
    print()
    tenants_cleared = clear_tenants_and_macs_file()
    
    return success_count == len(paired_devices) and tenants_cleared

def main():
    print("Bluetooth Paired Device Removal + Tenant Cleanup")
    print("===============================================")
    print("• Removes all paired Bluetooth devices")
    print("• Clears tenants-and-macs.json file")
    print()
    
    success = delete_all_paired_devices()
    
    if success:
        print("\n✓ All operations completed successfully")
        sys.exit(0)
    else:
        print("\n⚠ Some operations failed")
        sys.exit(1)

if __name__ == "__main__":
    main()
