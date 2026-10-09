"""common.py — shared helpers (logging, toast notifications, screenshots, VPN check)."""
import os
import socket
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
from zoneinfo import ZoneInfo

import psutil

import config

NY_TZ = ZoneInfo("America/New_York")
SCREENSHOT_DIR = os.path.join(config.APP_DIR, "screenshots")

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- logging
def setup_logging(log_file: str, level: str = "WARNING") -> None:
    """Weekly-rotating log, warnings/errors only by default (LOG_LEVEL=INFO to debug)."""
    logger = logging.getLogger()
    logger.setLevel(os.getenv("LOG_LEVEL", level).upper())
    if not logger.handlers:
        h = TimedRotatingFileHandler(log_file, when="W6", interval=1, backupCount=4, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                                         datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(h)
    for noisy in ("urllib3", "selenium", "comtypes", "PIL"):
        logging.getLogger(noisy).setLevel(logging.ERROR)


# ---------------------------------------------------------------- time
def now_ny() -> datetime:
    return datetime.now(NY_TZ)


def ny_time_str() -> str:
    return now_ny().strftime("%Y-%m-%d %H:%M %Z")


# ---------------------------------------------------------------- notifications
def toast(title: str, message: str, seconds: int = 10) -> None:
    """Windows toast. Never raises — a notification failure must not fail a punch."""
    try:
        from plyer import notification
        notification.notify(title=title, message=message[:250], app_name="UKG Bot", timeout=seconds)
    except Exception as e:
        log.debug(f"Toast skipped: {e}")


def save_screenshot(png_bytes: bytes, name: str) -> str | None:
    """Keep the last 20 screenshots locally for troubleshooting."""
    try:
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        path = os.path.join(SCREENSHOT_DIR, f"{now_ny():%Y%m%d_%H%M%S}_{name}.png")
        with open(path, "wb") as f:
            f.write(png_bytes)
        for old in sorted(os.listdir(SCREENSHOT_DIR))[:-20]:
            os.remove(os.path.join(SCREENSHOT_DIR, old))
        return path
    except Exception as e:
        log.error(f"Could not save screenshot: {e}")
        return None


# ---------------------------------------------------------------- VPN detection
def _forti_adapter_names() -> set[str]:
    names: set[str] = set()
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        import wmi
        for a in wmi.WMI().Win32_NetworkAdapter(NetConnectionStatus=2):
            text = f"{a.Description or ''} {a.NetConnectionID or ''}".lower()
            if ("fortinet" in text or "fortissl" in text) and a.NetConnectionID:
                names.add(a.NetConnectionID)
    except Exception as e:
        log.debug(f"WMI unavailable: {e}")
    return names


def is_forti_connected() -> bool:
    """True when a Fortinet adapter is UP and holds a real IPv4 address."""
    wmi_names = _forti_adapter_names()
    try:
        stats = psutil.net_if_stats()
        for name, addrs in psutil.net_if_addrs().items():
            st = stats.get(name)
            if not st or not st.isup or (name not in wmi_names and "forti" not in name.lower()):
                continue
            if any(a.family == socket.AF_INET and a.address and not a.address.startswith("169.254") for a in addrs):
                return True
    except Exception as e:
        log.error(f"VPN interface check failed: {e}")
    return False
