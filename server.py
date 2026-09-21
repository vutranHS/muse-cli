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

        data = []
        try:
            for _ in range(count):
                name, path, img = musegen.gen(prompt, account=account, wait=WAIT)
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
