#!/bin/bash
# macOS ships Bash 3.2. Keep this bootstrap independent of Python and Homebrew.
set -eu
ROOT="$(cd "$(dirname "$0")" && pwd -P)"
fail() { printf '\nSetup stopped: %s\n' "$1" >&2; exit 1; }
checksum() { LC_ALL=C LC_CTYPE=C LANG=C /usr/bin/shasum -a 256 "$1" | /usr/bin/awk '{print $1}'; }
if [ "${1:-}" = "--help" ]; then
  printf '%s\n' 'Semantic Search — Apple Silicon Mac quickstart' \
    'bash "Get Started.command"                 Install and open the interactive toy demo' \
    'bash "Get Started.command" --check         Check HTTP + MCP, then close the service' \
    'bash "Get Started.command" --setup-only    Install runtime and embedding weights only' \
    'bash "Get Started.command" --repo /path/to/repo --source-root src' \
    'Requires macOS on Apple Silicon, working Git, internet for first setup, and at least 6 GB free.'
  exit 0
fi
[ "$(/usr/bin/uname -s)" = Darwin ] && [ "$(/usr/bin/uname -m)" = arm64 ] || fail 'This launcher requires an Apple Silicon Mac (arm64 macOS).'
command -v git >/dev/null 2>&1 && git --version >/dev/null 2>&1 || fail 'Install Apple command line tools with: xcode-select --install. Finish that installation, then run this launcher again.'
WORK="$ROOT/.cache/quickstart"
[ ! -L "$ROOT/.cache" ] && [ ! -L "$WORK" ] || fail 'The reserved .cache/quickstart directory must not be a symlink.'
for DIRECTORY in "$WORK/bootstrap" "$WORK/python" "$WORK/uv-cache"; do
  [ ! -L "$DIRECTORY" ] || fail "Reserved setup directory must not be a symlink: $DIRECTORY"
done
mkdir -p "$WORK/bootstrap" "$WORK/python" "$WORK/uv-cache"
UV="$WORK/bootstrap/uv"
[ ! -L "$UV.sha256" ] || fail 'Cached uv checksum must not be a symlink.'
UV_SHA='a9c1b29002cf3c83f07fa9cd8a887a3be0107d90e23189721221e7257db8e3d6'
DOWNLOAD=''
cleanup() { if [ -n "$DOWNLOAD" ] && [ -d "$DOWNLOAD" ]; then /bin/rm -rf "$DOWNLOAD"; fi; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [ -e "$UV" ] || [ -L "$UV" ]; then
  [ -f "$UV" ] && [ ! -L "$UV" ] && [ -f "$UV.sha256" ] || fail 'Unrecognized cached uv binary; move .cache/quickstart/bootstrap aside and retry.'
  ACTUAL="$(checksum "$UV")"
  [ "$ACTUAL" = "$(/bin/cat "$UV.sha256")" ] || fail 'Cached uv changed; move .cache/quickstart/bootstrap aside and retry.'
else
  printf '\nDownloading the pinned local setup tool (uv 0.13.0, 14 MB)…\n'
  DOWNLOAD="$(/usr/bin/mktemp -d "$WORK/bootstrap/download.XXXXXX")"
  /usr/bin/curl --fail --location --proto '=https' --tlsv1.2 --retry 2 --connect-timeout 30 \
    'https://github.com/astral-sh/uv/releases/download/0.13.0/uv-aarch64-apple-darwin.tar.gz' \
    --output "$DOWNLOAD/uv.tar.gz"
  ACTUAL="$(checksum "$DOWNLOAD/uv.tar.gz")"
  [ "$ACTUAL" = "$UV_SHA" ] || fail 'Downloaded uv checksum differs from the pinned release.'
  /usr/bin/tar -xOf "$DOWNLOAD/uv.tar.gz" uv-aarch64-apple-darwin/uv > "$DOWNLOAD/uv"
  /bin/chmod 755 "$DOWNLOAD/uv"
  /bin/mv "$DOWNLOAD/uv" "$UV"
  checksum "$UV" > "$UV.sha256"
fi
case "$("$UV" --version)" in 'uv 0.13.0'|'uv 0.13.0 ('*) ;; *) fail 'Expected the pinned uv 0.13.0 release.' ;; esac
export UV_PYTHON_INSTALL_DIR="$WORK/python"
export UV_CACHE_DIR="$WORK/uv-cache"
export UV_NO_CONFIG=1
printf '\nPreparing local Python 3.12 (no system Python or Homebrew required)…\n'
"$UV" python install 3.12 --install-dir "$WORK/python" --no-bin --no-config
PYTHON="$("$UV" python find 3.12 --managed-python --no-project --no-config)"
cleanup
DOWNLOAD=''
exec "$PYTHON" "$ROOT/scripts/quickstart_mac.py" --uv-path "$UV" "$@"
