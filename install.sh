#!/usr/bin/env bash
# Zeus v5.1 installer — Kali / Debian / Ubuntu / Arch
#
# Installs:
#   • Python deps from requirements.txt
#   • sherlock, maigret, holehe (via pipx)
#   • symlink ~/.local/bin/zeus → ./zeus.py
#
# Does NOT touch ~/.bashrc unless you ask (interactive prompt).

set -e

# ── Colors ────────────────────────────────────────────────────────
if [ -t 1 ]; then
  RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'
  CYN=$'\033[36m'; DIM=$'\033[90m'; RST=$'\033[0m'; BLD=$'\033[1m'
else
  RED=""; GRN=""; YEL=""; CYN=""; DIM=""; RST=""; BLD=""
fi

say()  { printf "%s\n" "${CYN}[zeus]${RST} $*"; }
ok()   { printf "%s\n" "${GRN}  ✓${RST} $*"; }
warn() { printf "%s\n" "${YEL}  ⚠${RST} $*"; }
err()  { printf "%s\n" "${RED}  ✕${RST} $*"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ZEUS_PY="$SCRIPT_DIR/zeus.py"

if [ ! -f "$ZEUS_PY" ]; then
  err "zeus.py not found in $SCRIPT_DIR"
  exit 1
fi

say "Zeus v5.1 installer"
say "working from: $SCRIPT_DIR"
echo

# ── Python check ──────────────────────────────────────────────────
if ! command -v python3 >/dev/null 2>&1; then
  err "python3 not installed — install python3 (>= 3.10) first"
  exit 1
fi

PY_VER=$(python3 -c 'import sys; print("{}.{}".format(*sys.version_info[:2]))')
ok "python3 $PY_VER"

# ── pipx check (the right way to install isolated CLI tools) ──────
if ! command -v pipx >/dev/null 2>&1; then
  warn "pipx not installed — needed for sherlock/maigret/holehe"
  warn "  Debian/Kali/Ubuntu:  sudo apt install pipx"
  warn "  Arch:                sudo pacman -S python-pipx"
  warn "  Manual:              python3 -m pip install --user pipx && python3 -m pipx ensurepath"
  echo
  read -rp "  Continue without OSINT CLIs? [y/N] " ans
  case "$ans" in [yY]*) ;; *) exit 1 ;; esac
else
  ok "pipx present"
fi

# ── Python deps ───────────────────────────────────────────────────
say "installing python deps..."
if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
  # Try plain pip first, fall back to --break-system-packages (PEP 668)
  if pip install -r "$SCRIPT_DIR/requirements.txt" >/dev/null 2>&1; then
    ok "requirements.txt installed (pip)"
  elif pip install -r "$SCRIPT_DIR/requirements.txt" --break-system-packages >/dev/null 2>&1; then
    ok "requirements.txt installed (--break-system-packages)"
  else
    warn "pip failed — try manually:"
    warn "  pip install -r requirements.txt --break-system-packages"
  fi
else
  warn "requirements.txt not found — skipping python deps"
fi

# ── OSINT CLIs via pipx ───────────────────────────────────────────
if command -v pipx >/dev/null 2>&1; then
  say "installing osint clis (sherlock, maigret, holehe)..."

  if command -v sherlock >/dev/null 2>&1; then
    ok "sherlock already installed"
  else
    if pipx install sherlock-project >/dev/null 2>&1; then
      ok "sherlock installed"
    else
      warn "sherlock install failed — try manually: pipx install sherlock-project"
    fi
  fi

  if command -v maigret >/dev/null 2>&1; then
    ok "maigret already installed"
  else
    if pipx install maigret >/dev/null 2>&1; then
      ok "maigret installed"
    else
      warn "maigret install failed — try manually: pipx install maigret"
    fi
  fi

  if command -v holehe >/dev/null 2>&1; then
    ok "holehe already installed"
  else
    if pipx install holehe >/dev/null 2>&1; then
      ok "holehe installed"
    else
      warn "holehe install failed — try manually: pipx install holehe"
    fi
  fi
fi

# ── Make zeus.py executable ───────────────────────────────────────
chmod +x "$ZEUS_PY"
ok "zeus.py marked executable"

# ── Symlink as `zeus` ─────────────────────────────────────────────
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
LINK="$BIN_DIR/zeus"
ln -sf "$ZEUS_PY" "$LINK"
ok "symlinked $LINK → $ZEUS_PY"

# Warn if ~/.local/bin not on PATH
case ":$PATH:" in
  *":$BIN_DIR:"*) ok "$BIN_DIR is on PATH" ;;
  *)
    warn "$BIN_DIR is NOT on PATH — add this to your shell rc:"
    warn "  export PATH=\"\$HOME/.local/bin:\$PATH\""
    ;;
esac

# ── Groq API key check ────────────────────────────────────────────
echo
if [ -n "${GROQ_API_KEY:-}" ]; then
  ok "GROQ_API_KEY is set — AI summary paragraph will work"
else
  warn "GROQ_API_KEY not set — AI summary will be skipped (everything else works)"
  warn "  Get a free key at: https://console.groq.com"
  warn "  Then add to your shell rc:  export GROQ_API_KEY=gsk_..."
fi

echo
say "${BLD}done.${RST}  run:  ${GRN}zeus${RST}"
