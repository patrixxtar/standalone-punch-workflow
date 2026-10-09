"""
config.py — loads config.json from this folder and exposes each key as
`config.NAME`. Friends only edit config.json.

VPN login = Microsoft login: username is EMAIL_USER without the @domain,
password is EMAIL_PASS (set "VPN_PASS" only if it differs).
"""
import os
import json

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(APP_DIR, "config.json")

_DEFAULTS = {
    # Microsoft / UKG
    "EMAIL_USER": "",
    "EMAIL_PASS": "",
    "SECRET_KEY": "",                 # TOTP seed (Microsoft Authenticator) — required
    "UKG_URL": "",
    "KRONOS_URL": "",
    "UKG_MFA_EMAIL_VALUE": "2",       # <option value> of your email on the UKG MFA page
    "MFA_MANUAL_WAIT_SEC": 180,       # how long to wait for you to type the emailed code
    # UKG email codes — preferred: your ukg-otp.php API (users get a key, not the mailbox login)
    "OTP_API_URL": "",
    "OTP_API_KEY": "",
    # fallback: read the catch-all inbox directly over IMAP
    "OTP_IMAP_HOST": "",
    "OTP_IMAP_PORT": 993,
    "OTP_IMAP_USER": "",              # catch-all mailbox login
    "OTP_IMAP_PASS": "",
    "OTP_DOMAIN": "ssqa.digital",
    "OTP_ALIAS": "",                  # default: <username>@<OTP_DOMAIN>
    "OTP_POLL_TIMEOUT_SEC": 120,
    # FortiClient
    "FORTICLIENT_EXE": r"C:\Program Files\Fortinet\FortiClient\FortiClient.exe",
    "VPN_PASS": "",
    "VPN_NAME": "",                   # optional: VPN to use if no VPN has a Clear Certificate button
    "VPN_CONNECT_TIMEOUT_SEC": 105,
    # Scheduler (New York time, weekdays)
    "SCHEDULES": ["07:44", "17:00"],
    "CATCHUP_WINDOW_MIN": 15,
    "HEADLESS": True,                 # scheduled runs; manual runs are always visible
}

if not os.path.exists(CONFIG_FILE):
    raise SystemExit(f"config.json not found:\n  {CONFIG_FILE}\nCopy config.example.json to config.json and fill it in.")

with open(CONFIG_FILE, "r", encoding="utf-8") as _f:
    globals().update({**_DEFAULTS, **json.load(_f)})

_missing = [k for k in ("EMAIL_USER", "EMAIL_PASS", "SECRET_KEY", "UKG_URL", "KRONOS_URL") if not globals().get(k)]
if _missing:
    raise SystemExit(f"config.json is missing required values: {', '.join(_missing)}")

VPN_USER = str(EMAIL_USER).split("@")[0]
if not globals().get("VPN_PASS"):
    VPN_PASS = EMAIL_PASS
if not globals().get("OTP_ALIAS"):
    OTP_ALIAS = f"{VPN_USER}@{OTP_DOMAIN}"
