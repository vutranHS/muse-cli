#!/usr/bin/env python3
"""OpenAI-compatible image server backed by musegen (rotating Muse accounts).

  POST /v1/images/generations
    body: {"model": "muse[/<account>]", "prompt": "...", "n": 1,
           "response_format": "b64_json"}
    ->   {"created": <ts>, "data": [{"b64_json": "..."}]}

  GET /v1/models   -> lists muse + one entry per onboarded account
  GET /healthz     -> {"ok": true}

Model string: "muse" or "muse/default" = rotate all accounts;
"muse/<name>" = force that account. (9router sends provider/model, so the
part after the slash is the account selector.)

Run:  ./.venv/bin/python server.py            # 127.0.0.1:8799
      MUSE_PORT=8799 MUSE_HOST=127.0.0.1 ...
"""
import base64, glob, json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import musegen  # gen(), accounts()

HOST = os.environ.get("MUSE_HOST", "127.0.0.1")
PORT = int(os.environ.get("MUSE_PORT", "8799"))
WAIT = int(os.environ.get("MUSE_WAIT", "300"))
# how long a request may wait in the queue for a free account before 429
QUEUE_WAIT = int(os.environ.get("MUSE_QUEUE_WAIT", "900"))


class _Pool:
    """One in-flight gen per account (a VM allows one WS at a time). Concurrent
    requests beyond the account count wait here instead of erroring."""
    def __init__(self):
        self._cond = threading.Condition()
        self._inuse = set()
        self._last = None  # last account handed out (round-robin cursor)

    def _names(self):
        return [os.path.splitext(os.path.basename(f))[0] for f in musegen.accounts()]

    def acquire(self, deadline, only=None, exclude=()):
        with self._cond:
            while True:
                names = self._names()
                if only:
                    pick = only if (only not in self._inuse and only not in exclude
                                    and only in names) else None
                else:
                    # round-robin: start right after the last-used account and take
                    # the first free one, so load spreads evenly across accounts.
                    n = len(names)
                    start = (names.index(self._last) + 1) if self._last in names else 0
                    pick = None
                    for k in range(n):
                        cand = names[(start + k) % n]
                        if cand not in self._inuse and cand not in exclude:
                            pick = cand
                            break
                if pick:
                    self._inuse.add(pick)
                    if not only:
                        self._last = pick
                    return pick
                # nothing free (or the forced one is busy) -> wait for a release
                rem = deadline - time.time()
                if rem <= 0 or not names:
                    return None
                self._cond.wait(min(rem, 5))

    def release(self, name):
        with self._cond:
            self._inuse.discard(name)
            self._cond.notify_all()


POOL = _Pool()


def gen_pooled(prompt, only=None, deadline=None):
    """Acquire a free account (queueing if all busy), gen, release. On a
    per-account failure, drop it for this request and try another."""
    if deadline is None:
        deadline = time.time() + QUEUE_WAIT
    tried, last = set(), None
    while time.time() < deadline:
        acc = POOL.acquire(deadline, only=only, exclude=tried)
        if acc is None:
            break
        try:
            return musegen.gen(prompt, account=acc, wait=WAIT)
        except musegen.Quota as e:
            last = f"{acc}: quota {e}"
        except SystemExit as e:            # single-account gen exhausted (busy/auth)
            last = f"{acc}: {e}"
        except Exception as e:
            last = f"{acc}: {e}"
        finally:
            POOL.release(acc)
        tried.add(acc)                     # don't reuse a failed account this request
    raise RuntimeError(f"queue timeout / all accounts unavailable; last: {last}")


_HQ = "high resolution, high quality, sharp, clean crisp edges"
_RATIO = {
    "1024x1024": "1:1", "512x512": "1:1", "2048x2048": "1:1",
    "1792x1024": "16:9", "1344x768": "16:9", "1920x1080": "16:9",
    "1024x1792": "9:16", "768x1344": "9:16", "1080x1920": "9:16",
    "1536x1024": "3:2", "1024x1536": "2:3",
}


