#!/usr/bin/env bash
# Zeus installer — sets up Python deps, /usr/local/bin shortcut,
# and (optionally) GROQ_API_KEY + GITHUB_TOKEN in your shell rc.
#
# Mirrors the Athena and Ares installers: same layout, same shell-
# detection pattern, same fallback chain.  GROQ_API_KEY is shared
# across all three tools — if you already have it set for Athena
# or Ares, Zeus picks it up.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$SCRIPT_DIR/zeus.py"
TARGET="/usr/local/bin/zeus"

c_yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
c_gold()   { printf '\033[33m\033[1m%s\033[0m\n' "$*"; }
c_green()  { printf '\033[32m%s\033[0m\n' "$*"; }
c_warn()   { printf '\033[33m\033[1m%s\033[0m\n' "$*"; }
c_red()    { printf '\033[31m%s\033[0m\n' "$*"; }

c_gold "==> Zeus installer (v1.0) ⚡"
echo

# ── Detect rc files to update ─────────────────────────────────────
LOGIN_SHELL_NAME="$(basename "${SHELL:-/bin/bash}")"
PRIMARY_RC="$HOME/.bashrc"
case "$LOGIN_SHELL_NAME" in
    zsh)  PRIMARY_RC="$HOME/.zshrc" ;;
    bash) PRIMARY_RC="$HOME/.bashrc" ;;
    *)    PRIMARY_RC="$HOME/.profile" ;;
esac

RC_FILES=()
for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
    [[ -f "$rc" ]] && RC_FILES+=("$rc")
done
if [[ ! -f "$PRIMARY_RC" ]]; then
    touch "$PRIMARY_RC"
    RC_FILES+=("$PRIMARY_RC")
fi

c_green "[ok] login shell: $LOGIN_SHELL_NAME"
c_green "[ok] will update: ${RC_FILES[*]}"

# ── Python check ──────────────────────────────────────────────────
if ! command -v python3 >/dev/null 2>&1; then
    c_red "Python 3 not found. Install python3 (3.10+) and re-run."
    exit 1
fi
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3,10) else 0)')
if [[ "$PY_OK" != "1" ]]; then
    c_red "Python 3.10+ required, found $PY_VER"
    exit 1
fi
c_green "[ok] Python $PY_VER"

# ── Zeus script presence ──────────────────────────────────────────
if [[ ! -f "$SCRIPT" ]]; then
    c_red "zeus.py not found at $SCRIPT"
    exit 1
fi
chmod +x "$SCRIPT"
c_green "[ok] zeus.py present"

# ── Python dependencies ───────────────────────────────────────────
c_gold "==> Installing Python dependencies (groq, networkx)"
PIP_FLAGS=""
if pip3 install --help 2>&1 | grep -q -- "--break-system-packages"; then
    PIP_FLAGS="--break-system-packages"
fi
if [[ -f "$SCRIPT_DIR/requirements.txt" ]]; then
    REQ_SRC="-r $SCRIPT_DIR/requirements.txt"
else
    REQ_SRC="groq networkx"
fi
if ! pip3 install -q $PIP_FLAGS $REQ_SRC; then
    c_warn "[!] pip install failed — trying with --user"
    pip3 install -q --user $PIP_FLAGS $REQ_SRC
fi
c_green "[ok] Python dependencies installed"

# ── Optional OSINT CLI tools (helpful but not required) ────────────
c_gold "==> Optional OSINT CLI tools"
echo "    Zeus degrades gracefully when these aren't present, but"
echo "    installing them gives you full coverage:"
echo "      sherlock     — username pivoting across ~400 platforms"
echo "      maigret      — slower-but-smarter username search"
echo "      holehe       — email registered-services enumeration"
echo "      phoneinfoga  — phone OSINT (carrier / country / line type)"
echo "      subfinder    — passive subdomain enumeration"
echo "      amass        — passive subdomain enumeration"
echo "      exiftool     — image EXIF extraction"
echo "      jq           — JSON parsing (already in Kali)"
echo
echo "    Quick install (all in one go):"
echo "      pipx install sherlock-project maigret holehe"
echo "      go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest"
echo "      go install -v github.com/owasp-amass/amass/v4/...@master"
echo "      sudo apt install libimage-exiftool-perl jq -y"
echo
echo "    None of these are required to start Zeus.  Install whatever you have."
echo

