#!/bin/sh
set -eu

PREFIX="${CHATGPTPROXY_PREFIX:-$HOME/.local/share/chatgptproxy}"
BIN_DIR="${CHATGPTPROXY_BIN_DIR:-$HOME/.local/bin}"
REPO_URL="https://github.com/sPROFFEs/GPT-webProxy.git"

# Discover a suitable Python 3.11+ interpreter
PYTHON=""
for candidate in "${PYTHON:-}" python3 python3.13 python3.12 python3.11 python; do
  if [ -n "$candidate" ] && command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  echo "Error: Python 3.11+ is required to install chatgptproxy." >&2
  exit 1
fi

echo "Using Python: $("$PYTHON" --version 2>&1) ($PYTHON)"
mkdir -p "$PREFIX" "$BIN_DIR"

if [ ! -d "$PREFIX/venv" ] || [ ! -f "$PREFIX/venv/bin/python" ]; then
  echo "Creating virtualenv at $PREFIX/venv..."
  rm -rf "$PREFIX/venv"
  "$PYTHON" -m venv "$PREFIX/venv"
fi

# Ensure pip is present in the virtualenv
if ! "$PREFIX/venv/bin/python" -m pip --version >/dev/null 2>&1; then
  echo "Bootstrapping pip inside venv..."
  "$PREFIX/venv/bin/python" -m ensurepip --upgrade 2>/dev/null || {
    curl -fsSL https://bootstrap.pypa.io/get-pip.py | "$PREFIX/venv/bin/python" - --no-warn-script-location --quiet
  }
fi

echo "Installing/upgrading dependencies..."
"$PREFIX/venv/bin/python" -m pip install --upgrade --quiet pip setuptools wheel

# Determine if we are running from a local checkout or via curl pipe
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE:-}" ] && [ -f "${BASH_SOURCE[0]:-}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
elif [ -f "./pyproject.toml" ]; then
  SCRIPT_DIR="$(pwd)"
fi

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
  echo "Installing chatgptproxy from local directory: $SCRIPT_DIR..."
  "$PREFIX/venv/bin/python" -m pip install --quiet "$SCRIPT_DIR"
else
  echo "Installing chatgptproxy from $REPO_URL..."
  "$PREFIX/venv/bin/python" -m pip install --quiet "git+$REPO_URL"
fi

ln -sf "$PREFIX/venv/bin/chatgptproxy" "$BIN_DIR/chatgptproxy"
if [ -f "$PREFIX/venv/bin/chatgpt-web2api" ]; then
  ln -sf "$PREFIX/venv/bin/chatgpt-web2api" "$BIN_DIR/chatgpt-web2api"
fi
if [ -f "$PREFIX/venv/bin/chatgpt-web2api-mcp" ]; then
  ln -sf "$PREFIX/venv/bin/chatgpt-web2api-mcp" "$BIN_DIR/chatgpt-web2api-mcp"
fi

# Check for Chrome / Chromium availability
CHROME_FOUND=""
for browser in google-chrome google-chrome-stable chromium chromium-browser chrome; do
  if command -v "$browser" >/dev/null 2>&1; then
    CHROME_FOUND="$browser"
    break
  fi
done

# Add $BIN_DIR to shell config if not already in PATH
PATH_BLOCK="export PATH=\"$BIN_DIR:\$PATH\""
case ":${PATH}:" in
  *":$BIN_DIR:"*) ;;
  *)
    for rcfile in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
      if [ -f "$rcfile" ] && ! grep -q "CHATGPTPROXY_BIN_DIR" "$rcfile" && ! grep -q "$BIN_DIR" "$rcfile"; then
        echo "" >> "$rcfile"
        echo "# chatgptproxy PATH" >> "$rcfile"
        echo "$PATH_BLOCK" >> "$rcfile"
      fi
    done
    ;;
esac

echo ""
echo "============================================================"
echo " chatgptproxy installed successfully!"
echo " Location: $BIN_DIR/chatgptproxy"
echo "============================================================"

if [ -z "$CHROME_FOUND" ]; then
  echo ""
  echo "Note: Chrome or Chromium was not detected on PATH."
  echo "ChatGPT Web requires Chrome/Chromium for browser sessions."
  if command -v apt-get >/dev/null 2>&1; then
    echo "To install Chromium on Debian/Ubuntu/Kali/Parrot:"
    echo "  sudo apt-get update && sudo apt-get install -y chromium"
  elif command -v dnf >/dev/null 2>&1; then
    echo "To install Chromium on Fedora/RHEL:"
    echo "  sudo dnf install -y chromium"
  elif command -v pacman >/dev/null 2>&1; then
    echo "To install Chromium on Arch:"
    echo "  sudo pacman -S chromium"
  fi
fi

echo ""
echo "Quick start:"
echo "  export PATH=\"$BIN_DIR:\$PATH\""
echo "  chatgptproxy guided"
echo "  chatgptproxy start"
echo ""
