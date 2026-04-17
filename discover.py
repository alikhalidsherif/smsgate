# /// script
# dependencies = ["huawei-lte-api"]
# ///

"""
Probe every known API endpoint on the device and report what responds vs what errors.
Helps discover what the E5331 actually supports.
Run: uv run discover.py 2>/dev/null
"""

import logging
import os

from huawei_lte_api.Client import Client
from huawei_lte_api.Connection import Connection

logging.basicConfig(level=logging.WARNING)

ROUTER_URL = os.environ.get("ROUTER_URL", "http://192.168.8.1")
ROUTER_USER = os.environ.get("ROUTER_USER", "admin")
ROUTER_PASS = os.environ.get("ROUTER_PASS", "")

PROBES = [
    ("device.information", lambda c: c.device.information()),
    ("device.basic_information", lambda c: c.device.basic_information()),
    ("device.boot_time", lambda c: c.device.boot_time()),
    ("device.signal", lambda c: c.device.signal()),
    ("monitoring.status", lambda c: c.monitoring.status()),
    ("monitoring.traffic_statistics", lambda c: c.monitoring.traffic_statistics()),
    ("monitoring.month_statistics", lambda c: c.monitoring.month_statistics()),
    ("net.net_mode", lambda c: c.net.net_mode()),
    ("net.net_mode_list", lambda c: c.net.net_mode_list()),
    ("net.current_plmn", lambda c: c.net.current_plmn()),
    ("net.network", lambda c: c.net.network()),
    ("sms.sms_count", lambda c: c.sms.sms_count()),
    ("dial_up.mobile_dataswitch", lambda c: c.dial_up.mobile_dataswitch()),
    ("dial_up.connection", lambda c: c.dial_up.connection()),
    ("dial_up.profiles", lambda c: c.dial_up.profiles()),
    ("wlan.wifi_profile", lambda c: c.wlan.wifi_profile()),
    ("wlan.wifi_clients", lambda c: c.wlan.wifi_clients()),
    ("wlan.multi_basic_settings", lambda c: c.wlan.multi_basic_settings()),
    ("ussd.status", lambda c: c.ussd.status()),
]


def main():
    print("Probing E5331 capabilities...\n")
    conn = Connection(ROUTER_URL, ROUTER_USER, ROUTER_PASS)
    client = Client(conn)

    supported = []
    not_supported = []

    for name, fn in PROBES:
        try:
            result = fn(client)
            supported.append((name, result))
            print(f"  OK  {name}")
        except Exception as e:
            not_supported.append((name, str(e)))
            print(f"  XX  {name}  ({e})")

    print(f"\n{'=' * 50}")
    print(f"Supported: {len(supported)}/{len(PROBES)}")
    print(f"Not supported / errored: {len(not_supported)}/{len(PROBES)}")

    print("\n--- Supported detail ---")
    for name, result in supported:
        print(f"\n[{name}]")
        if isinstance(result, dict):
            for k, v in result.items():
                print(f"  {k}: {v}")
        else:
            print(f"  {result}")


if __name__ == "__main__":
    main()
