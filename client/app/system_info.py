import platform
import socket
import getpass
from typing import Dict


LOCAL_IP_FALLBACKS = {"127.0.0.1", "127.0.1.1", "::1"}


def get_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "unknown-host"


def get_primary_ip() -> str:
    # Cross-platform trick: open UDP socket without sending packets.
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
        finally:
            sock.close()
        if ip and ip not in LOCAL_IP_FALLBACKS:
            return ip
    except Exception:
        pass

    try:
        ip = socket.gethostbyname(get_hostname())
        if ip:
            return ip
    except Exception:
        pass

    return "unknown"


def get_current_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def system_descriptor() -> Dict[str, str]:
    return {
        "os": platform.system().lower() or "unknown",
        "platform": platform.platform(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine() or "unknown",
        "python": platform.python_version(),
    }
