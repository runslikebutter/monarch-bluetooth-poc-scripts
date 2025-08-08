#!/usr/bin/env python3
"""
Intercom proximity detector + WebSocket broadcaster
--------------------------------------------------
• Listens for BLE advertisements from known phones.
• Decides "NEAR" vs "FAR" with two criteria:
    1. Smoothed RSSI (so a single loud packet doesn't fool us).
    2. A rolling 3‑second window that must contain >= 3 packets.
• Pushes live status to any client connected to ws://localhost:8769.
• Dynamically loads tenant list from tenants-and-macs.json file.
• Watches for file changes and updates tenant list automatically.

Requirements: pip install websockets bleak watchdog

Everything is heavily commented so you can skim top‑to‑bottom without
looking anything up. Feel free to tweak the CONSTS section.
"""

import asyncio
import json
import time
import subprocess
import os
from collections import deque

import websockets  # pip install websockets
from bleak import BleakScanner  # pip install bleak
from watchdog.observers import Observer  # pip install watchdog
from watchdog.events import FileSystemEventHandler

# ──────────────────────────────────────────────────────────────
# 1. TUNABLE CONSTANTS
# ──────────────────────────────────────────────────────────────
ENTER_THRESHOLD = -65          # dB — average RSSI required to say "near"
# Why a smaller (ABS) exit threshold than enter?
# Enter -> Easier entry / lightup. They'll then be at like -50 when up close to the intercom - it will jump to that.
# Exit -> Easier exit - it will probably jump from -64 to -72 for instance.
EXIT_THRESHOLD  = -69          # dB — drop‑back point so we don't flap
# The knob that controls how quickly the average climbs or drops is ALPHA:
# Higher ALPHA (e.g. 0.7‑0.8) → the newest packet counts more → the EWMA chases changes faster (1‑2 s in/out instead of 3‑5 s).
# Lower ALPHA (e.g. 0.3) → smoother but sluggish.
# ALPHA is now dynamic: 0.3 when someone is near (smoother), 0.8 when no one is near (faster response)
ALPHA           = 0.8          # 0‑1 — weight of *new* RSSI in EWMA (initial value)
WINDOW_SEC      = 4            # seconds — how far back we keep packet times
PACKETS_REQUIRED = 4           # need at least this many packets in window
BROADCAST_HZ    = 5            # JSON pushes per second
PORT            = 8769         # WebSocket port

# Logo brightness constants
MIN_BRIGHTNESS = 10            # minimum logo brightness
MAX_BRIGHTNESS = 255           # maximum logo brightness  
BRIGHTNESS_ADJUSTMENT_RATE = 30 # how much to adjust brightness per interval

# Tenant broadcast timeout
TENANT_TIMEOUT_SEC = 10        # seconds - don't broadcast tenants not seen for this long

# Current brightness state
current_brightness = MIN_BRIGHTNESS  # start at minimum

# ──────────────────────────────────────────────────────────────
# 2. DYNAMIC TENANT MANAGEMENT
#    Read from tenants-and-macs.json and watch for changes
# ──────────────────────────────────────────────────────────────

# Global variables for tenant data
tenantsAndMacs = {"tenantsAndMacs": []}  # Raw data from JSON file
knownTenants = []  # Active tenants with BLE tracking data
JSON_FILE = "tenants-and-macs.json"
main_loop = None  # Reference to main event loop for thread-safe async calls

def read_tenants_and_macs_file():
    """Read the tenants-and-macs.json file or return default structure."""
    try:
        if os.path.exists(JSON_FILE):
            with open(JSON_FILE, 'r') as f:
                data = json.load(f)
                # Ensure the structure is correct
                if 'tenantsAndMacs' not in data:
                    data = {"tenantsAndMacs": []}
                return data
        else:
            # File doesn't exist, return default structure
            print(f"📄 {JSON_FILE} not found, starting with empty tenant list")
            return {"tenantsAndMacs": []}
    except (json.JSONDecodeError, IOError) as e:
        print(f"⚠️ Error reading {JSON_FILE}: {e}")
        return {"tenantsAndMacs": []}

