#!/usr/bin/env bash
# Start Lehmus Chat on Linux/macOS.
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "First start: setting up Python environment..."
  python3 -m venv .venv
fi
if ! cmp -s requirements.txt .venv/installed-requirements.txt; then
  .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt
  cp requirements.txt .venv/installed-requirements.txt
fi
exec .venv/bin/python launcher.py
