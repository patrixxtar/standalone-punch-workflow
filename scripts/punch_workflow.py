"""
punch_workflow.py — connect VPN (if needed) → Microsoft sign-in (TOTP) → UKG punch.

    python punch_workflow.py                 live punch, visible browser
    python punch_workflow.py --dry-run       full flow, stops before clicking Punch
    python punch_workflow.py --headless      (used by scheduler.py)
From code: from punch_workflow import run_punch_workflow

UKG email code: if OTP_API_URL/KEY (or OTP_IMAP_*) is set in config.json the code is fetched
from the shared ssqa.digital mailbox (only emails forwarded to YOUR alias). Otherwise the first run must be
visible and you type the code yourself. "Remember this device" is ticked either way,
and the Chrome profile (./chrome_profile) is kept so later runs usually skip MFA.
"""
import os
import sys
import time
import logging
import threading
import contextlib
from urllib.parse import urlparse

import pyotp
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (StaleElementReferenceException, ElementNotInteractableException,
                                        ElementClickInterceptedException)

import config
from common import setup_logging, toast, save_screenshot, is_forti_connected, ny_time_str
from vpn_connect import connect_vpn
import otp_mail

PAGE_WAIT_SEC = 30
RUN_TIMEOUT_SEC = 15 * 60
PROFILE_DIR = os.path.join(config.APP_DIR, "chrome_profile")
log = logging.getLogger("punch")


# --------------------------------------------------------------- helpers
def _shot(driver, name: str) -> None:
    with contextlib.suppress(Exception):
        save_screenshot(driver.get_screenshot_as_png(), name)


def _kill(driver) -> None:
    with contextlib.suppress(Exception):
        driver.quit()


def build_driver(headless: bool) -> webdriver.Chrome:
    o = webdriver.ChromeOptions()
    if headless:
        o.add_argument("--headless=new")
    o.add_argument(f"--user-data-dir={PROFILE_DIR}")      # keeps "remember this device"
    for a in ("--window-size=1920,1080", "--disable-blink-features=AutomationControlled",
              "--ignore-certificate-errors", "--no-first-run", "--no-default-browser-check"):
        o.add_argument(a)
    o.add_experimental_option("excludeSwitches", ["enable-automation"])
    o.add_experimental_option("useAutomationExtension", False)
    d = webdriver.Chrome(options=o)
    d.set_page_load_timeout(90)
    return d


# --------------------------------------------------------------- Microsoft sign-in
# Microsoft's sign-in is a chain of pages whose order varies (password → code, or password →
# "Approve sign in request" → "Verify your identity" → code, or straight through when the session
# is remembered). Instead of assuming an order, we look at which page is on screen and act on it,
# so a slow or different page can never leave us waiting for a field that isn't coming.
LOGIN_TIMEOUT_SEC = 150
MS_HOST_SUFFIXES = ("microsoftonline.com", "live.com", "microsoft.com")
SUBMIT_SELECTORS = ("input#idSubmit_SAOTCC_Continue", "input#idSIButton9", "input[type='submit']", "button[type='submit']")


def _first_visible(driver, css: str):
    """First element matching `css` that is actually displayed (Microsoft keeps hidden twins in the DOM)."""
    for el in driver.find_elements(By.CSS_SELECTOR, css):
        try:
            if el.is_displayed():
                return el
        except StaleElementReferenceException:
            continue
    return None


def _click_submit(driver, selectors=SUBMIT_SELECTORS) -> bool:
    """Click the first displayed + enabled submit-type button. False if there is none yet."""
    for css in selectors:
        for el in driver.find_elements(By.CSS_SELECTOR, css):
            try:
                if el.is_displayed() and el.is_enabled():
                    el.click()
                    return True
            except StaleElementReferenceException:
                continue
    return False


def _fill(driver, css: str, text: str) -> bool:
    """Type into the visible field and confirm the page kept the value (re-renders can wipe it)."""
    el = _first_visible(driver, css)
    if not el:
        return False
    el.clear()
    el.send_keys(text)
    return el.get_attribute("value") == text


def _fresh_totp(last_code: str = "") -> str:
    """A TOTP code with at least 5s of life left, and never the one we already submitted."""
    totp = pyotp.TOTP(config.SECRET_KEY)
    remaining = totp.interval - (time.time() % totp.interval)
    if remaining < 5 or totp.now() == last_code:
        time.sleep(remaining + 1)
    return totp.now()


