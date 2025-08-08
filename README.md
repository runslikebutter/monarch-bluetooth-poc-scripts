# monarch-bluetooth-poc-scripts

https://github.com/runslikebutter/monarch-bluetooth-poc-scripts
https://github.com/runslikebutter/bluetooth-easy-entry-ios-poc

# To get set up
1. install the pip3 packages
`pip3 install websockets`
`pip3 install watchdog`
`pip3 install bleak`

ON BOOT (we could make a script for this):

### Enable bluetooth

sudo modprobe hci_uart
sudo hciattach /dev/ttymxc2 any 115200 flow
bluetoothctl
THEN: power on

### Enable proximity sensor
sudo su
cd /sys/class/input/event2/device
echo "200 1 1 3 3000 10 0 0.0 0.0" > /sys/class/input/event2/device/autonomous_config
echo 3 > mode
echo 86400 > timing_budget
echo 1 > enable_ps_sensor
cat /sys/class/input/event2/device/range_millimeter # test that it gives you a value

## Run the Python services
1. Clone the repo and then scp it to ~ on the intercom
2. Open up 4 different terminals and cd monarch-bluetooth-poc-scripts 
3. Run command in each one (it does not matter which order):

## Enable the intercom to advertise so unpaired phones can see it
python3 advertise_intercom.py

## Allow for pairing of phones
python3 scan-beacons.py

## Scan for paired phones and send their calculated RSSI values to Monarch
python3 send-rssi-monarch.py

## Get the proximity sensor values and send them to Monarch
sudo python3 proximity-send-monarch.py

### Install Monarch
Last step is to install this branch of Monarch.