def sync_known_tenants():
    """Sync knownTenants with the current tenantsAndMacs data."""
    global knownTenants
    
    # Create a map of existing tenants by MAC to preserve their runtime data
    existing_by_mac = {t["macAddress"].upper(): t for t in knownTenants}
    
    # Build new knownTenants list based on tenantsAndMacs
    new_known_tenants = []
    
    for tenant_data in tenantsAndMacs["tenantsAndMacs"]:
        mac = tenant_data["mac"].upper()
        tenant_id = tenant_data["id"]
        
        # Check if we already have this tenant (preserve runtime data)
        if mac in existing_by_mac:
            existing_tenant = existing_by_mac[mac]
            # Update tenant ID in case it changed
            existing_tenant["tenantId"] = tenant_id
            new_known_tenants.append(existing_tenant)
            print(f"🔄 Updated tenant {tenant_id} (MAC: {mac})")
        else:
            # New tenant - create fresh entry
            new_tenant = {
                "macAddress": mac,
                "tenantId": tenant_id,
                # Live state (filled in at runtime)
                "ewma": None,
                "packetTimes": deque(),
                "isNear": False,
                "lastSeenTs": None,
                "extraRssis": [],  # Track RSSI values since last broadcast
            }
            new_known_tenants.append(new_tenant)
            print(f"➕ Added new tenant {tenant_id} (MAC: {mac})")
    
    # Check for removed tenants
    current_macs = {tenant_data["mac"].upper() for tenant_data in tenantsAndMacs["tenantsAndMacs"]}
    for old_tenant in knownTenants:
        if old_tenant["macAddress"].upper() not in current_macs:
            print(f"➖ Removed tenant {old_tenant['tenantId']} (MAC: {old_tenant['macAddress']})")
    
    # Update the global knownTenants
    knownTenants = new_known_tenants
    print(f"📋 Tenant list updated: {len(knownTenants)} tenants active")
    
    # Show current scan targets
    if knownTenants:
        addrs = ", ".join(t["macAddress"] for t in knownTenants)
        print(f"🎯 Now scanning for: {addrs}")
    else:
        print(f"🎯 No active scan targets")

def load_tenants_from_file():
    """Load tenants from file and sync with knownTenants."""
    global tenantsAndMacs
    new_data = read_tenants_and_macs_file()
    
    # Only update if data actually changed
    if new_data != tenantsAndMacs:
        tenantsAndMacs = new_data
        print(f"📂 Loaded {len(tenantsAndMacs['tenantsAndMacs'])} tenants from {JSON_FILE}")
        sync_known_tenants()
        return True
    return False

# File watcher class
class TenantsFileHandler(FileSystemEventHandler):
    """Watch for changes to the tenants-and-macs.json file."""
    
    def on_modified(self, event):
        if event.is_directory:
            return
        if os.path.basename(event.src_path) == JSON_FILE:
            print(f"📁 Detected change in {JSON_FILE}")
            # Schedule async reload in main event loop (thread-safe)
            try:
                if main_loop and not main_loop.is_closed():
                    asyncio.run_coroutine_threadsafe(self._delayed_reload(), main_loop)
                else:
                    print(f"⚠️ Cannot reload - main event loop not available")
            except Exception as e:
                print(f"⚠️ Error scheduling file reload: {e}")
    
    async def _delayed_reload(self):
        try:
            await asyncio.sleep(0.1)  # Brief delay to ensure file write is complete
            load_tenants_from_file()
        except Exception as e:
            print(f"⚠️ Error reloading tenants file: {e}")

# ──────────────────────────────────────────────────────────────
# 3. WEBSOCKET BOOK‑KEEPING
# ──────────────────────────────────────────────────────────────
connected_clients: set[websockets.WebSocketServerProtocol] = set()

async def register_client(ws):
    connected_clients.add(ws)
    print(f"🔵 CLIENT +1 (now {len(connected_clients)}) {ws.remote_address}")

