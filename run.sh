#!/usr/bin/env bash
# PicMe — macOS / Linux launcher
# Usage: ./run.sh [port]   (default port: 8000)
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$REPO_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "ERROR: virtualenv not found at .venv/"
  echo "Run the setup steps in README.md → 'Running on macOS' first."
  exit 1
fi

cd "$REPO_DIR"
exec "$PYTHON" backend/main.py
