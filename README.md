# UKG Auto-Punch (VPN + punch) — portable, no server

Everything runs on your own laptop. No Python install, no admin rights, no exe:
the only program in the folder is Microsoft-signed `python.exe` from python.org.

## Files
```
README.md, .gitignore
unblock.bat         run once after unzipping: stops the Windows "Run / Don't run" security prompt
start.bat           run the scheduler in the background (punches at the NY times in scripts\config.json)
stop.bat            stop it
punch_now.bat       manual live punch, visible browser  <- run this FIRST, once
test_dry_run.bat    whole flow without clicking Punch
test_vpn.bat        VPN connect test
test_otp.bat        shows your Gmail forwarding code / newest UKG code from the shared mailbox

scripts\
  *.py                  the scripts
  python\               bundled private Python runtime
  config.example.json   copy to config.json and fill in
  config.json, chrome_profile\, screenshots\, automation.log, fired_slots.json
                        created on your machine as you use it (never shared / git-ignored)
```

## Installation
1. Unzip to e.g. `C:\Automation` (not Desktop/OneDrive/Downloads), then double-click `unblock.bat` once.
   (Windows marks every file from a downloaded zip as "from the internet", which causes the *Run / Don't run* prompt
   on each script. Alternative: before unzipping, right-click the zip → Properties → tick **Unblock** → OK.)
2. In the `scripts` folder, copy `config.example.json` → `config.json` and fill in:
   - `EMAIL_USER` / `EMAIL_PASS` — Microsoft login. VPN uses the same login (username = part before `@`).
   - `SECRET_KEY` — TOTP seed from Microsoft Authenticator setup. Required.
   - `UKG_URL`, `KRONOS_URL`, `UKG_MFA_EMAIL_VALUE` — as given by the person sharing.
3. **Set up email-code forwarding** (so scheduled runs can pass UKG's email check by themselves):
   1. In Gmail: Settings → *Forwarding and POP/IMAP* → *Add a forwarding address* → enter
      `<your.username>@ssqa.digital` (the part of your work email before `@`, e.g. `mylene.priol@ssqa.digital`).
   2. Gmail sends a confirmation code to that address. Run `test_otp.bat`; it prints the code. Enter it in Gmail.
   3. Create a filter: *From* = the UKG sender address, action *Forward to* your alias. Leave the forwarding
      mode set to "disabled" on the main page — the filter does the forwarding.
   4. Put the `OTP_API_URL` and `OTP_API_KEY` you were given into config.json. `OTP_ALIAS` is filled in
      automatically from `EMAIL_USER`; change it only if your alias differs.
   If you skip this, the script still works, but every run that hits UKG's email check must be visible so you can type the code.
4. **Run `punch_now.bat` once** with the browser visible. If UKG asks for an emailed code,
   type it in the browser; "Remember this device" is pre-ticked. (This punches for real, so do it at a real punch time.)
5. Double-click `start.bat`. Optional: `Win+R` → `shell:startup` → add a shortcut to `start.bat` so it runs at login.

Needs Google Chrome and FortiClient installed. The laptop must be **awake and unlocked**
at punch time (FortiClient's window can't be driven on a locked screen; the punch itself can
run headless). Turn on *Save Password* + *Auto Connect* in FortiClient to make the VPN step rarely needed.

## Where to look when something fails
- Windows toast notifications report success/failure.
- `scripts\automation.log` — errors only.  `scripts\screenshots\` — last 20 screenshots of failures/successes.
- VPN never connects → `automation.log` lists the text FortiClient showed. The script clicks *REMOTE ACCESS*, then
  *Connect* on the VPN that has a **Clear Certificate** button. If none of your VPNs has one, set `"VPN_NAME"` in
  config.json to the VPN you want (e.g. `"NQX Taytay_IPSEC"`). Keep FortiClient's window visible and don't touch the
  mouse while it runs.
