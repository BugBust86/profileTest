#!/bin/bash
# Stable entry point; paths are resolved relative to this script.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  echo "错误：需要 Python 3.9+。macOS 可执行 brew install python。" >&2
  exit 1
fi
exec python3 "$SCRIPT_DIR/collector.py" "$@"

