#!/usr/bin/env python3
# advertise_intercom_fixed.py   – works with dbus‑next ≥ 0.2

import asyncio, sys, json
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, dbus_property, PropertyAccess, method
from dbus_next import BusType, Variant

ADAPTER   = sys.argv[1] if len(sys.argv) > 1 else "hci0"
UUID      = "E7B2C021-5D07-4D0B-9C20-223488C8B012"  # Changed to match iOS intercom scanning
CHAR_UUID = "E7B2C021-5D07-4D0B-9C20-223488C8B012"  # Data characteristic UUID
LOCALNAME = "Intercom"

BLUEZ     = "org.bluez"
ADV_MGR   = "org.bluez.LEAdvertisingManager1"
GATT_MGR  = "org.bluez.GattManager1"
ADV_PATH  = "/com/intercom/adv0"
APP_PATH  = "/com/intercom"
SERVICE_PATH = "/com/intercom/service0"
CHAR_PATH = "/com/intercom/service0/char0"

class Advertisement(ServiceInterface):
    def __init__(self):
        super().__init__("org.bluez.LEAdvertisement1")

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> "s":
        return "peripheral"

    @dbus_property(access=PropertyAccess.READ)
    def ServiceUUIDs(self) -> "as":
        return [UUID]

    @dbus_property(access=PropertyAccess.READ)
    def LocalName(self) -> "s":
        return LOCALNAME

    @dbus_property(access=PropertyAccess.READ)
    def IncludeTxPower(self) -> "b":
        return True

    @method()
    def Release(self):
        print("[+] Advertisement released by BlueZ")

class GattApplication(ServiceInterface):
    def __init__(self):
        super().__init__("org.freedesktop.DBus.ObjectManager")

    @method()
    def GetManagedObjects(self) -> "a{oa{sa{sv}}}":
        return {
            SERVICE_PATH: {
                "org.bluez.GattService1": {
                    "UUID": Variant("s", UUID),
                    "Primary": Variant("b", True),
                    "Characteristics": Variant("ao", [CHAR_PATH])
                }
            },
            CHAR_PATH: {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", CHAR_UUID),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant("as", ["write", "write-without-response"]),
                    "Value": Variant("ay", [])
                }
            }
        }

class IntercomService(ServiceInterface):
    def __init__(self):
        super().__init__("org.bluez.GattService1")

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return UUID

    @dbus_property(access=PropertyAccess.READ)
    def Primary(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def Characteristics(self) -> "ao":
        return [CHAR_PATH]

class DataCharacteristic(ServiceInterface):
    def __init__(self):
        super().__init__("org.bluez.GattCharacteristic1")
        self.value = b""

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return CHAR_UUID

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> "o":
        return SERVICE_PATH

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> "as":
        return ["write", "write-without-response", "read"]

    @dbus_property(access=PropertyAccess.READ)
    def Value(self) -> "ay":
        return self.value

    @method()
    def WriteValue(self, value: "ay", options: "a{sv}"):
        """Handle incoming data from iOS app"""
        try:
            # Convert byte array to string
            data_str = bytes(value).decode('utf-8')
            print(f"\n🔔 [INTERCOM] Received data from iOS:")
            print(f"📱 Raw data: {data_str}")
            
            # Try to parse as JSON
            try:
                json_data = json.loads(data_str)
                print(f"📋 Parsed JSON:")
                for key, val in json_data.items():
                    print(f"    {key}: {val}")
                
                # Log specific fields if they exist
                if 'tenantId' in json_data:
                    print(f"🏢 Tenant ID: {json_data['tenantId']}")
                if 'name' in json_data:
                    print(f"👤 Name: {json_data['name']}")
                if 'timestamp' in json_data:
                    print(f"⏰ Timestamp: {json_data['timestamp']}")
                if 'source' in json_data:
                    print(f"📱 Source: {json_data['source']}")
                    
            except json.JSONDecodeError:
                print(f"⚠️  Not valid JSON, treating as plain text")
            
            # Store the value
            self.value = value
            print(f"✅ Data successfully received and stored\n")
            
        except Exception as e:
            print(f"❌ Error processing data: {e}")

    @method()
    def StartNotify(self):
        print("[+] Notifications started for data characteristic")

    @method()
    def StopNotify(self):
        print("[+] Notifications stopped for data characteristic")

async def main():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    # Create and export all objects
    advertisement = Advertisement()
    app = GattApplication()
    service = IntercomService()
    characteristic = DataCharacteristic()
    
    # Export objects to D-Bus
    bus.export(ADV_PATH, advertisement)
    bus.export(APP_PATH, app)
    bus.export(SERVICE_PATH, service)
    bus.export(CHAR_PATH, characteristic)

    # Get adapter
    adapter_obj = bus.get_proxy_object(
        "org.bluez",
        f"/org/bluez/{ADAPTER}",
        await bus.introspect(BLUEZ, f"/org/bluez/{ADAPTER}")
    )
    
    # Register GATT application first
    gatt_mgr = adapter_obj.get_interface(GATT_MGR)
    await gatt_mgr.call_register_application(APP_PATH, {})
    print("[+] GATT application registered")
    
    # Then start advertising
    adv_mgr = adapter_obj.get_interface(ADV_MGR)
    await adv_mgr.call_register_advertisement(ADV_PATH, {})
    print("[+] Advertising started...")
    print(f"[+] Service UUID: {UUID}")
    print(f"[+] Characteristic UUID: {CHAR_UUID}")
    print("[+] Ready to receive data from iOS app!")
    print("[+] Press Ctrl-C to stop")

    try:
        await asyncio.Future()
    finally:
        print("\n[+] Shutting down...")
        try:
            await adv_mgr.call_unregister_advertisement(ADV_PATH)
            await gatt_mgr.call_unregister_application(APP_PATH)
        except:
            pass
        print("[+] Cleanup complete")

if __name__ == "__main__":
    asyncio.run(main())
