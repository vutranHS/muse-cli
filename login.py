#!/usr/bin/env python3
"""Interactive account onboarding via Playwright.

Opens a real browser to muse.ai; you log in with Facebook; the moment the
`hatch_sess` cookie appears it grabs all cookies (incl. HttpOnly) and writes
accounts/<name>.txt, then verifies the gateway connects.

  ./.venv/bin/python login.py acc1

Each account gets its own persistent profile under profiles/<name>, so the
Facebook session (and 2FA trust) is remembered for next time.
"""
import json, os, re, sys, time, urllib.parse
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

    captured = {"email": None}
    full_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    def better(cur, new):
        # prefer a complete email over a partial; else keep the longer value
        if not new:
            return cur
        if full_re.match(new) and not (cur and full_re.match(cur)):
            return new
        if cur and full_re.match(cur) and not full_re.match(new):
            return cur
        return new if len(new) > len(cur or "") else cur

    def rec(source, val):
        v = (val or "").strip()
        if v and ("@" in v or v.replace("+", "").isdigit()):
            captured["email"] = better(captured["email"], v)

    # Primary capture: read the email field at the moment the user clicks
    # "Continue"/submits (so we get the full value, not a mid-typing snapshot).
    click_js = r"""
      function grab(){
        for (const el of document.querySelectorAll('input')) {
          var s=((el.name||'')+' '+(el.id||'')+' '+(el.type||'')+' '+
                 ((el.autocomplete)||'')).toLowerCase();
          if ((el.type==='email' || /email|username|phone|contact|login|user/.test(s))
              && el.value) { try{ window.__recEmail(el.value); }catch(_){} return; }
        }
      }
      document.addEventListener('click', grab, true);
      document.addEventListener('submit', grab, true);
      document.addEventListener('keydown', function(e){ if(e.key==='Enter') grab(); }, true);
    """

    scan_js = """() => {
      for (const el of document.querySelectorAll('input')) {
        const s=((el.name||'')+' '+(el.id||'')+' '+(el.type||'')+' '+
                 ((el.autocomplete)||'')).toLowerCase();
        if ((el.type==='email' || /email|username|phone|contact|login|user/.test(s))
            && el.value && el.value.length>=4) return el.value;
      }
      return null;
    }"""

    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                prof, headless=False, channel="chrome",
                args=["--no-first-run", "--no-default-browser-check"])
        except Exception:
            ctx = p.chromium.launch_persistent_context(prof, headless=False)
        ctx.expose_binding("__recEmail", rec)
        ctx.add_init_script(click_js)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://muse.ai/", wait_until="domcontentloaded")
        print("→ Log in with Facebook in the opened window. Waiting for session…")

        def scan_email():
            for pg in ctx.pages:
                for fr in pg.frames:
                    try:
                        v = fr.evaluate(scan_js)
                    except Exception:
                        continue
                    if v:
                        captured["email"] = better(captured["email"], v.strip())

        deadline = time.time() + timeout
        got = None
        while time.time() < deadline:
            scan_email()  # fallback if click capture missed
            names = {c["name"] for c in ctx.cookies()}
            if "hatch_sess" in names:
                got = ctx.cookies()
                break
            time.sleep(0.5)
        scan_email()
        if not got:
            ctx.close()
            raise SystemExit("timed out waiting for login (no hatch_sess cookie)")

        cs = cookie_string(got)
        uid = next((c["value"] for c in got if c["name"] == "c_user"), None)
        with open(out, "w") as fh:
            fh.write(cs + "\n")
        os.chmod(out, 0o600)
        meta = {"name": name, "email": captured["email"], "fb_uid": uid,
                "saved_at": int(time.time())}
        with open(os.path.join(HERE, "accounts", f"{name}.json"), "w") as fh:
            json.dump(meta, fh, indent=2)
        ctx.close()
    print(f"saved {out}  email={captured['email']!r} fb_uid={uid}")

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
