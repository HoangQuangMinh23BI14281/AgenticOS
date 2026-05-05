#!/bin/bash
# LCMv3: File context fingerprint on PreToolUse
set -euo pipefail

SCRIPTS_DIR="${LOSSLESS_HOME:-$HOME/.lossless-code}"
INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null || echo "")

case "$TOOL_NAME" in
    Read|Edit|MultiEdit|Write|NotebookEdit) ;;
    *) exit 0 ;;
esac

FILE_PATH=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
inp = d.get('tool_input', {})
print(inp.get('file_path', inp.get('path', '')))
" 2>/dev/null || echo "")

if [ -z "$FILE_PATH" ]; then exit 0; fi

FINGERPRINT=$(python3 "$SCRIPTS_DIR/file_context.py" --file "$FILE_PATH" 2>/dev/null || echo "")

if [ -n "$FINGERPRINT" ]; then
    python3 -c "
import json, sys
fp = sys.stdin.read()
if fp.strip():
    print(json.dumps({
        'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'additionalContext': fp
        }
    }))
" <<< "$FINGERPRINT"
fi

exit 0