# ── Symlink to /usr/local/bin (or fall back to alias) ─────────────
LINK_OK=0
if sudo -n true 2>/dev/null || [[ -w "/usr/local/bin" ]]; then
    if [[ -L "$TARGET" || -e "$TARGET" ]]; then
        sudo rm -f "$TARGET" 2>/dev/null || rm -f "$TARGET"
    fi
    if [[ -w "/usr/local/bin" ]]; then
        ln -s "$SCRIPT" "$TARGET"
    else
        sudo ln -s "$SCRIPT" "$TARGET"
    fi
    c_green "[ok] $TARGET → $SCRIPT"
    LINK_OK=1
else
    c_warn "==> sudo not available — adding 'zeus' alias to your rc files"
    for rc in "${RC_FILES[@]}"; do
        if ! grep -q "^alias zeus=" "$rc" 2>/dev/null; then
            echo "alias zeus='python3 $SCRIPT'" >> "$rc"
            c_green "[ok] alias added to $rc"
        else
            c_green "[ok] alias already present in $rc"
        fi
    done
fi

# ── GROQ_API_KEY (shared with Athena and Ares) ────────────────────
HAS_KEY_IN_RC=0
for rc in "${RC_FILES[@]}"; do
    if grep -q "GROQ_API_KEY" "$rc" 2>/dev/null; then
        HAS_KEY_IN_RC=1
        break
    fi
done

if [[ -z "${GROQ_API_KEY:-}" && "$HAS_KEY_IN_RC" == "0" ]]; then
    echo
    c_warn "==> No GROQ_API_KEY found in your environment or rc files"
    echo "    Get a free key at: https://console.groq.com (no credit card)"
    echo "    The same key works for Athena, Ares, and Zeus."
    read -r -p "    Paste your Groq API key (or press Enter to skip): " key
    if [[ -n "$key" ]]; then
        for rc in "${RC_FILES[@]}"; do
            echo "export GROQ_API_KEY=$key" >> "$rc"
            c_green "[ok] GROQ_API_KEY written to $rc"
        done
        c_warn "    Reload your shell:  source $PRIMARY_RC"
    else
        c_warn "[!] Skipped. Set GROQ_API_KEY before running zeus."
    fi
elif [[ "$HAS_KEY_IN_RC" == "1" ]]; then
    c_green "[ok] GROQ_API_KEY already configured — Zeus will use it."
fi

# ── Optional GITHUB_TOKEN (free PAT, raises GitHub API rate limits) ─
HAS_GH_IN_RC=0
for rc in "${RC_FILES[@]}"; do
    if grep -q "GITHUB_TOKEN" "$rc" 2>/dev/null; then
        HAS_GH_IN_RC=1
        break
    fi
done

if [[ -z "${GITHUB_TOKEN:-}" && "$HAS_GH_IN_RC" == "0" ]]; then
    echo
    c_warn "==> Optional: GITHUB_TOKEN (free PAT)"
    echo "    Raises GitHub API rate limit from 60/hr to 5000/hr."
    echo "    Get one at: https://github.com/settings/tokens"
    echo "      → Generate new token (classic)"
    echo "      → check 'public_repo' and 'read:user' scopes"
    echo "      → copy the token starting with 'ghp_'"
    echo "    Zeus runs fine without it, just slower on the GitHub branch."
    read -r -p "    Paste GitHub PAT (or press Enter to skip): " gh_key
    if [[ -n "$gh_key" ]]; then
        for rc in "${RC_FILES[@]}"; do
            echo "export GITHUB_TOKEN=$gh_key" >> "$rc"
            c_green "[ok] GITHUB_TOKEN written to $rc"
        done
    else
        c_warn "[!] Skipped — GitHub branch will be rate-limited."
    fi
fi

# ── Friendly callout if Athena and Ares are also installed ────────
PANTHEON_FOUND=0
if [[ -d "$HOME/.athena" || -d "$HOME/.ares" ]]; then
    PANTHEON_FOUND=1
    echo
    c_gold "==> Pantheon detected"
    [[ -d "$HOME/.athena" ]] && echo "    Athena (offense)    at $HOME/.athena"
    [[ -d "$HOME/.ares"   ]] && echo "    Ares (defense)      at $HOME/.ares"
    echo "    Zeus (intelligence) runs in /tmp/zeus_<pid>/  ⚡  RAM-only"
    echo
    echo "    Athena finds the path in.  Ares verifies you've closed it."
    echo "    Zeus aggregates everything legally findable about a subject."
fi

echo
c_gold "==> Install complete  ⚡"
if [[ "$LINK_OK" == "1" ]]; then
    echo "    Run:  zeus"
else
    echo "    Run:  source $PRIMARY_RC && zeus"
fi
echo
