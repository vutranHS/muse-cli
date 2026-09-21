#!/usr/bin/env python3
"""Muse image-gen farm: prompt -> base64 image, across rotating accounts.

Thin wrapper over muse-cli's gateway client (muse.py). Each account is a
muse.ai cookies file in accounts/<name>.txt. On quota/auth failure it rotates
to the next account.

Usage:
  musegen.py "a red apple"                  # rotate accounts, print base64
  musegen.py "a red apple" --account bob    # force one account
  musegen.py "a red apple" --out out.webp   # also save the webp
  musegen.py --list                         # list accounts
  # one-shot with a directly-supplied token (bypass cookies, for testing):
  MUSE_VM_ID=<id> MUSE_HATCH_TOKEN=<s0:...> musegen.py "a red apple" --token-only
"""
import argparse, base64, glob, json, os, re, secrets, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "vendor"))  # vendored muse-cli gateway client
import muse

ACCOUNTS_DIR = os.path.join(HERE, "accounts")

# reply text that means "you're out of quota" -> rotate to next account.
QUOTA_RX = re.compile(r"(quota|limit|rate.?limit|too many|try again later|"
                      r"out of|exceeded|upgrade|premium)", re.I)


class Quota(Exception):
    pass


def accounts():
    return sorted(glob.glob(os.path.join(ACCOUNTS_DIR, "*.txt")))


def _connect_cookies(path):
    cookies = muse.load_cookies(path)
    if not cookies.strip():
        raise muse.AuthError(f"empty cookies: {path}")
    return muse.Gateway(cookies)  # mints fresh access+hatch tokens


def _connect_token():
    vm = os.environ["MUSE_VM_ID"]
    tok = os.environ["MUSE_HATCH_TOKEN"]
    return muse.Gateway(cookies="", vm_id=vm, access_token="unused", hatch_token=tok)


def _find_new_image(gw, prompt, baseline, deadline):
    """Poll chat.history for an assistant image reply newer than baseline."""
    while time.time() < deadline:
        try:
            h = gw.call_json("chat.history", body={"limit": 15})
        except (muse.GatewayError, TimeoutError):
            time.sleep(3); continue
        for ev in sorted(h.get("chat_events", []), key=lambda e: e.get("seq", 0)):
            if ev.get("seq", 0) <= baseline:
                continue
            blob = json.dumps(ev)
            # a quota/refusal text reply (no image) -> rotate
            if '"role":"assistant"' in blob or ev.get("role") == "assistant":
                m = re.search(r'workspace/imagine_media/[^"\\]+?\.webp', blob)
                if m:
                    return m.group(0)
                txt = (ev.get("payload", {}) or {}).get("display_text") \
                    or ev.get("display_text") or ""
                if txt and QUOTA_RX.search(txt):
                    raise Quota(txt[:200])
        time.sleep(3)
    return None


def gen_once(gw, prompt, wait=150):
    baseline = 0
    h = gw.call_json("chat.history", body={"limit": 1})
    evs = h.get("chat_events", [])
    baseline = max([e.get("seq", 0) for e in evs], default=0)

    params = {"items": [{"type": "text", "text": prompt}],
              "node_id": secrets.token_hex(8), "capabilities": {}}
    gw._open("chat.stream", body=params)

    path = _find_new_image(gw, prompt, baseline, time.time() + wait)
    if not path:
        raise TimeoutError(f"no image within {wait}s")
    muse.ROUTES["media.raw"] = {"method": "media.raw", "http": "GET",
                               "path": "/media/raw/" + path,
                               "responseType": "binary", "channel": "media"}
    data = gw.request("media.raw", timeout=30)
    if data[:4] not in (b"RIFF", b"\xff\xd8\xff\xe0", b"\x89PNG"):
        raise RuntimeError(f"not an image: {data[:16]!r}")
    return path, data


def gen(prompt, account=None, token_only=False, wait=150):
    """Return (account_name, image_path, bytes). Rotates on quota/auth error."""
    if token_only:
        gw = _connect_token()
        try:
            return ("(token)", *gen_once(gw, prompt, wait))
        finally:
            gw.close()

    files = [os.path.join(ACCOUNTS_DIR, account + ".txt")] if account else accounts()
    if not files:
        raise SystemExit(f"no accounts in {ACCOUNTS_DIR} (see --help)")
    last = None
    for f in files:
        name = os.path.splitext(os.path.basename(f))[0]
        try:
            gw = _connect_cookies(f)
        except muse.AuthError as e:
            last = f"{name}: auth {e}"; print(f"[skip] {last}", file=sys.stderr); continue
        try:
            path, data = gen_once(gw, prompt, wait)
            return name, path, data
        except Quota as e:
            last = f"{name}: quota {e}"; print(f"[rotate] {last}", file=sys.stderr)
        except (muse.GatewayError, TimeoutError, RuntimeError) as e:
            last = f"{name}: {e}"; print(f"[rotate] {last}", file=sys.stderr)
        finally:
            gw.close()
    raise SystemExit(f"all accounts exhausted; last: {last}")


def main():
    ap = argparse.ArgumentParser(description="Muse prompt -> base64 image (rotating accounts)")
    ap.add_argument("prompt", nargs="?", help="image prompt")
    ap.add_argument("--account", help="force one account (name without .txt)")
    ap.add_argument("--out", help="also save the raw webp to this path")
    ap.add_argument("--wait", type=int, default=150, help="seconds to wait for the image")
    ap.add_argument("--token-only", action="store_true",
                    help="use MUSE_VM_ID + MUSE_HATCH_TOKEN env, skip cookies")
    ap.add_argument("--list", action="store_true", help="list accounts and exit")
    a = ap.parse_args()

    if a.list:
        for f in accounts():
            print(os.path.splitext(os.path.basename(f))[0])
        return
    if not a.prompt:
        ap.error("prompt required")

    name, path, data = gen(a.prompt, a.account, a.token_only, a.wait)
    if a.out:
        open(a.out, "wb").write(data)
    b64 = base64.b64encode(data).decode()
    # machine-readable line on stdout; diagnostics already on stderr
    print(json.dumps({"account": name, "path": path, "bytes": len(data),
                      "b64": b64}))


if __name__ == "__main__":
    main()
