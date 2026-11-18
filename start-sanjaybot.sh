#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$HOME/allien"
APP="$PROJECT_DIR/app.py"
VENV="$PROJECT_DIR/.venv"

if [[ ! -f "$APP" ]]; then
    echo "SANJAYBOT app.py was not found at:"
    echo "  $APP"
    echo
    echo "Make sure the updated app.py is inside ~/allien."
    read -r -p "Press Enter to close..."
    exit 1
fi

cd "$PROJECT_DIR"

if [[ -f "$VENV/bin/activate" ]]; then
    # Use the project's virtual environment when it exists.
    source "$VENV/bin/activate"
    PYTHON="$VENV/bin/python"
else
    # Fall back to the system Python if no project venv exists.
    PYTHON="$(command -v python3 || true)"
    if [[ -z "$PYTHON" ]]; then
        echo "Python 3 was not found. Install it before starting SANJAYBOT."
        read -r -p "Press Enter to close..."
        exit 1
    fi
fi

echo "Starting SANJAYBOT..."
echo "Project: $PROJECT_DIR"
echo
"$PYTHON" "$APP"
status=$?

echo
if [[ $status -ne 0 ]]; then
    echo "SANJAYBOT exited with error code $status."
else
    echo "SANJAYBOT closed."
fi
read -r -p "Press Enter to close this window..."
exit "$status"
