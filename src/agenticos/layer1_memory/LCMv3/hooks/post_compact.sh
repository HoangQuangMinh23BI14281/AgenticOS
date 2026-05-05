#!/bin/bash
# LCMv3: Record compaction event (NO context injection)
# CRITICAL: All output suppressed to prevent compaction loops
exec >/dev/null 2>&1

SCRIPTS_DIR="${LOSSLESS_HOME:-$HOME/.lossless-code}"
COOLDOWN_FILE="/tmp/.lcmv3-postcompact-cooldown"
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

python3 "$SCRIPTS_DIR/hook_store_message.py" \
    --session "$SESSION_ID" --role tool --tool-name "compaction" \
    --content "[Context compaction occurred at $(date -u +%Y-%m-%dT%H:%M:%SZ)]" \
    >/dev/null 2>&1 || true

exit 0
