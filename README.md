# Monarch Bluetooth POC Scripts

Related repositories:
- [monarch-bluetooth-poc-scripts](https://github.com/runslikebutter/monarch-bluetooth-poc-scripts)
- [bluetooth-easy-entry-ios-poc](https://github.com/runslikebutter/bluetooth-easy-entry-ios-poc)

## Overview

This proof-of-concept implements a Bluetooth Low Energy (BLE) based proximity detection system for intercom door entry. The system allows residents to unlock doors automatically when they approach an intercom while carrying their paired smartphone.

### System Architecture

The system consists of four main components running on the intercom hardware:

1. **Advertisement Service** (`advertise-intercom.py`) - Makes the intercom discoverable for pairing
2. **Pairing Scanner** (`scan-beacons.py`) - Handles phone pairing requests  
3. **Proximity Monitor** (`send-rssi-monarch.py`) - Tracks paired phones and calculates proximity
4. **Gesture Detection** (`proximity-send-monarch.py`) - Detects wave gestures using proximity sensor

## Setup

### Install Dependencies

Install the required Python packages:

```bash
pip3 install websockets
pip3 install watchdog
pip3 install bleak
```

### Boot Configuration

The following commands need to be run on boot (consider creating a script for this):

#### Enable Bluetooth

```bash
sudo modprobe hci_uart
sudo hciattach /dev/ttymxc2 any 115200 flow
bluetoothctl
```

Then in bluetoothctl:
```
power on
```

#### Enable Proximity Sensor

```bash
sudo su
cd /sys/class/input/event2/device
echo "200 1 1 3 3000 10 0 0.0 0.0" > /sys/class/input/event2/device/autonomous_config
echo 3 > mode
echo 86400 > timing_budget
echo 1 > enable_ps_sensor
```

Test that the sensor is working:
```bash
cat /sys/class/input/event2/device/range_millimeter
```

## Running the Services

1. Clone this repository and transfer it to the intercom:
   ```bash
   scp -r monarch-bluetooth-poc-scripts user@intercom:~
   ```

2. Open 4 different terminals and navigate to the project directory:
   ```bash
   cd monarch-bluetooth-poc-scripts
   ```

3. Run the following commands in separate terminals (order doesn't matter):

### Service Commands

#### Enable Intercom Advertisement
Allows unpaired phones to discover the intercom:
```bash
python3 advertise-intercom.py
```

#### Enable Phone Pairing
Scans for and allows pairing with phones:
```bash
python3 scan-beacons.py
```

#### RSSI Monitoring
Scans for paired phones and sends RSSI values to Monarch:
```bash
python3 send-rssi-monarch.py
```

#### Proximity Sensor Monitoring
Reads proximity sensor values and sends them to Monarch:
```bash
sudo python3 proximity-send-monarch.py
```

## Technical Documentation

### 1. Phone Pairing Process

The pairing system uses a two-step process designed to securely associate tenant phones with the intercom.

#### Step 1: Intercom Advertisement (`advertise-intercom.py`)

**Purpose**: Makes the intercom discoverable by unpaired phones during the pairing process.

**Technical Details**:
- Advertises a BLE GATT service with UUID `E7B2C021-5D07-4D0B-9C20-223488C8B012`
- Uses local name "Intercom" for identification
- Includes a writable characteristic for receiving pairing data from the mobile app
- Can run continuously or be triggered via WebSocket for enhanced security

**Current POC Behavior**:
- Always advertising (simplified for proof-of-concept)
- Phones see only the manufacturing data in scan results
- Mobile app filters for the specific service UUID

**Production Considerations**:
1. **Security Enhancement**: Implement WebSocket-triggered advertising
   - User taps "Pair" in mobile app
   - Server sends "start advertising" command to specific intercoms
   - Advertising stops after timeout or successful pairing
2. **Device Identification**: Include intercom name/location in advertisement data (limited to ~20 bytes)

#### Step 2: Phone Discovery and Pairing (`scan-beacons.py`)

**Purpose**: Listens for phones attempting to pair and handles the Bluetooth pairing process.

**Current POC Flow**:
1. User enters tenant ID directly in mobile app (POC shortcut)
2. User taps "Pair" in mobile app
3. Phone advertises with local name `BMX_P[TENANT_ID]` for 10 seconds
4. Intercom scans for advertisements starting with `BMX_P`
5. Extracts tenant ID from advertisement name
6. Initiates Bluetooth pairing via `bluetoothctl`
7. Stores mapping of tenant ID → identity MAC address

**Technical Implementation**:
- Uses Bleak scanner with active scanning mode
- Implements anti-spam protection (15-second minimum between attempts)
- Captures device's **identity MAC** address during pairing (not the randomized MAC)
- BlueZ automatically resolves future randomized MACs using stored IRK (Identity Resolving Key)

**Production Architecture** (Recommended):
1. User taps "Pair" for specific intercom in mobile app
2. Server generates short token (<10 chars) and long token for security
3. App advertises `BMX_P[SHORT_TOKEN]` 
4. Intercom receives token from server via WebSocket
5. When tokens match, intercom initiates pairing
6. Both devices verify long token for additional security
7. System validates tenant has building access

**Pairing Data Storage**:
```json
{
  "tenantsAndMacs": [
    {
      "id": "tenant_id",
      "mac": "70:22:FE:03:C1:41"
    }
  ]
}
```

### 2. Proximity Detection System (`send-rssi-monarch.py`)

This is the core component that enables automatic door unlocking based on phone proximity.

#### RSSI-Based Proximity Algorithm

**Why RSSI?** Radio Signal Strength Indicator (RSSI) values correlate with distance, but are notoriously noisy. Raw RSSI readings can vary dramatically even with minimal movement.

**Example Raw RSSI Pattern**:
```
Close approach: -80, -79, -83, -65, -63, -87, -64, -59, -79, -58
```

The system uses **Exponential Weighted Moving Average (EWMA)** to smooth these readings:

```python
# Dynamic ALPHA based on context
ALPHA = 0.8  # When no one near (fast response)
ALPHA = 0.3  # When someone near (smooth tracking)

# EWMA calculation
ewma = ALPHA * new_rssi + (1 - ALPHA) * previous_ewma
```

#### Two-Threshold Hysteresis System

To prevent "flapping" between near/far states, the system uses different thresholds:

- **Enter Threshold**: -65 dBm (easier to enter "near" state)
- **Exit Threshold**: -69 dBm (harder to exit "near" state)

#### Packet Window Validation

Beyond RSSI smoothing, the system requires consistent signal presence:

- **Window**: 4-second rolling window
- **Minimum Packets**: 4 packets required in window
- **Purpose**: Prevents single strong packets from triggering false positives

#### Tenant Tracking Data Structure

```python
{
    "macAddress": "70:22:FE:03:C1:41",
    "tenantId": "123456",
    "ewma": -62.3,                    # Smoothed RSSI
    "packetTimes": deque([...]),      # Rolling window timestamps  
    "isNear": True,                   # Current proximity state
    "lastSeenTs": 1699123456.78,      # Last packet timestamp
    "extraRssis": [-61, -65, -59]     # Raw RSSI since last broadcast
}
```

#### WebSocket Broadcasting

The system broadcasts proximity data at 5 Hz to Monarch via WebSocket (port 8769):

```json
[
  {
    "macAddress": "70:22:FE:03:C1:41", 
    "tenantId": "123456",
    "ewma": -62.3,
    "isNear": true,
    "lastSeenTs": "2023-11-04T15:30:45",
    "packetCount": 7,
    "extraRssis": [-61, -65, -59]
  }
]
```

#### Logo Brightness Control

The system provides visual feedback by controlling the intercom's logo LED:

- **Brightness Range**: 10 (minimum) to 255 (maximum)
- **Behavior**: Brightens when anyone is near, dims when alone
- **Hardware Interface**: `/sys/class/leds/ledlogo/brightness`

### 3. Wave Detection (`proximity-send-monarch.py`)

**Purpose**: Detects intentional gestures to trigger door unlocking.

**Technical Implementation**:
- Reads from `/sys/class/input/event2/device/range_millimeter`
- Broadcasts two proximity values at 10 Hz via WebSocket (port 8770)
- Simple threshold-based detection (configured in Monarch)

**Data Format**:
```json
{
  "prox1": 245,  // millimeters
  "prox2": 389   // millimeters  
}
```

**Production Alternatives**:
1. **Face Detection**: Use camera to detect presence and attention
2. **Touch Interface**: Tap gesture on intercom screen
3. **No Secondary Gesture**: Auto-unlock on proximity (less secure)

### 4. System Integration with Monarch

#### Door Unlocking Logic (in Monarch)

1. **Proximity Detection**: Monitor nearby tenants via WebSocket from `send-rssi-monarch.py`
2. **Wave Detection**: Listen for gesture from `proximity-send-monarch.py`  
3. **Tenant Selection**: When wave detected, find tenant with strongest RSSI (closest to 0)
4. **Authorization**: Verify tenant has building access
5. **Door Release**: Activate door unlock mechanism

#### WebSocket Architecture

```
┌─────────────────┐    WS:8769     ┌─────────────────┐
│ send-rssi-      ├───────────────►│                 │
│ monarch.py      │   (proximity)  │                 │
└─────────────────┘                │                 │
                                   │    Monarch      │
┌─────────────────┐    WS:8770     │   (Decision     │
│ proximity-send- ├───────────────►│    Engine)      │
│ monarch.py      │   (gestures)   │                 │
└─────────────────┘                └─────────────────┘

┌─────────────────┐    WS:8771
│ scan-beacons.py ├───────────────► (pairing events)
└─────────────────┘
```

### 5. Security Considerations

#### Current POC Limitations
- Tenant ID transmitted in plaintext via BLE advertisement names
- No server-side validation of pairing requests
- Simple proximity-based unlocking

#### Production Security Enhancements
1. **Encrypted Pairing Tokens**: Replace plaintext tenant IDs with encrypted tokens
2. **Server-Mediated Pairing**: All pairing requests validated by backend
3. **Time-Limited Sessions**: Pairing tokens expire after short duration
4. **Building-Scoped Access**: Validate tenant building access before storing pairing
5. **Audit Logging**: Track all pairing and unlock events

### 6. Performance Tuning

#### RSSI Algorithm Parameters

**Adjustable Constants** (in `send-rssi-monarch.py`):
```python
ENTER_THRESHOLD = -65    # dBm - easier entry
EXIT_THRESHOLD = -69     # dBm - harder exit  
ALPHA = 0.8             # EWMA smoothing factor
WINDOW_SEC = 4          # packet window duration
PACKETS_REQUIRED = 4    # minimum packets in window
BROADCAST_HZ = 5        # WebSocket update frequency
```

**Tuning Guidelines**:
- **Tighter thresholds** (-60/-65): Requires closer approach but more reliable
- **Higher ALPHA** (0.8-0.9): Faster response but more noise sensitivity
- **Longer windows** (5-6 sec): More stable but slower to detect departures
- **More packets required** (5-6): Reduces false positives but needs stronger signal

#### Hardware-Specific Calibration

RSSI values vary significantly by:
- **Hardware antenna design**
- **Phone model and orientation** 
- **Environmental interference**
- **Physical obstacles**

Each deployment requires empirical testing to determine optimal thresholds.

## Final Step

Install the appropriate branch of Monarch to complete the setup.

## Development Notes

### File Structure
```
tenants-and-macs.json    # Pairing storage (created by scan-beacons.py)
advertise-intercom.py    # BLE advertisement service
scan-beacons.py          # Pairing handler + WebSocket server (port 8771)
send-rssi-monarch.py     # Proximity monitor + WebSocket server (port 8769)  
proximity-send-monarch.py # Gesture detection + WebSocket server (port 8770)
```

### Debugging
- Set `DEBUG = True` in `scan-beacons.py` for verbose bluetoothctl output
- Monitor WebSocket connections: `wscat -c ws://localhost:8769`
- Check sensor readings: `cat /sys/class/input/event2/device/range_millimeter`
- View current pairings: `python3 show-paired-devices.py`