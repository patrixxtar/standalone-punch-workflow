"""
vpn_connect.py — connect FortiClient VPN automatically (no AutoHotkey, no server).

    python vpn_connect.py            connect
    python vpn_connect.py --check    print connected / not connected
From code: from vpn_connect import connect_vpn; connect_vpn()

Flow
  1. Launch FortiClient and focus its window.
  2. Find "REMOTE ACCESS" in the sidebar by its text and click it.
  3. Find the VPN card that has a "Clear Certificate" button and click that card's "Connect".
     (If no card has a certificate, the VPN named by VPN_NAME in config.json is used.)
  4. FortiAuth window: type username, TAB, password, ENTER.
  5. Wait for a Fortinet adapter that holds an IP address.

Buttons are located through Windows UI Automation, so nothing depends on screen
resolution, DPI scaling, window position or the order of the VPNs.
"""
import os
import sys
import time
import logging
import subprocess
from typing import NamedTuple

import psutil

import config
from common import setup_logging, toast, is_forti_connected, ny_time_str

FORTI_WINDOW_WAIT_SEC = 15
FORTIAUTH_WAIT_SEC = 30
UI_FIND_TIMEOUT_SEC = 20          # how long to wait for each button to appear
log = logging.getLogger("vpn")


# ----------------------------------------------------------- window helpers
def _windows_of(exe_name: str, class_name: str | None = None):
    from pywinauto import Desktop
    pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == exe_name.lower()}
    if not pids:
        return []
    kwargs = {"class_name": class_name} if class_name else {}
    return [w for w in Desktop(backend="win32").windows(**kwargs) if w.process_id() in pids and w.is_visible()]