def _login_state(driver) -> str:
    """Which sign-in page is showing right now."""
    if _first_visible(driver, "input[name='otc']"):
        return "otc"
    if _first_visible(driver, "div[data-value='PhoneAppOTP']"):
        return "proofs"                      # \"Verify your identity\" list of methods
    if _first_visible(driver, "#signInAnotherWay"):
        return "another_way"                 # \"Approve sign in request\" → use another method
    if _first_visible(driver, "input[name='passwd']"):
        return "password"
    if _first_visible(driver, "input[name='loginfmt'], input[type='email']"):
        return "email"
    if _first_visible(driver, "input#KmsiCheckboxField"):
        return "kmsi"                        # \"Stay signed in?\"
    host = urlparse(driver.current_url).hostname or ""
    if host and not host.endswith(MS_HOST_SUFFIXES):
        return "off_ms"                      # back on UKG
    return "unknown"


def _describe_page(driver) -> str:
    with contextlib.suppress(Exception):
        head = _first_visible(driver, "#loginHeader, div[role='heading'], h1")
        return f"url={driver.current_url} heading={(head.text if head else '')!r}"
    return "page unavailable"


def microsoft_login(driver, wait) -> None:
    driver.get(config.UKG_URL)
    deadline = time.time() + LOGIN_TIMEOUT_SEC
    last_action: dict[str, float] = {}      # state -> when we last acted on it
    tries: dict[str, int] = {}
    last_code = ""
    off_ms_since = None
    acted = False
    state = "unknown"

    while time.time() < deadline:
        try:
            state = _login_state(driver)

            if state == "off_ms":
                off_ms_since = off_ms_since or time.time()
                # Back on UKG. Insist on a short stable period so a redirect still in flight isn't mistaken for "done".
                if time.time() - off_ms_since >= (2 if acted else 6):
                    return
                time.sleep(0.5)
                continue
            off_ms_since = None

            # We just acted on this page; give it time to move on before touching it again.
            if state in last_action and time.time() - last_action[state] < 8:
                time.sleep(0.5)
                continue
            if tries.get(state, 0) >= 3:
                raise RuntimeError(f"Microsoft sign-in stuck on '{state}' page. {_describe_page(driver)}")

            done = False
            if state == "email":
                done = _fill(driver, "input[name='loginfmt'], input[type='email']", config.EMAIL_USER) and _click_submit(driver)
            elif state == "password":
                done = _fill(driver, "input[name='passwd']", config.EMAIL_PASS) and _click_submit(driver)
            elif state == "another_way":
                el = _first_visible(driver, "#signInAnotherWay")
                if el:
                    el.click()
                    done = True
            elif state == "proofs":
                el = _first_visible(driver, "div[data-value='PhoneAppOTP']")
                if el:
                    el.click()
                    done = True
            elif state == "otc":
                code = _fresh_totp(last_code)
                done = _fill(driver, "input[name='otc']", code) and _click_submit(driver)
                if done:
                    last_code = code                                 # only "used" once actually submitted
            elif state == "kmsi":
                with contextlib.suppress(Exception):                 # \"Don't show this again\"
                    _first_visible(driver, "input#KmsiCheckboxField").click()
                done = _click_submit(driver, ("input#idSIButton9", "input[type='submit']"))
            else:                                                    # unknown / loading
                time.sleep(0.5)
                continue

            if done:
                acted = True
                last_action[state] = time.time()
                tries[state] = tries.get(state, 0) + 1
            time.sleep(0.5)
        except (StaleElementReferenceException, ElementNotInteractableException,
                ElementClickInterceptedException):
            time.sleep(0.5)                                          # page re-rendered under us: look again

    raise RuntimeError(f"Microsoft sign-in did not finish in {LOGIN_TIMEOUT_SEC}s (last page: {state}). {_describe_page(driver)}")


