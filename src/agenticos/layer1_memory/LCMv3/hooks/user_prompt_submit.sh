#!/bin/bash
# LCMv3: Store user prompt on UserPromptSubmit
set -euo pipefail

SCRIPTS_DIR="${LOSSLESS_HOME:-$HOME/.lossless-code}"
INPUT=$(cat)

eval "$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'SESSION_ID={json.dumps(d.get(\"session_id\", \"\"))}')
print(f'CWD={json.dumps(d.get(\"cwd\", \"\"))}')
" 2>/dev/null || echo 'SESSION_ID=""; CWD=""')"

if [ -z "$SESSION_ID" ]; then exit 0; fi

# Ensure session exists
python3 "$SCRIPTS_DIR/hook_session_start.py" --session "$SESSION_ID" --dir "$CWD" 2>/dev/null || true

exit 0