async def unregister_client(ws):
    connected_clients.discard(ws)
    print(f"🔴 CLIENT -1 (now {len(connected_clients)})")

async def websocket_handler(ws):
    await register_client(ws)
    try:
        async for msg in ws:  # we don't *expect* messages, but log anyway
            print(f"📥 From client: {msg}")
    finally:
        await unregister_client(ws)

# Helper: make JSON‑safe copy (deques aren't JSON‑able)
def _serialize_tenants():
    serial = []
    for t in knownTenants:
        # Filter out tenants that haven't been seen for more than TENANT_TIMEOUT_SEC
        if t["lastSeenTs"] is not None and time.time() - t["lastSeenTs"] > TENANT_TIMEOUT_SEC:
            continue

        copy = t.copy()
        copy["packetCount"] = len(copy["packetTimes"])
        copy.pop("packetTimes", None)
        
        # Include extraRssis if there are any, then clear them
        if t["extraRssis"]:
            copy["extraRssis"] = t["extraRssis"].copy()
            t["extraRssis"].clear()  # Clear after copying for next broadcast
        
        serial.append(copy)
    return serial

def adjustLogoBrightness(direction):
    """Adjust logo brightness up or down within min/max bounds."""
    global current_brightness
    
    if direction == "up":
        new_brightness = min(current_brightness + BRIGHTNESS_ADJUSTMENT_RATE, MAX_BRIGHTNESS)
        if new_brightness != current_brightness:
            current_brightness = new_brightness
            print(f"💡 Logo brightness UP: {current_brightness}")
            subprocess.run(f"echo {current_brightness} > /sys/class/leds/ledlogo/brightness", shell=True)
    elif direction == "down":
        new_brightness = max(current_brightness - (BRIGHTNESS_ADJUSTMENT_RATE * 2), MIN_BRIGHTNESS)
        if new_brightness != current_brightness:
            current_brightness = new_brightness
            print(f"🔅 Logo brightness DOWN: {current_brightness}")
            subprocess.run(f"echo {current_brightness} > /sys/class/leds/ledlogo/brightness", shell=True)
    else:
        print(f"⚠️  Unknown brightness direction: {direction}")

async def broadcast_tenants():
    if not connected_clients:
        return
    payload = json.dumps(_serialize_tenants(), default=str)
    dead = set()
    for ws in connected_clients:
        try:
            await ws.send(payload)
        except websockets.exceptions.ConnectionClosed:
            dead.add(ws)
    for ws in dead:
        await unregister_client(ws)

# ──────────────────────────────────────────────────────────────
# 4. BLE CALLBACK — HEARTBEAT OF THE APP
# ──────────────────────────────────────────────────────────────

def on_ble_packet(device, adv):
    """Called by Bleak for every advertisement we see."""
    for t in knownTenants:
        if device.address.upper() != t["macAddress"].upper():
            continue  # not our guy; keep looping

        now = time.time()
        rssi = device.rssi

        # 1. EWMA smoothing (seed with first sample)
        if t["ewma"] is None:
            t["ewma"] = rssi
        else:
            t["ewma"] = ALPHA * rssi + (1 - ALPHA) * t["ewma"]

        # 2. Rolling packet window — append now, drop anything older than WINDOW_SEC
        t["packetTimes"].append(now)
        while t["packetTimes"] and now - t["packetTimes"][0] > WINDOW_SEC:
            t["packetTimes"].popleft()
        pkt_count = len(t["packetTimes"])

        # 2.5. Collect RSSI for broadcast tracking
        t["extraRssis"].append(rssi)

        # 3. Two‑state machine with hysteresis
        if not t["isNear"]:
            # Currently FAR — can we switch to NEAR?
            if t["ewma"] >= ENTER_THRESHOLD and pkt_count >= PACKETS_REQUIRED:
                t["isNear"] = True
                print(f"✅ {t['tenantId']} is NEAR  (EWMA {t['ewma']:.1f} dB, pkts {pkt_count})")
        else:
            # Currently NEAR — should we fall back to FAR?
            if t["ewma"] < EXIT_THRESHOLD or pkt_count < PACKETS_REQUIRED:
                t["isNear"] = False
                print(f"⬅️ {t['tenantId']} went FAR (EWMA {t['ewma']:.1f} dB, pkts {pkt_count})")

        # 4. Always update last‑seen timestamp
        t["lastSeenTs"] = now
        break  # matched tenant found; done

