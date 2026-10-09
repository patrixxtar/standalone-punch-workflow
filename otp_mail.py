"""
otp_mail.py — fetch UKG email codes for THIS user from the shared ssqa.digital inbox.

Each user forwards their UKG emails to  <username>@<OTP_DOMAIN>  (e.g.
mylene.priol@ssqa.digital). Two backends, chosen automatically:

  1. API  (preferred)  OTP_API_URL + OTP_API_KEY  -> GET catchall-otp.php?key=..&to=<alias>[&after_id=..]
     Same contract as the original punch-workflow.py:  {"ok": true, "data": {id, code, subject, body}}
     The server filters by recipient, so users never hold the mailbox password.
  2. IMAP (fallback)   OTP_IMAP_HOST/USER/PASS    -> reads the catch-all inbox directly and
     filters recipient headers client-side.

    python otp_mail.py --verify   show the Gmail forwarding-confirmation code for my alias
    python otp_mail.py --test     show the newest UKG code for my alias
"""
import re
import sys
import time
import email
import imaplib
import logging

import requests
from email.header import decode_header, make_header
from email.message import Message

import config

log = logging.getLogger("otp")
RECIPIENT_HEADERS = ("To", "Delivered-To", "X-Forwarded-To", "X-Original-To", "Envelope-To", "Cc")
CODE_PATTERNS = (re.compile(r"Access Code is:\s*(\d{6})", re.I), re.compile(r"\b(\d{6})\b"))


def api_configured() -> bool:
    return bool(config.OTP_API_URL and config.OTP_API_KEY)


def imap_configured() -> bool:
    return bool(config.OTP_IMAP_HOST and config.OTP_IMAP_USER and config.OTP_IMAP_PASS)


def configured() -> bool:
    return api_configured() or imap_configured()


# ============================================================ API backend
def _api_record(after_id: int | None = None, mode: str | None = None) -> dict | None:
    """One call to catchall-otp.php. Returns `data` dict, or None when nothing new is there."""
    params = {"key": config.OTP_API_KEY, "to": config.OTP_ALIAS}
    if after_id:
        params["after_id"] = after_id
    if mode:
        params["mode"] = mode
    resp = requests.get(config.OTP_API_URL, params=params, timeout=15)
    if resp.status_code == 403:
        raise RuntimeError("OTP API rejected the key (403). Check OTP_API_KEY.")
    if resp.status_code == 400:
        raise RuntimeError(f"OTP API rejected the alias '{config.OTP_ALIAS}' (400).")
    if resp.status_code == 502:
        raise RuntimeError("OTP API could not reach the mailbox (502).")
    try:
        payload = resp.json()
    except ValueError:
        raise RuntimeError(f"OTP API returned non-JSON (HTTP {resp.status_code}): {resp.text[:120]}")
    return payload.get("data") if payload.get("ok") else None


def _api_code(record: dict) -> str | None:
    code = record.get("code")
    if code:
        return str(code)
    text = f"{record.get('subject', '')}\n{record.get('body', '')}"
    for pat in CODE_PATTERNS:
        hit = pat.search(text)
        if hit:
            return hit.group(1)
    return None


# ============================================================ IMAP backend


def _connect() -> imaplib.IMAP4_SSL:
    m = imaplib.IMAP4_SSL(config.OTP_IMAP_HOST, int(config.OTP_IMAP_PORT), timeout=20)
    m.login(config.OTP_IMAP_USER, config.OTP_IMAP_PASS)
    m.select("INBOX", readonly=True)
    return m


def _hdr(msg: Message, name: str) -> str:
    try:
        return str(make_header(decode_header(msg.get(name, "") or "")))
    except Exception:
        return msg.get(name, "") or ""


def _recipients(msg: Message) -> str:
    return " ".join(_hdr(msg, h) for h in RECIPIENT_HEADERS).lower()


def _body(msg: Message) -> str:
    parts = msg.walk() if msg.is_multipart() else [msg]
    out = []
    for p in parts:
        if p.get_content_type() in ("text/plain", "text/html"):
            try:
                out.append(p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "ignore"))
            except Exception:
                pass
    return re.sub(r"<[^>]+>", " ", "\n".join(out))


def _recent_uids(m: imaplib.IMAP4_SSL, since_days: int = 1) -> list[int]:
    since = time.strftime("%d-%b-%Y", time.gmtime(time.time() - since_days * 86400))
    _, data = m.uid("SEARCH", None, f'(SINCE "{since}")')
    return sorted(int(u) for u in (data[0] or b"").split())