def _ratio_for(size):
    s = (size or "").strip().lower()
    if not s or s == "auto":
        return None
    if s in _RATIO:
        return _RATIO[s]
    if "x" in s:  # bucket any WxH into the nearest common ratio
        try:
            w, h = (int(x) for x in s.split("x")[:2])
            r = w / h
        except Exception:
            return None
        if abs(r - 1) <= 0.1:
            return "1:1"
        if r >= 1.2:
            return "16:9"
        if r <= 0.83:
            return "9:16"
        return "1:1"
    return s if ":" in s else None  # allow passing "1:1"/"9:16" directly


def augment_prompt(prompt, body):
    parts = [prompt]
    ratio = _ratio_for(body.get("size"))
    if ratio:
        # short ratio + general anti-distort (no shape assumption)
        parts.append(f"{ratio} aspect ratio; keep the artwork's original "
                     "proportions, do not stretch, squash, or distort any element")
    # highest quality by default (safe, style-neutral words); opt out with
    # quality=standard/low.
    q = (body.get("quality") or "hd").lower()
    if q in ("hd", "high", "max", "maximum", "best"):
        parts.append(_HQ)
    return " — ".join(parts) if len(parts) > 1 else prompt


def account_from_model(model):
    """'muse' / 'muse/default' -> rotate (None); 'muse/<name>' -> that account."""
    if not model:
        return None
    part = model.split("/", 1)[1] if "/" in model else ""
    part = part or (model if model not in ("muse",) else "")
    return None if part in ("", "default", "muse") else part


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, msg, etype="invalid_request_error"):
        self._send(code, {"error": {"message": msg, "type": etype}})

    def log_message(self, *a):  # quieter
        sys.stderr.write("[muse-server] " + (a[0] % a[1:]) + "\n")

    def do_GET(self):
        if self.path.rstrip("/") == "/healthz":
            return self._send(200, {"ok": True})
        if self.path.rstrip("/") == "/v1/usage":
            import concurrent.futures as _cf
            def one(f):
                name = os.path.splitext(os.path.basename(f))[0]
                try:
                    q = musegen.account_quota(musegen.muse.load_cookies(f))
                    q["account"] = name
                    return q
                except Exception as e:
                    return {"account": name, "error": str(e)}
            with _cf.ThreadPoolExecutor(max_workers=8) as ex:
                rows = list(ex.map(one, musegen.accounts()))
            return self._send(200, {"object": "list", "data": rows})
        if self.path.rstrip("/") == "/v1/models":
            names = [os.path.splitext(os.path.basename(f))[0]
                     for f in musegen.accounts()]
            data = [{"id": "muse", "object": "model", "owned_by": "muse"}]
            data += [{"id": f"muse/{n}", "object": "model", "owned_by": "muse"}
                     for n in names]
            return self._send(200, {"object": "list", "data": data})
        return self._err(404, f"no route {self.path}")

    def do_POST(self):
        if self.path.split("?", 1)[0].rstrip("/") != "/v1/images/generations":
            return self._err(404, f"no route {self.path}")
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._err(400, "invalid JSON body")

        prompt = body.get("prompt")
        if not prompt:
            return self._err(400, "missing required field: prompt")
        account = account_from_model(body.get("model"))
        count = int(body.get("n", 1) or 1)
        rf = body.get("response_format", "b64_json")
        eff_prompt = augment_prompt(prompt, body)

        deadline = time.time() + QUEUE_WAIT
        data = []
        try:
            for _ in range(count):
                # queue for a free account instead of erroring when all are busy
                name, path, img = gen_pooled(eff_prompt, only=account, deadline=deadline)
                if rf == "url":
                    # no hosted URL; return a data URL so OpenAI clients still work
                    b64 = base64.b64encode(img).decode()
                    data.append({"url": "data:image/webp;base64," + b64,
                                 "revised_prompt": prompt, "muse_account": name})
                else:
                    data.append({"b64_json": base64.b64encode(img).decode(),
                                 "revised_prompt": prompt, "muse_account": name})
        except Exception as e:
            return self._err(429, f"queue/accounts unavailable: {e}", "insufficient_quota")

        return self._send(200, {"created": int(time.time()), "data": data})


if __name__ == "__main__":
    srv = ThreadingHTTPServer((HOST, PORT), H)
    print(f"muse OpenAI-images server on http://{HOST}:{PORT}  "
          f"(POST /v1/images/generations)  accounts={len(musegen.accounts())}")
    srv.serve_forever()
