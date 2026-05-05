#!/bin/bash
# Start LCMv3 MCP server
# Sửa line endings tự động nếu chạy trên WSL
SED_CMD=$(command -v sed)
if [ -n "$SED_CMD" ]; then
    $SED_CMD -i 's/\r$//' "$0" 2>/dev/null || true
fi

# Tìm Python/UV
if command -v uv >/dev/null 2>&1; then
    exec uv run python3 "$(dirname "$0")/server.py" "$@"
else
    exec python3 "$(dirname "$0")/server.py" "$@"
fi
