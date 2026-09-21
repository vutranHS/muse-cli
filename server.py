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
import base64, glob, json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import musegen  # gen(), accounts()

HOST = os.environ.get("MUSE_HOST", "127.0.0.1")
PORT = int(os.environ.get("MUSE_PORT", "8799"))
WAIT = int(os.environ.get("MUSE_WAIT", "180"))


# Muse's Imagine model caps at ~2.3MP; resolution isn't a free knob, but aspect
# ratio and quality descriptors are honored via the prompt. Map the OpenAI
# size/quality fields onto natural-language hints.
_ASPECT = {
    "1792x1024": "16:9 widescreen", "1024x1792": "9:16 vertical",
    "1536x1024": "3:2 landscape", "1024x1536": "2:3 portrait",
    "1344x768": "16:9 widescreen", "768x1344": "9:16 vertical",
    "1024x1024": "1:1 square", "auto": "", "": "",
}
_HQ = ("ultra-detailed, sharp focus, high resolution, intricate detail, "
       "professional photography, best quality")


def augment_prompt(prompt, body):
    extra = []
    ar = _ASPECT.get((body.get("size") or "").lower())
    if ar is None:  # unknown WxH -> derive orientation
        s = (body.get("size") or "").lower()
        if "x" in s:
            try:
                w, h = (int(x) for x in s.split("x")[:2])
                ar = "16:9 widescreen" if w > h * 1.2 else "9:16 vertical" if h > w * 1.2 else "1:1 square"
            except Exception:
                ar = ""
        else:
            ar = ""
    if ar:
        extra.append(f"{ar} aspect ratio")
    # default to HD when the caller doesn't specify quality; honor an explicit
    # "standard"/"low" to opt out.
    q = (body.get("quality") or "hd").lower()
    if q in ("hd", "high", "max", "maximum", "best"):
        extra.append(_HQ)
    return prompt + (" — " + ", ".join(extra) if extra else "")


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

        data = []
        try:
            for _ in range(count):
                name, path, img = musegen.gen(eff_prompt, account=account, wait=WAIT)
                if rf == "url":
                    # no hosted URL; return a data URL so OpenAI clients still work
                    b64 = base64.b64encode(img).decode()
                    data.append({"url": "data:image/webp;base64," + b64,
                                 "revised_prompt": prompt, "muse_account": name})
                else:
                    data.append({"b64_json": base64.b64encode(img).decode(),
                                 "revised_prompt": prompt, "muse_account": name})
        except SystemExit as e:  # all accounts exhausted
            return self._err(429, str(e), "insufficient_quota")
        except Exception as e:
            return self._err(502, f"muse gen failed: {e}", "api_error")

        return self._send(200, {"created": int(time.time()), "data": data})


if __name__ == "__main__":
    srv = ThreadingHTTPServer((HOST, PORT), H)
    print(f"muse OpenAI-images server on http://{HOST}:{PORT}  "
          f"(POST /v1/images/generations)  accounts={len(musegen.accounts())}")
    srv.serve_forever()
