#!/usr/bin/env python3
"""Interactive account onboarding via Playwright.

Opens a real browser to muse.ai; you log in with Facebook; the moment the
`hatch_sess` cookie appears it grabs all cookies (incl. HttpOnly) and writes
accounts/<name>.txt, then verifies the gateway connects.

  ./.venv/bin/python login.py acc1

Each account gets its own persistent profile under profiles/<name>, so the
Facebook session (and 2FA trust) is remembered for next time.
"""
import os, sys, time
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "vendor"))
import muse


def cookie_string(cookies):
    # keep what the muse.ai auth endpoints need; dedupe by name (last wins)
    keep = {}
    for c in cookies:
        d = c.get("domain", "")
        if "muse.ai" in d or "facebook.com" in d or d.endswith("meta.com"):
            keep[c["name"]] = c["value"]
    return "; ".join(f"{k}={v}" for k, v in keep.items())


def onboard(name, timeout=600):
    prof = os.path.join(HERE, "profiles", name)
    os.makedirs(prof, exist_ok=True)
    out = os.path.join(HERE, "accounts", f"{name}.txt")

    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                prof, headless=False, channel="chrome",
                args=["--no-first-run", "--no-default-browser-check"])
        except Exception:
            ctx = p.chromium.launch_persistent_context(prof, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://muse.ai/", wait_until="domcontentloaded")
        print("→ Log in with Facebook in the opened window. Waiting for session…")

        deadline = time.time() + timeout
        got = None
        while time.time() < deadline:
            names = {c["name"] for c in ctx.cookies()}
            if "hatch_sess" in names:
                got = ctx.cookies()
                break
            time.sleep(2)
        if not got:
            ctx.close()
            raise SystemExit("timed out waiting for login (no hatch_sess cookie)")

        cs = cookie_string(got)
        with open(out, "w") as fh:
            fh.write(cs + "\n")
        os.chmod(out, 0o600)
        ctx.close()
    print(f"saved {out}")

    # verify
    gw = muse.Gateway(muse.load_cookies(out))
    try:
        h = gw.call_json("chat.history", body={"limit": 1})
        print(f"OK: connected, vm {gw.vm_id} | chat_events {len(h.get('chat_events', []))}")
        print(f"ACCOUNT '{name}' READY")
    finally:
        gw.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: login.py <account-name>")
    onboard(sys.argv[1])
