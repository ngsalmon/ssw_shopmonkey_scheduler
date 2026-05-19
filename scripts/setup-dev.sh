#!/usr/bin/env bash
# Create the local development venv and install dependencies.
#
# This isolates the project from system-installed Python packages (notably
# the apt-installed httplib2, which emits pyparsing deprecation warnings)
# and locks the interpreter version that matches the production Docker
# image (python:3.12-slim).

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

VENV_DIR=".venv"
MIN_PY_MAJOR=3
MIN_PY_MINOR=11

# Pick the best available interpreter. Preferred order: python3.12 (matches
# prod), python3.11, then anything python3 reports as >=3.11.
find_python() {
    local candidates=(
        "python3.12"
        "python3.11"
        "python3.13"
        "python3"
    )
    for cmd in "${candidates[@]}"; do
        if command -v "$cmd" >/dev/null 2>&1; then
            local version
            version=$("$cmd" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")') || continue
            local major minor
            major=$(echo "$version" | cut -d. -f1)
            minor=$(echo "$version" | cut -d. -f2)
            if [ "$major" -gt "$MIN_PY_MAJOR" ] || \
               { [ "$major" -eq "$MIN_PY_MAJOR" ] && [ "$minor" -ge "$MIN_PY_MINOR" ]; }; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON_BIN="$(find_python || true)"
if [ -z "${PYTHON_BIN:-}" ]; then
    echo "error: could not find a Python ${MIN_PY_MAJOR}.${MIN_PY_MINOR}+ interpreter."
    echo "Install python3.12 (matches production Docker image):"
    echo "  Ubuntu/Debian: sudo apt install python3.12 python3.12-venv"
    echo "  macOS:         brew install python@3.12"
    exit 1
fi

PYTHON_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}")')"
echo "Using $PYTHON_BIN (Python $PYTHON_VERSION)"

if [ -d "$VENV_DIR" ]; then
    echo "Removing existing $VENV_DIR (fresh install for reproducibility)..."
    rm -rf "$VENV_DIR"
fi

echo "Creating venv at $VENV_DIR..."
"$PYTHON_BIN" -m venv "$VENV_DIR"

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "Upgrading pip..."
pip install --quiet --upgrade pip

echo "Installing project dependencies..."
pip install --quiet -r requirements.txt

echo
echo "Setup complete. Activate the venv with:"
echo "    source $VENV_DIR/bin/activate"
echo
echo "Run tests with:"
echo "    pytest                    # default suite (skips integration)"
echo "    pytest -m integration     # integration tests (requires real creds)"
echo "    cd e2e && npm test        # Playwright e2e (requires npm install first)"
