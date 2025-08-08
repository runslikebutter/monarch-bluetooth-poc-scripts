#!/usr/bin/env python3
# show-paired-devices.py
#
# Shows paired Bluetooth devices using bluetoothctl
# Equivalent to running: bluetoothctl -> paired-devices
#
# Usage: python3 show-paired-devices.py

import subprocess
import sys
from datetime import datetime

def show_paired_devices():
    """Show all paired Bluetooth devices using bluetoothctl."""
    
    ts = datetime.now().isoformat(timespec="seconds")
    print(f"{ts}  Showing paired Bluetooth devices...")
    print("=" * 50)
    
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
            # Parse and display the output
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
            
            if paired_devices:
                print(f"Found {len(paired_devices)} paired device(s):")
                print()
                for i, (mac, name) in enumerate(paired_devices, 1):
                    print(f"{i:2d}. {name}")
                    print(f"    MAC: {mac}")
                    print()
            else:
                print("No paired devices found.")
                
        else:
            print(f"⚠ Error running bluetoothctl (return code: {process.returncode})")
            if stderr:
                print(f"Error output: {stderr}")
                
    except subprocess.TimeoutExpired:
        print("⚠ Command timed out")
        process.kill()
    except FileNotFoundError:
        print("⚠ bluetoothctl not found. Make sure BlueZ is installed.")
    except Exception as e:
        print(f"⚠ Error: {e}")
    
    ts = datetime.now().isoformat(timespec="seconds")
    print(f"{ts}  Done.")

def main():
    print("Bluetooth Paired Devices")
    print("========================")
    print()
    
    show_paired_devices()

if __name__ == "__main__":
    main()
