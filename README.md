# muse-farm

Headless image generation for **Muse** (Meta *hatch* backend) across multiple
accounts. Prompt in → base64 (WebP) out. Rotates accounts to beat per-account
quota. No app UI, no browser at gen time.

## How it works
Muse gen runs over an encrypted **Noise-XX WebSocket** to a personal VM
(`wss://hatch.metaaivm.com/v1/noise`). Gen is an agent action: send a draw
prompt → the assistant reply carries an image at
`workspace/imagine_media/media-generation-*.webp`, fetched over the gateway.

The Noise/protobuf gateway client is vendored from
[nikships/muse-cli](https://github.com/nikships/muse-cli) (MIT, `vendor/`).
`musegen.py` is a thin wrapper: send prompt, pick the newest image (`seq`),
fetch bytes, base64.

## Setup
```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

## Accounts (one-time per account)
Credential = **muse.ai cookies** (`hatch_sess` + `datr`). Fresh gateway tokens
are minted each run from these.

1. Log in to https://muse.ai/ in a browser.
2. Save cookies to `accounts/<name>.txt`, curl-style:
   `hatch_sess=...; datr=...`
   (Netscape cookie-jar files also work.)

## Usage
```bash
# rotate through all accounts, print JSON {account, path, bytes, b64}
./.venv/bin/python musegen.py "a red apple"

# force one account, also save the webp
./.venv/bin/python musegen.py "a red apple" --account bob --out apple.webp

./.venv/bin/python musegen.py --list

# one-shot with a directly-supplied token (testing; bypasses cookies)
MUSE_VM_ID=<id> MUSE_HATCH_TOKEN='s0:...' ./.venv/bin/python musegen.py "a red apple" --token-only
```

On quota/auth failure for an account, it prints `[rotate] ...` and moves to the
next account file. Exits non-zero only when all accounts are exhausted.

## Notes
- Directly-supplied gateway tokens expire in ~hours; cookies re-mint fresh
  tokens automatically.
- Requires SIP nothing at runtime — LLDB/debug was only used during reverse
  engineering, not for normal operation.
