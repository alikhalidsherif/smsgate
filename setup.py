# /// script
# dependencies = ["huawei-lte-api"]
# ///
import logging
import os

from huawei_lte_api.Client import Client
from huawei_lte_api.Connection import Connection
from huawei_lte_api.enums.net import NetworkModeEnum

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

ROUTER_URL = os.environ.get("ROUTER_URL", "http://192.168.8.1")
ROUTER_USER = os.environ.get("ROUTER_USER", "admin")
ROUTER_PASS = os.environ.get("ROUTER_PASS", "")


def run():
    conn = Connection(ROUTER_URL, ROUTER_USER, ROUTER_PASS)
    client = Client(conn)

    # Disable mobile data
    try:
        result = client.dial_up.mobile_dataswitch({"dataswitch": "0"})
        log.info("Mobile data off: %s", result)
    except Exception as e:
        log.warning("mobile_dataswitch failed: %s", e)

    # Lock to 2G only
    try:
        result = client.net.set_net_mode(
            NetworkModeEnum.MODE_2G_ONLY, "3FFFFFFF", "7FFFFFFFFFFFFFFF"
        )
        log.info("Locked to 2G: %s", result)
    except Exception as e:
        log.warning("set_net_mode failed, trying simple form: %s", e)
        try:
            result = client.net.set_net_mode(NetworkModeEnum.MODE_2G_ONLY)
            log.info("Locked to 2G (simple): %s", result)
        except Exception as e2:
            log.warning("Also failed: %s", e2)

    log.info("NOTE: LED control not available on E5331 via API.")


if __name__ == "__main__":
    run()
