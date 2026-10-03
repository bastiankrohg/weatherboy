"""Who may use the page through the public tunnel: whoever is at home. Every device on the flat's WiFi reaches
the internet from the same public IPv4 address (and, with IPv6, from the flat's own address range), so a visit
from there is someone at home. Cloudflare reports each visitor's address (CF-Connecting-IP).

"Home" is where the printer is: the flat's address is only looked up while the printer answers, and the last
one confirmed is kept on disk. So a laptop running this on another network (a university, a café) doesn't
start treating everyone there as being at home."""
import ipaddress
import json
import socket
import threading
import time
import urllib.request
from pathlib import Path

FILE = Path(__file__).with_name("data") / "hjemme.json"
REFRESH = 300  # seconds
RETRY = 30  # seconds, while home isn't known yet: a missed knock shouldn't shut everyone out for REFRESH
_home = {"v4": None, "v6": None, "at": 0.0}
_lock = threading.Lock()


def public_ipv4():
    with urllib.request.urlopen("https://1.1.1.1/cdn-cgi/trace", timeout=5) as r:
        return next(ipaddress.ip_address(l[3:]) for l in r.read().decode().splitlines() if l.startswith("ip="))


def ipv6_home_net():
    """The flat's IPv6 range, if it has one: devices on the WiFi share this machine's /56."""
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as s:
            s.connect(("2001:db8::1", 9))  # documentation address: nothing is sent, it just picks the route
            ip = ipaddress.ip_address(s.getsockname()[0])
        return ipaddress.ip_network(f"{ip}/56", strict=False) if ip.is_global else None
    except OSError:
        return None


def addresses(at_home=lambda: True):
    """(IPv4, IPv6 range) of home. Re-checked every REFRESH seconds, but only while at_home() says this
    machine is on the home network; otherwise the last confirmed values (kept in FILE) stand."""
    with _lock:
        if time.time() - _home["at"] > REFRESH:
            _home["at"] = time.time()
            if at_home():
                try:
                    _home["v4"], _home["v6"] = public_ipv4(), ipv6_home_net()
                    FILE.parent.mkdir(exist_ok=True)
                    FILE.write_text(json.dumps({"v4": str(_home["v4"]), "v6": _home["v6"] and str(_home["v6"])}),
                                    encoding="utf-8")
                except OSError:  # a hiccup: keep what we had
                    pass
            elif _home["v4"] is None and FILE.exists():  # away from home since start: what we knew last
                saved = json.loads(FILE.read_text(encoding="utf-8"))
                _home["v4"] = ipaddress.ip_address(saved["v4"])
                _home["v6"] = saved["v6"] and ipaddress.ip_network(saved["v6"])
            if _home["v4"] is None:
                _home["at"] -= REFRESH - RETRY
        return _home["v4"], _home["v6"]


def allowed(visitor, at_home=lambda: True):
    """Is this visitor (an address from Cloudflare) at home? Unknown home or unparsable visitor: no."""
    try:
        ip = ipaddress.ip_address(visitor.strip())
    except ValueError:
        return False
    v4, v6 = addresses(at_home)
    return (v4 is not None and ip == v4) or (v6 is not None and ip in v6)
