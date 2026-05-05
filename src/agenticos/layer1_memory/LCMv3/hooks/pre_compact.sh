#!/bin/bash
# LCMv3: Persist messages to vault BEFORE Claude discards them
# CRITICAL: All output suppressed to prevent compaction loops
exec >/dev/null 2>&1

SCRIPTS_DIR="${LOSSLESS_HOME:-$HOME/.lossless-code}"
COOLDOWN_FILE="/tmp/.lcmv3-precompact-cooldown"
COOLDOWN_SECS=60

if [ -f "$COOLDOWN_FILE" ]; then
    LAST=$(cat "$COOLDOWN_FILE" 2>/dev/null || echo "0")
    NOW=$(date +%s)
    DIFF=$((NOW - LAST))
    if [ "$DIFF" -lt "$COOLDOWN_SECS" ] 2>/dev/null; then exit 0; fi
fi

INPUT=$(cat || echo '{}')
SESSION_ID=$(echo "$INPUT" | python3 -c "
import sys, json
try: print(json.load(sys.stdin).get('session_id', ''))
except Exception: print('')
" 2>/dev/null || echo "")

if [ -z "$SESSION_ID" ]; then exit 0; fi

date +%s > "$COOLDOWN_FILE" 2>/dev/null || true

nohup python3 "$SCRIPTS_DIR/summarise.py" --session "$SESSION_ID" </dev/null >/dev/null 2>&1 &
disown 2>/dev/null || true

exit 0
