# Monarch Bluetooth POC Scripts

Related repositories:
- [monarch-bluetooth-poc-scripts](https://github.com/runslikebutter/monarch-bluetooth-poc-scripts)
- [bluetooth-easy-entry-ios-poc](https://github.com/runslikebutter/bluetooth-easy-entry-ios-poc)

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

## Final Step

Install the appropriate branch of Monarch to complete the setup.