def _fetch(m: imaplib.IMAP4_SSL, uid: int) -> Message | None:
    _, data = m.uid("FETCH", str(uid), "(BODY.PEEK[])")
    for part in data:
        if isinstance(part, tuple):
            return email.message_from_bytes(part[1])
    return None


def _mine(msg: Message) -> bool:
    return str(config.OTP_ALIAS).lower() in _recipients(msg)


def _extract_code(msg: Message) -> str | None:
    text = f"{_hdr(msg, 'Subject')}\n{_body(msg)}"
    for pat in CODE_PATTERNS:
        hit = pat.search(text)
        if hit:
            return hit.group(1)
    return None


def latest_uid() -> int:
    """Newest message id addressed to me — take this BEFORE requesting a new code."""
    if api_configured():
        try:
            rec = _api_record()
            return int(rec.get("id") or 0) if rec else 0
        except Exception as e:
            log.warning(f"OTP API baseline read failed: {e}")
            return 0
    if not imap_configured():
        return 0
    try:
        with _connect() as m:
            for uid in reversed(_recent_uids(m)):
                msg = _fetch(m, uid)
                if msg and _mine(msg):
                    return uid
    except Exception as e:
        log.warning(f"OTP baseline read failed: {e}")
    return 0


def wait_for_code(after_uid: int, timeout: int | None = None, interval: int = 5) -> str | None:
    """Poll until an email newer than after_uid, addressed to me, contains a 6-digit code."""
    timeout = int(timeout or config.OTP_POLL_TIMEOUT_SEC)
    deadline = time.time() + timeout

    if api_configured():
        while time.time() < deadline:
            try:
                rec = _api_record(after_id=after_uid)
                if rec:
                    code = _api_code(rec)
                    if code:
                        return code
            except RuntimeError as e:          # auth / server misconfig: retrying won't help
                log.error(str(e))
                return None
            except Exception as e:
                log.warning(f"OTP API request failed, retrying: {e}")
            time.sleep(interval)
        log.error(f"No UKG code for {config.OTP_ALIAS} within {timeout}s (API).")
        return None

    while time.time() < deadline:
        try:
            with _connect() as m:
                for uid in reversed(_recent_uids(m)):
                    if uid <= after_uid:
                        break
                    msg = _fetch(m, uid)
                    if msg and _mine(msg):
                        code = _extract_code(msg)
                        if code:
                            return code
        except Exception as e:
            log.warning(f"OTP mailbox poll failed, retrying: {e}")
        time.sleep(interval)
    log.error(f"No UKG code for {config.OTP_ALIAS} within {timeout}s.")
    return None


def _confirmation_from_text(text: str) -> str | None:
    if "forward" not in text.lower():
        return None
    code = re.search(r"Confirmation code:\s*(\d+)", text, re.I)
    link = re.search(r"https://mail-settings\.google\.com/\S+", text)
    return (code.group(1) if code else "") + (f"\n{link.group(0)}" if link else "") or text[:500]


def forwarding_confirmation() -> str | None:
    """Gmail's 'confirm forwarding' mail: returns its confirmation code or link."""
    if api_configured():
        rec = _api_record(mode="forwarding")
        if not rec:
            return None
        return (rec.get("code") or "") + (f"\n{rec['link']}" if rec.get("link") else "") or rec.get("body", "")[:500]
    with _connect() as m:
        for uid in reversed(_recent_uids(m, since_days=3)):
            msg = _fetch(m, uid)
            if not msg or not _mine(msg):
                continue
            found = _confirmation_from_text(f"{_hdr(msg, 'Subject')}\n{_body(msg)}")
            if found:
                return found
    return None


if __name__ == "__main__":
    if not configured():
        sys.exit("Set OTP_API_URL + OTP_API_KEY (or OTP_IMAP_*) in config.json.")
    print(f"Alias: {config.OTP_ALIAS}   backend: {'API' if api_configured() else 'IMAP'}")
    if "--verify" in sys.argv:
        print(forwarding_confirmation() or "No Gmail forwarding-confirmation email found for your alias.")
    elif api_configured():
        rec = _api_record()
        print(f"Newest code: {_api_code(rec)}  (subject: {rec.get('subject', '')})" if rec and _api_code(rec)
              else "No UKG code email found for your alias.")
    else:
        with _connect() as m:
            for uid in reversed(_recent_uids(m, 3)):
                msg = _fetch(m, uid)
                if msg and _mine(msg) and _extract_code(msg):
                    print(f"Newest code: {_extract_code(msg)}  (subject: {_hdr(msg, 'Subject')})")
                    break
            else:
                print("No UKG code email found for your alias (last 3 days).")