# ──────────────────────────────────────────────────────────────
# 5. ASYNC TASKS
# ──────────────────────────────────────────────────────────────

async def bluetooth_scanner():
    if knownTenants:
        addrs = ", ".join(t["macAddress"] for t in knownTenants)
        print(f"🔍 Scanning for: {addrs}")
    else:
        print(f"🔍 BLE scanner ready (no tenants loaded yet)")
    
    scanner = BleakScanner(on_ble_packet, scanning_mode="active")
    await scanner.start()
    try:
        await asyncio.Future()  # sleep forever
    finally:
        await scanner.stop()

async def periodic_broadcaster():
    global ALPHA
    delay = 1.0 / BROADCAST_HZ
    while True:
        await broadcast_tenants()
        
        # Check if anyone is near and adjust logo brightness only if needed
        anyone_near = any(t["isNear"] for t in knownTenants)
        
        # Adjust ALPHA based on proximity: smoother when near, faster when far
        if anyone_near and ALPHA != 0.3:
            ALPHA = 0.3
            print(f"🎯 ALPHA adjusted to {ALPHA} (someone is near - smoother tracking)")
        elif not anyone_near and ALPHA != 0.8:
            ALPHA = 0.8
            print(f"🎯 ALPHA adjusted to {ALPHA} (no one near - faster response)")
        
        if anyone_near and current_brightness < MAX_BRIGHTNESS:
            adjustLogoBrightness("up")
        elif not anyone_near and current_brightness > MIN_BRIGHTNESS:
            adjustLogoBrightness("down")
        
        await asyncio.sleep(delay)

async def file_watcher_task():
    """Set up file watching for tenants-and-macs.json changes."""
    event_handler = TenantsFileHandler()
    observer = Observer()
    
    # Watch the current directory for changes to our JSON file
    watch_path = os.path.dirname(os.path.abspath(JSON_FILE)) or "."
    observer.schedule(event_handler, watch_path, recursive=False)
    
    observer.start()
    print(f"👁️ Watching {JSON_FILE} for changes...")
    
    try:
        await asyncio.Future()  # Run forever
    except asyncio.CancelledError:
        observer.stop()
        observer.join()
        raise

# ──────────────────────────────────────────────────────────────
# 6. MAIN EVENT LOOP
# ──────────────────────────────────────────────────────────────

async def main():
    global main_loop
    main_loop = asyncio.get_running_loop()  # Store reference for thread-safe async calls
    
    print("🚪 Intercom proximity server starting…")
    
    # Load tenants from file on startup
    load_tenants_from_file()
    
    # Show loaded tenants
    if knownTenants:
        for t in knownTenants:
            print(f"   • Watching {t['tenantId']} ({t['macAddress']})")
    else:
        print("   • No tenants loaded - waiting for tenants-and-macs.json updates")
    
    print(f"🌐 WebSocket → ws://localhost:{PORT} (broadcast {BROADCAST_HZ} Hz)")
    print("⚙️  ENTER ≥", ENTER_THRESHOLD, "EXIT <", EXIT_THRESHOLD,
          f"| window {WINDOW_SEC}s need {PACKETS_REQUIRED} pkts")

    # Start all async tasks
    ws_server = await websockets.serve(websocket_handler, "localhost", PORT)
    scan_task = asyncio.create_task(bluetooth_scanner())
    send_task = asyncio.create_task(periodic_broadcaster())
    watcher_task = asyncio.create_task(file_watcher_task())

    try:
        await ws_server.wait_closed()  # waits forever (Ctrl‑C to break)
    finally:
        scan_task.cancel()
        send_task.cancel()
        watcher_task.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Goodbye!")