def _wait_window(exe_name: str, timeout: int, class_name: str | None = None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        wins = _windows_of(exe_name, class_name)
        if wins:
            return wins[0]
        time.sleep(0.5)
    return None


def _focus(win) -> None:
    try:
        if win.is_minimized():
            win.restore()
        win.set_focus()
        time.sleep(1.5)
    except Exception as e:
        log.warning(f"Could not focus window: {e}")


def _type(text: str) -> None:
    """Escape pywinauto's special keys (+ ^ % ~ ( ) { }) so passwords type literally."""
    from pywinauto import keyboard
    keyboard.send_keys("".join(f"{{{c}}}" if c in "+^%~(){}" else c for c in text), with_spaces=True, pause=0.03)


def _keys(seq: str) -> None:
    from pywinauto import keyboard
    keyboard.send_keys(seq)


def _close_forticlient() -> None:
    for w in _windows_of("FortiClient.exe", "Chrome_WidgetWin_1"):
        try:
            w.close()
        except Exception:
            pass


def _wait_connected(timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_forti_connected():
            return True
        time.sleep(3)
    return False


def _finish(ok: bool, message: str) -> bool:
    toast("VPN ✅" if ok else "VPN ❌", message)
    (log.info if ok else log.error)(message)
    _close_forticlient()
    return ok


# ----------------------------------------------------------- reading the screen (UI Automation)
class Node(NamedTuple):
    name: str       # normalised (lower-case, single spaces) — used for matching
    label: str      # original text — used in logs
    ctype: str      # Button, Text, Group, ...
    left: int
    top: int
    right: int
    bottom: int

    @property
    def cx(self) -> int:
        return (self.left + self.right) // 2

    @property
    def cy(self) -> int:
        return (self.top + self.bottom) // 2


def _norm(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def _snapshot(hwnd: int) -> list[Node]:
    """Every visible, named UI element inside the window, with its on-screen rectangle."""
    from pywinauto.uia_element_info import UIAElementInfo
    nodes: list[Node] = []
    for info in UIAElementInfo(hwnd).descendants():
        try:
            label = info.name or ""
            name = _norm(label)
            if not name or not info.visible:
                continue
            r = info.rectangle
            if r.right - r.left <= 0 or r.bottom - r.top <= 0:
                continue
            nodes.append(Node(name, label.strip(), str(info.control_type), r.left, r.top, r.right, r.bottom))
        except Exception:
            continue
    return nodes


def _click(node: Node) -> None:
    from pywinauto import mouse
    log.info(f"Clicking {node.ctype} '{node.label}' at ({node.cx}, {node.cy})")
    mouse.click(coords=(node.cx, node.cy))


def _poll(fn, timeout: float, interval: float = 0.7):
    """Call fn() until it returns something truthy or the timeout passes."""
    deadline = time.time() + timeout
    while True:
        try:
            result = fn()
        except Exception as e:
            log.debug(f"poll: {e}")
            result = None
        if result:
            return result
        if time.time() >= deadline:
            return None
        time.sleep(interval)


# ----------------------------------------------------------- deciding what to click
def pick_remote_access(nodes: list[Node]) -> Node | None:
    """The 'REMOTE ACCESS' sidebar item (left-most match wins)."""
    hits = [n for n in nodes if n.name == "remote access"] or [n for n in nodes if "remote access" in n.name]
    return min(hits, key=lambda n: (n.left, n.top)) if hits else None


def pick_cert_connect(nodes: list[Node], vpn_name: str = "") -> tuple[Node, str] | None:
    """
    The 'Connect' button of the VPN card that has a 'Clear Certificate' button.

    Each card looks like:   [ VPN name ............ [Connect] ]
                            [ EMS        [View] [Clear Certificate] ]
    so the right Connect is the closest one ABOVE the 'Clear Certificate' button.
    If no card has a certificate and `vpn_name` is set, that VPN's own row is used instead.
    """
    connects = [n for n in nodes if n.name == "connect"]      # exact match: never "Disconnect"/"Connected"
    certs = sorted((n for n in nodes if n.name == "clear certificate"), key=lambda n: (n.top, n.left))

    if certs and connects:
        above = [c for c in connects if c.top < certs[0].top]
        if above:
            return max(above, key=lambda c: c.top), "VPN with certificate"

    if vpn_name and connects:
        want = _norm(vpn_name)
        labels = [n for n in nodes if want in n.name]
        if labels:
            row = labels[0]
            return min(connects, key=lambda c: abs(c.cy - row.cy)), f"VPN named '{vpn_name}'"
    return None


# ----------------------------------------------------------- the flow
def _click_connect(hwnd: int) -> bool:
    """Open Remote Access, then click Connect on the certificate VPN. True if Connect was clicked."""
    tab = _poll(lambda: pick_remote_access(_snapshot(hwnd)), UI_FIND_TIMEOUT_SEC)
    if tab:
        time.sleep(0.5)                                # let the window settle, then re-read the position
        _click(pick_remote_access(_snapshot(hwnd)) or tab)
        time.sleep(1.5)
    else:
        log.warning("'REMOTE ACCESS' not found; looking for the VPN list anyway.")

    vpn_name = str(getattr(config, "VPN_NAME", "") or "")
    found = _poll(lambda: pick_cert_connect(_snapshot(hwnd), vpn_name), UI_FIND_TIMEOUT_SEC)
    if not found:
        seen = sorted({n.label for n in _snapshot(hwnd)})[:40]
        log.error(f"No Connect button for a certificate VPN found. Visible text: {seen}")
        return False

    time.sleep(0.8)                                    # let the page animation finish, then re-read
    button, why = pick_cert_connect(_snapshot(hwnd), vpn_name) or found
    log.info(f"Connecting: {why}")
    _click(button)
    return True


def _enter_credentials() -> None:
    auth = _wait_window("FortiAuth.exe", FORTIAUTH_WAIT_SEC)
    if not auth:
        log.warning("FortiAuth window not shown (saved credentials?); waiting for the tunnel.")
        return
    _focus(auth)
    _type(config.VPN_USER)
    _keys("{TAB}")
    time.sleep(1)
    _type(config.VPN_PASS)
    time.sleep(1)
    _keys("{ENTER}")


# ----------------------------------------------------------- main entry
def connect_vpn() -> bool:
    if is_forti_connected():
        return True

    exe = config.FORTICLIENT_EXE
    if not os.path.exists(exe):
        return _finish(False, f"FortiClient.exe not found at {exe}")

    toast("VPN", "Connecting to FortiClient VPN… please don't touch the keyboard or mouse.", 8)
    try:
        subprocess.Popen([exe], cwd=os.path.dirname(exe), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)   # keep FortiClient's logs out of our console
        win = _wait_window("FortiClient.exe", FORTI_WINDOW_WAIT_SEC, "Chrome_WidgetWin_1")
        if not win:
            return _finish(False, "FortiClient window did not appear in time.")
        _focus(win)

        if not _click_connect(win.handle):
            return _finish(False, "No VPN with a certificate found. Set VPN_NAME in config.json.")

        _enter_credentials()

        if _wait_connected(int(config.VPN_CONNECT_TIMEOUT_SEC)):
            return _finish(True, f"VPN connected · {ny_time_str()}")
        return _finish(False, f"VPN did not come up within {config.VPN_CONNECT_TIMEOUT_SEC}s.")
    except Exception as e:
        return _finish(False, f"VPN automation crashed: {e}")


if __name__ == "__main__":
    setup_logging(os.path.join(config.APP_DIR, "automation.log"))
    if "--check" in sys.argv:
        print("connected" if is_forti_connected() else "not connected")
        sys.exit(0)
    ok = connect_vpn()
    print("connected" if ok else "FAILED — see automation.log")
    logging.shutdown()
    sys.stdout.flush()
    os._exit(0 if ok else 1)          # skips COM teardown, which prints a harmless "Win32 exception ... IUnknown"
