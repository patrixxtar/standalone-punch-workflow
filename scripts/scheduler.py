"""
scheduler.py — fires the punch at the configured New York times on weekdays.

* Uses the window [slot, slot + CATCHUP_WINDOW_MIN): a laptop waking up a few
  minutes late still punches; a slot is never fired twice (fired_slots.json).
* No network, no server. Runs in the background via start.bat.
"""
import os
import json
import time
import logging
import threading
from datetime import timedelta

import config
from common import setup_logging, now_ny, toast
from punch_workflow import run_punch_workflow

TICK_SEC = 15
STATE_FILE = os.path.join(config.APP_DIR, "fired_slots.json")
log = logging.getLogger("scheduler")


def _load() -> list[str]:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(slots: list[str]) -> None:
    today = now_ny().strftime("%Y-%m-%d")
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump([s for s in slots if s.startswith(today)], f)
    except Exception as e:
        log.error(f"Could not write {STATE_FILE}: {e}")


def already_running() -> bool:
    try:
        import ctypes
        ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\UkgPunchScheduler")
        return ctypes.windll.kernel32.GetLastError() == 183
    except Exception:
        return False


def main() -> None:
    fired = _load()
    window = timedelta(minutes=int(config.CATCHUP_WINDOW_MIN))
    running = threading.Lock()
    log.warning(f"Scheduler started. Slots (NY): {', '.join(config.SCHEDULES)}")

    while True:
        try:
            now = now_ny()
            if now.weekday() < 5:
                for hhmm in config.SCHEDULES:
                    h, m = map(int, hhmm.split(":"))
                    slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
                    key = f"{now:%Y-%m-%d} {hhmm}"
                    if slot <= now < slot + window and key not in fired and running.acquire(blocking=False):
                        fired.append(key)
                        _save(fired)
                        try:
                            toast("UKG", f"Scheduled punch ({hhmm} NY) starting…", 6)
                            run_punch_workflow(dry_run=False, headless=bool(config.HEADLESS))
                        finally:
                            running.release()
        except Exception as e:
            log.exception(f"Scheduler error: {e}")
        time.sleep(TICK_SEC)


if __name__ == "__main__":
    setup_logging(os.path.join(config.APP_DIR, "automation.log"))
    if already_running():
        raise SystemExit("Scheduler is already running.")
    main()
