#!/bin/sh
# Piercing WM - Pre-commit gate script
# Runs py_compile on all sources + pytest + shellcheck

set -e

echo "=== Checking Python syntax ==="
python3 -m py_compile launcher/src/*.py || {
    echo "Python syntax check FAILED"
    exit 1
}

echo "=== Running pytest ==="
if command -v pytest >/dev/null 2>&1; then
    python3 -m pytest tests/ -q || {
        echo "pytest FAILED"
        exit 1
    }
else
    echo "pytest not available, skipping"
fi

echo "=== Running shellcheck ==="
if command -v shellcheck >/dev/null 2>&1; then
    for f in scripts/*.sh devices/*/*.sh; do
        if [ -f "$f" ]; then
            shellcheck -s sh "$f" || {
                echo "shellcheck FAILED on $f"
                exit 1
            }
        fi
    done
else
    echo "shellcheck not available, skipping"
fi

echo "=== All checks passed ==="