def ukg_email_mfa(driver, headless: bool) -> None:
    """Only triggers if UKG shows the MFA page. Requires a visible browser + the user."""
    short = WebDriverWait(driver, 10)
    try:
        radio = short.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input#ctl00_Content_radioButtonEmail")))
    except Exception:
        return                                           # device remembered → no MFA

    auto = otp_mail.configured()
    if headless and not auto:
        raise RuntimeError("UKG asked for an email code but the run is headless and no OTP mailbox is "
                           "configured. Run punch_now.bat once (visible) to remember this device.")

    radio.click()
    Select(short.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, "select#ctl00_Content_ddlMfaEmail")))).select_by_value(str(config.UKG_MFA_EMAIL_VALUE))

    baseline = otp_mail.latest_uid() if auto else 0        # snapshot BEFORE requesting a new code
    driver.find_element(By.CSS_SELECTOR, "input#ctl00_Content_ButtonMultiFactDeliveryMethod").click()

    code_field = WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "input#ctl00_Content_txtMultiFactorAccessCode")))
    with contextlib.suppress(Exception):
        cb = driver.find_element(By.CSS_SELECTOR, "input#ctl00_Content_chkRememberDevice")
        if not cb.is_selected():
            cb.click()

    if auto:
        code = otp_mail.wait_for_code(after_uid=baseline)
        if not code:
            raise RuntimeError(f"No UKG code arrived for {config.OTP_ALIAS}. Is Gmail forwarding set up?")
        code_field.clear()
        code_field.send_keys(code)
        driver.find_element(By.CSS_SELECTOR, "input#ctl00_Content_ButtonMultiFactorAccessCodeEntry").click()
        return

    code_field.click()
    wait_sec = int(config.MFA_MANUAL_WAIT_SEC)
    toast("UKG needs you", f"Type the code UKG emailed you into the browser and press Submit ({wait_sec}s).", 15)
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        if not driver.find_elements(By.CSS_SELECTOR, "input#ctl00_Content_txtMultiFactorAccessCode"):
            return                                       # user submitted, page moved on
        time.sleep(2)
    raise RuntimeError(f"No MFA code entered within {wait_sec}s.")


def find_punch_button(driver, wait):
    driver.get(config.KRONOS_URL)
    time.sleep(15)
    if len(driver.window_handles) > 1:
        driver.switch_to.window(driver.window_handles[-1])
    driver.switch_to.default_content()

    locator = (By.CSS_SELECTOR, "button#punchSubmitBtnId")
    with contextlib.suppress(Exception):
        return wait.until(EC.element_to_be_clickable(locator))

    short = WebDriverWait(driver, 10)
    for iframe in driver.find_elements(By.TAG_NAME, "iframe"):
        driver.switch_to.default_content()
        with contextlib.suppress(Exception):
            driver.switch_to.frame(iframe)
            return short.until(EC.element_to_be_clickable(locator))
    raise RuntimeError("Punch button not found on page or in any iframe.")


# --------------------------------------------------------------- main
def run_punch_workflow(dry_run: bool = False, headless: bool = False) -> bool:
    """Returns True on success; never raises; hard-stops after RUN_TIMEOUT_SEC."""
    if not connect_vpn():
        toast("UKG ❌", "VPN is not connected — punch aborted.")
        log.error("Punch aborted: VPN not connected.")
        return False

    driver = watchdog = None
    try:
        driver = build_driver(headless)
        watchdog = threading.Timer(RUN_TIMEOUT_SEC, lambda: _kill(driver))
        watchdog.daemon = True
        watchdog.start()
        wait = WebDriverWait(driver, PAGE_WAIT_SEC)

        microsoft_login(driver, wait)
        ukg_email_mfa(driver, headless)
        wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "div[role='dialog'][aria-describedby='ui-id-1'], div#mx-welcome-banner-container")))

        btn = find_punch_button(driver, wait)

        if dry_run:
            _shot(driver, "dry_run")
            toast("UKG 🧪", "Dry run OK — reached the Punch button (not clicked).")
            return True

        btn.click()
        wait.until(EC.visibility_of_element_located((By.XPATH, "//p[contains(text(), 'Your punch was successful')]")))
        time.sleep(1)
        _shot(driver, "punch_ok")
        toast("UKG ✅", f"Punch recorded · {ny_time_str()}")
        log.info(f"Punch OK {ny_time_str()}")
        return True

    except Exception as e:
        _shot(driver, "failed")
        log.error(f"Punch failed: {e}")
        toast("UKG ❌", f"Punch failed: {str(e)[:120]} — see screenshots/ and automation.log", 15)
        return False
    finally:
        if watchdog:
            watchdog.cancel()
        if driver:
            time.sleep(2)
            _kill(driver)


if __name__ == "__main__":
    setup_logging(os.path.join(config.APP_DIR, "automation.log"))
    ok = run_punch_workflow(dry_run="--dry-run" in sys.argv, headless="--headless" in sys.argv)
    sys.exit(0 if ok else 1)