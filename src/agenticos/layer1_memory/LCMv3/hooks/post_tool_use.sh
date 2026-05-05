#!/bin/bash
# LCMv3: Record tool call results on PostToolUse
set -euo pipefail

SCRIPTS_DIR="${LOSSLESS_HOME:-$HOME/.lossless-code}"
INPUT=$(cat)

eval "$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'SESSION_ID={json.dumps(d.get(\"session_id\", \"\"))}')
print(f'CWD={json.dumps(d.get(\"cwd\", \"\"))}')
print(f'TOOL_NAME={json.dumps(d.get(\"tool_name\", \"\"))}')
print(f'TOOL_INPUT={json.dumps(json.dumps(d.get(\"tool_input\", {})))}')
print(f'TOOL_OUTPUT={json.dumps(json.dumps(d.get(\"tool_output\", {}))[:500])}')
" 2>/dev/null || echo 'SESSION_ID=""; CWD=""; TOOL_NAME=""; TOOL_INPUT="{}"; TOOL_OUTPUT="{}"')"

if [ -z "$SESSION_ID" ] || [ -z "$TOOL_NAME" ]; then exit 0; fi

python3 "$SCRIPTS_DIR/hook_store_tool_call.py" \
    --session "$SESSION_ID" --dir "$CWD" --tool-name "$TOOL_NAME" \
    --input-json "$TOOL_INPUT" --output-json "$TOOL_OUTPUT" \
    2>/dev/null || true

exit 0
