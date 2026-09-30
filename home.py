"""Who may use the page through the public tunnel: whoever is at home. Every device on the flat's WiFi reaches
the internet from the same public IPv4 address (and, with IPv6, from the flat's own address range), so a visit
from there is someone at home. Cloudflare reports each visitor's address (CF-Connecting-IP); this module looks
up the flat's own, and re-checks every few minutes because the ISP can change it."""
import ipaddress
import socket
import threading
import time
import urllib.request

REFRESH = 300  # seconds
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


def addresses():
    with _lock:
        if time.time() - _home["at"] > REFRESH:
            try:
                _home["v4"] = public_ipv4()
            except OSError:  # keep the last known address through a hiccup; unknown stays unknown (= refuse)
                pass
            _home["v6"], _home["at"] = ipv6_home_net(), time.time()
        return _home["v4"], _home["v6"]


def allowed(visitor):
    """Is this visitor (an address from Cloudflare) at home? Unknown or unparsable: no."""
    try:
        ip = ipaddress.ip_address(visitor.strip())
    except ValueError:
        return False
    v4, v6 = addresses()
    return (v4 is not None and ip == v4) or (v6 is not None and ip in v6)
