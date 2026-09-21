#!/bin/zsh
# Usage: ./add-account.sh <name> 'hatch_sess=...; datr=...'
# Saves accounts/<name>.txt (chmod 600) and verifies it can connect.
set -e
here="${0:A:h}"
name="$1"; cookies="$2"
if [ -z "$name" ] || [ -z "$cookies" ]; then
  echo "usage: $0 <name> 'hatch_sess=...; datr=...'"; exit 2
fi
f="$here/accounts/$name.txt"
printf '%s\n' "$cookies" > "$f"; chmod 600 "$f"
echo "saved $f — verifying..."
"$here/.venv/bin/python" -c "
import sys
sys.path.insert(0, '$here/vendor')
import muse
gw = muse.Gateway(muse.load_cookies('$f'))
h = gw.call_json('chat.history', body={'limit': 1})
print('OK: connected, vm', gw.vm_id, '| chat_events', len(h.get('chat_events', [])))
gw.close()
" && echo "ACCOUNT '$name' READY" \
  || { echo "VERIFY FAILED — cookies sai/thiếu hatch_sess, hoặc account không vào được muse.ai"; exit 1; }
