#!/usr/bin/env python3
"""
Proximity sensor WebSocket broadcaster
-------------------------------------
• Reads proximity data from /sys/class/input/event2/device/range_millimeter
• Parses the two numbers and broadcasts them as JSON: { prox1: [number], prox2: [number] }
• Pushes live data to any client connected to ws://localhost:8769
• Configurable broadcast rate
"""

import asyncio
import json
import subprocess
import time

import websockets  # pip install websockets

# ──────────────────────────────────────────────────────────────
# 1. TUNABLE CONSTANTS
# ──────────────────────────────────────────────────────────────
numTimesToBroadcastPerSecond = 10  # How many times per second to read and broadcast
PORT = 8770                       # WebSocket port
SENSOR_PATH = "/sys/class/input/event2/device/range_millimeter"

# ──────────────────────────────────────────────────────────────
# 2. WEBSOCKET BOOK‑KEEPING
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

# ──────────────────────────────────────────────────────────────
# 3. PROXIMITY SENSOR READING
# ──────────────────────────────────────────────────────────────

def read_proximity_data():
    """Read proximity data from the system file and parse the two numbers."""
    try:
        result = subprocess.run(['cat', SENSOR_PATH], 
                              capture_output=True, text=True, timeout=1)
        if result.returncode == 0:
            output = result.stdout.strip()
            numbers = output.split()
            if len(numbers) >= 2:
                prox1 = int(numbers[0])
                prox2 = int(numbers[1])
                return prox1, prox2
            else:
                print(f"⚠️  Unexpected output format: {output}")
                return None, None
        else:
            print(f"⚠️  Error reading sensor: {result.stderr}")
            return None, None
    except subprocess.TimeoutExpired:
        print("⚠️  Timeout reading proximity sensor")
        return None, None
    except Exception as e:
        print(f"⚠️  Exception reading proximity sensor: {e}")
        return None, None

async def broadcast_proximity_data():
    """Read proximity data and broadcast to connected clients."""
    prox1, prox2 = read_proximity_data()
    
    if prox1 is not None and prox2 is not None:
        # Log the proximity data with timestamp
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        print(f"[{timestamp}] 📏 Proximity: prox1={prox1}mm, prox2={prox2}mm")
        
        # Only broadcast if there are connected clients
        if connected_clients:
            payload = json.dumps({"prox1": prox1, "prox2": prox2})
            dead = set()
            for ws in connected_clients:
                try:
                    await ws.send(payload)
                except websockets.exceptions.ConnectionClosed:
                    dead.add(ws)
            for ws in dead:
                await unregister_client(ws)
    else:
        # Log when we can't read valid data
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        print(f"[{timestamp}] ⚠️  Could not read valid proximity data")

# ──────────────────────────────────────────────────────────────
# 4. ASYNC TASKS
# ──────────────────────────────────────────────────────────────

async def periodic_broadcaster():
    """Periodically read proximity data and broadcast to clients."""
    delay = 1.0 / numTimesToBroadcastPerSecond
    while True:
        await broadcast_proximity_data()
        await asyncio.sleep(delay)

# ──────────────────────────────────────────────────────────────
# 5. MAIN EVENT LOOP
# ──────────────────────────────────────────────────────────────

async def main():
    print("📡 Proximity sensor WebSocket server starting…")
    print(f"🌐 WebSocket → ws://localhost:{PORT} (broadcast {numTimesToBroadcastPerSecond} Hz)")
    print(f"📊 Reading from: {SENSOR_PATH}")

    ws_server = await websockets.serve(websocket_handler, "localhost", PORT)
    send_task = asyncio.create_task(periodic_broadcaster())

    try:
        await ws_server.wait_closed()  # waits forever (Ctrl‑C to break)
    finally:
        send_task.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Goodbye!") 

