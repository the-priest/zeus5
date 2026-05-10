#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║         ZEUS — Fast Legal OSINT Search Engine  v5.1              ║
║         Bare-metal Kali NetHunter · Operator: The Priest         ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║   v5.1 — full pipeline rewrite.                                  ║
║   Throws out v1-v4's lane gates, specialist routing, OTT tree,   ║
║   50-turn agent loop, and 11-agent fallback chain.               ║
║                                                                  ║
║   New flow:                                                      ║
║     intake → enumerate → verify → enrich → report                ║
║   No AI for routing.  AI only writes the final summary.          ║
║                                                                  ║
║   Designed to run in 60-120s, produce 5-20 verified hits         ║
║   and zero false positives.                                      ║
║                                                                  ║
║   RAM-only · no disk persistence · public-OSINT-only.            ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import sys
import re
import json
import time
import shutil
import signal
import subprocess
import datetime
import hashlib
import concurrent.futures
import urllib.request
import urllib.error
from typing import List, Dict, Tuple, Optional, Any, Set
from urllib.parse import urlparse, urljoin

VERSION = "5.1"

# ─── Groq client (only used for final summary writeup) ────────────
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

# Fallback chain in case one model is rate-limited
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "llama-3.1-8b-instant",
]

# ═════════════════════════════════════════════════════════════════════
# CONFIG
# ═════════════════════════════════════════════════════════════════════

# Total wall-clock budget
TOTAL_TIMEOUT_SEC = 240  # 4 min hard cap

# Per-stage timeouts
ENUM_TIMEOUT       = 90   # sherlock+maigret+holehe in parallel
PER_FETCH_TIMEOUT  = 7    # one HTTP fetch
VERIFY_PARALLEL    = 8    # threads for verifier
ENRICH_PARALLEL    = 4    # threads for enrich

# Working dir
WORKDIR = f"/tmp/zeus_{os.getpid()}"

# Output styling helpers
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[90m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
WHITE  = "\033[97m"
BLUE   = "\033[34m"
MAGENTA = "\033[35m"

# ═════════════════════════════════════════════════════════════════════
# PROFILE VERIFICATION (carried over from v5.0 with refinements)
# ═════════════════════════════════════════════════════════════════════

# Per-platform rules for confirming a real profile page.
PLATFORM_VERIFY_RULES: Dict[str, Dict[str, Any]] = {
    "github.com":       {"must_contain": ["{handle}"],
                         "soft_404": ["Page not found", "Not Found"],
                         "enrich": True},
    "gitlab.com":       {"must_contain": ["{handle}"],
                         "soft_404": ["Page Not Found", "404"],
                         "enrich": True},
    "bsky.app":         {"must_contain": ["{handle}"],
                         "soft_404": []},
    "www.reddit.com":   {"must_contain": ["u/{handle}", "{handle}"],
                         "soft_404": ["page not found",
                                      "Sorry, nobody on Reddit"]},
    "old.reddit.com":   {"must_contain": ["{handle}"],
                         "soft_404": ["page not found"]},
    "www.youtube.com":  {"must_contain": ["{handle}"],
                         "soft_404": ["404 Not Found",
                                      "This page isn't available"]},
    "medium.com":       {"must_contain": ["@{handle}"],
                         "soft_404": ["PAGE NOT FOUND",
                                      "out of nothing"]},
    "twitter.com":      {"must_contain": ["{handle}"],
                         "soft_404": ["This account doesn't exist"]},
    "x.com":            {"must_contain": ["{handle}"],
                         "soft_404": ["This account doesn't exist"]},
    "soundcloud.com":   {"must_contain": ["{handle}"],
                         "soft_404": ["We can't find that user"]},
    "www.instagram.com": {"must_contain": ["@{handle}"],
                         "soft_404": ["Sorry, this page",
                                      "Page Not Found"]},
    "www.tiktok.com":   {"must_contain": ["@{handle}"],
                         "soft_404": ["Couldn't find this account",
                                      "Page not available"]},
    "www.linkedin.com": {"must_contain": ["{handle}"],
                         "soft_404": ["Page not found"]},
    "www.facebook.com": {"must_contain": ["{handle}"],
                         "soft_404": ["page isn't available",
                                      "Content Not Found"]},
    "stackoverflow.com": {"must_contain": ["{handle}"],
                         "soft_404": ["Page Not Found"]},
    "lichess.org":      {"must_contain": ["{handle}"],
                         "soft_404": ["Page not found"]},
    "letterboxd.com":   {"must_contain": ["{handle}"],
                         "soft_404": ["Sorry, we can't find"]},
    "open.spotify.com": {"must_contain": ["{handle}"],
                         "soft_404": ["Page not found"]},
    "keybase.io":       {"must_contain": ["{handle}"],
                         "soft_404": ["doesn't exist"]},
    "www.tumblr.com":   {"must_contain": ["{handle}"],
                         "soft_404": ["There's nothing here"]},
}

# Platforms that lie — always return 200 for any handle.  Auto-noise.
SOFT_404_PLATFORMS: Set[str] = {
    "discord.com", "discords.com",
    "patched.sh", "interpals.net", "www.interpals.net",
    "shelf.im", "www.shelf.im", "phpru.org", "php.ru",
    "svidbook.ru", "www.svidbook.ru",
    "velomania.ru", "forum.velomania.ru",
    "igromania.ru", "forum.igromania.ru",
    "opennet.ru", "www.opennet.ru",
    "nationstates.net", "yandexmusic.ru", "music.yandex",
    "rarible.com", "cavalier.hudsonrock.com",
    "www.gaiaonline.com", "codesnippets.fandom.com",
    "www.wikidot.com", "wikidot.com",
    "robertsspaceindustries.com",
    "www.mercadolivre.com.br",
    "tetr.io", "ch.tetr.io",
    "www.1337x.to", "www.couchsurfing.com",
    # holehe author footer URL — never a real finding
    "github.com/megadose",
}

# Strong-value platforms — these get prioritized in enumeration AND
# any URL on them ALWAYS gets attempted regardless of order.
HIGH_VALUE_PLATFORMS = [
    "github.com", "gitlab.com", "linkedin.com",
    "twitter.com", "x.com",
    "instagram.com", "facebook.com", "tiktok.com",
    "youtube.com", "reddit.com",
    "bsky.app", "mastodon.social", "threads.net",
    "medium.com", "substack.com",
    "stackoverflow.com", "soundcloud.com", "spotify.com",
    "keybase.io",
]


def verify_profile_url(url: str, handle: str,
                       timeout: int = PER_FETCH_TIMEOUT
                       ) -> Tuple[str, str, str]:
    """Real-HTTP profile verifier.

    Returns (verdict, evidence, body_snippet) where verdict is:
        confirmed — high-confidence hit
        probable  — handle present somewhere reasonable
        noise     — soft-404 / handle absent / blocked
        error     — fetch failed
    body_snippet is the first 8kb of the page, kept for the enricher.
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ("error", "bad URL", "")

    if host in SOFT_404_PLATFORMS:
        return ("noise", f"soft-404 platform: {host}", "")
    if any(frag in url.lower() for frag in
           ("/api/", "/api-v", "discords.com/api")):
        return ("noise", "API endpoint, not a profile", "")
    if "/search" in url.lower() or re.search(r'[?&]q=', url):
        return ("noise", "search URL, not a profile", "")

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Linux; Android 10) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/121.0.0.0 Mobile Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = r.getcode()
            body_bytes = r.read(80000)  # cap at 80kb
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ("noise", "HTTP 404", "")
        if e.code in (403, 401):
            return ("noise", f"HTTP {e.code}", "")
        if e.code == 429:
            return ("error", "rate limited", "")
        return ("noise", f"HTTP {e.code}", "")
    except Exception as e:
        return ("error", f"fetch: {type(e).__name__}", "")

    if status >= 400:
        return ("noise", f"HTTP {status}", "")

    try:
        body = body_bytes.decode("utf-8", errors="replace")
    except Exception:
        body = str(body_bytes)
    body_lower = body.lower()
    handle_lower = handle.lower()

    rules = PLATFORM_VERIFY_RULES.get(host, {})

    # Generic soft-404 phrases
    GENERIC_404 = (
        "page not found", "404 not found", "user not found",
        "profile not found", "this account doesn't exist",
        "page isn't available", "couldn't find",
        "page does not exist", "no user found",
        "out of nothing", "we can't find", "nothing here",
        "this user has been deleted", "account suspended",
        "account terminated",
    )
    for phrase in list(rules.get("soft_404", [])) + list(GENERIC_404):
        if phrase.lower() in body_lower:
            return ("noise", f"soft-404 phrase: {phrase!r}", "")

    # Strong-positive: handle in <title>
    title_match = re.search(r'<title[^>]*>(.*?)</title>',
                            body, re.IGNORECASE | re.DOTALL)
    title_text = (title_match.group(1) if title_match else "").lower()
    if handle_lower in title_text and not any(
        w in title_text for w in
        ("not found", "404", "error", "unavailable")
    ):
        return ("confirmed",
                f"in <title>: {title_text[:60].strip()}",
                body[:8000])

    # Strong-positive: handle in og:url / og:title / twitter:title
    for meta in ("og:url", "og:title", "twitter:title", "og:profile"):
        m = re.search(
            r'<meta\s+[^>]*property=["\']' + re.escape(meta) +
            r'["\'][^>]*content=["\']([^"\']+)["\']',
            body, re.IGNORECASE)
        if m and handle_lower in m.group(1).lower():
            return ("confirmed",
                    f"in meta {meta}: {m.group(1)[:60]}",
                    body[:8000])

    # Per-platform must-contain
    for tmpl in rules.get("must_contain", []):
        needle = tmpl.replace("{handle}", handle_lower)
        if needle in body_lower:
            return ("confirmed",
                    f"required pattern: {needle!r}",
                    body[:8000])

    # Generic: handle present in body
    if handle_lower in body_lower:
        return ("probable", "handle in body",
                body[:8000])

    return ("noise", "handle absent from page", "")


# ═════════════════════════════════════════════════════════════════════
# ENRICHMENT — for each confirmed profile, extract bio/location/links
# ═════════════════════════════════════════════════════════════════════

def enrich_profile(url: str, body: str) -> Dict[str, Any]:
    """Pull whatever public info the page reveals — bio, location,
    linked URLs, full name, profile photo URL, joined date.
    """
    out: Dict[str, Any] = {"url": url}
    host = (urlparse(url).hostname or "").lower()

    # Meta tags (works for most modern sites)
    def _meta(name: str) -> Optional[str]:
        for attr in ("property", "name"):
            m = re.search(
                r'<meta\s+[^>]*' + attr + r'=["\']' +
                re.escape(name) + r'["\'][^>]*content=["\']([^"\']+)["\']',
                body, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return None

    desc = (_meta("og:description") or _meta("twitter:description")
            or _meta("description"))
    if desc and len(desc) > 5:
        out["bio"] = desc[:280]

    full_name = _meta("og:title") or _meta("twitter:title")
    if full_name:
        # Strip the trailing platform name e.g. "Joe Bloggs · GitHub"
        clean = re.sub(r'\s*[·•|]\s*[A-Z][a-zA-Z]+$', '', full_name)
        clean = re.sub(r'^[\(@]?\w+[\)]?\s*[-:]\s*', '', clean)
        if clean and len(clean) > 1:
            out["display_name"] = clean[:80]

    photo = _meta("og:image") or _meta("twitter:image")
    if photo and photo.startswith("http"):
        out["photo"] = photo[:200]

    # GitHub-specific: pull email, location, bio from the page itself
    if host == "github.com":
        m = re.search(
            r'<span[^>]*itemprop=["\']homeLocation["\'][^>]*>'
            r'[^<]*<span[^>]*>([^<]+)</span>',
            body, re.IGNORECASE)
        if m:
            out["location"] = m.group(1).strip()
        m = re.search(
            r'<a[^>]*itemprop=["\']url["\'][^>]*href=["\']([^"\']+)["\']',
            body, re.IGNORECASE)
        if m:
            out["linked_site"] = m.group(1)

    # Linked external URLs from <a href> (filter to social/personal)
    linked = set()
    for m in re.finditer(r'href=["\'](https?://[^"\']+)["\']', body):
        u = m.group(1)
        try:
            uh = urlparse(u).hostname or ""
            uh_low = uh.lower().lstrip("www.")
        except Exception:
            continue
        # Keep only external, non-self, non-CDN
        if uh_low == host.lstrip("www."):
            continue
        if any(uh_low.endswith(s) for s in (
            "twitter.com", "x.com", "instagram.com",
            "linkedin.com", "facebook.com", "youtube.com",
            "tiktok.com", "github.com", "gitlab.com",
            "mastodon.social", "bsky.app",
        )):
            linked.add(u.rstrip("/"))
    if linked:
        out["linked_profiles"] = list(linked)[:8]

    return out


# ═════════════════════════════════════════════════════════════════════
# HANDLE VARIANT GENERATION
# ═════════════════════════════════════════════════════════════════════

def generate_handles(name: str, aliases: List[str],
                     existing: Set[str]) -> List[str]:
    """Generate plausible handle variants from a real name + aliases.

    Returns a deduplicated list of candidates, ordered by likelihood.
    Caps at 6 total to keep enumeration time bounded.
    """
    out: List[str] = []
    seen: Set[str] = {h.lower() for h in existing if h}

    def _add(c: str):
        c = c.strip().lower()
        if 3 <= len(c) <= 30 and c not in seen:
            out.append(c)
            seen.add(c)

    # Aliases first — they're explicit choices by the user
    for a in aliases:
        if not a:
            continue
        _add(a)

    # Then name-derived
    if name:
        tokens = re.split(r'[\s_\-\.]+', name.lower().strip())
        tokens = [t for t in tokens if t and t.isalnum()]
        if len(tokens) == 1:
            _add(tokens[0])
        elif len(tokens) >= 2:
            first, last = tokens[0], tokens[-1]
            if first.isalpha() and last.isalpha():
                _add(first + last)        # lukakrajina
                _add(first + "." + last)  # luka.krajina
                _add(first[0] + last)     # lkrajina
                _add(first + last[0])     # lukak
                _add(last)                # krajina
    return out[:6]


# ═════════════════════════════════════════════════════════════════════
# ENUMERATION — sherlock / maigret / holehe (all parallel)
# ═════════════════════════════════════════════════════════════════════

# Tool credit junk to strip from all command output before parsing.
TOOL_CREDITS = (
    "@palenath", "megadose", "github.com/megadose",
    "1FHDM49QfZX6pJmhjLE5tB2K6CaTLMZpXZ",
    "sdushantha", "soxoj", "sundowndev",
    "tomnomnom", "projectdiscovery", "webbreacher",
    "For BTC Donations",
)

def _sanitize_tool_output(text: str) -> str:
    """Strip tool-author credit banners so the AI/parser never sees them."""
    if not text:
        return text
    out_lines = []
    for line in text.splitlines():
        skip = False
        for credit in TOOL_CREDITS:
            if credit.lower() in line.lower():
                skip = True
                break
        if skip:
            continue
        # Strip progress bars
        if re.match(r'^\s*\d+%\|', line):
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


def _run_cmd(cmd: List[str], timeout: int = 60) -> str:
    """Run a subprocess command, return stdout+stderr (sanitized)."""
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, errors="replace",
        )
        return _sanitize_tool_output(p.stdout + p.stderr)
    except subprocess.TimeoutExpired:
        return ""
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


def enum_sherlock(handle: str, timeout: int = 90) -> List[str]:
    """Run sherlock against a handle, return raw candidate URLs."""
    if not shutil.which("sherlock"):
        return []
    out = _run_cmd(
        ["sherlock", "--timeout", "10", "--no-color",
         "--print-found", handle],
        timeout=timeout,
    )
    urls = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("[+]"):
            continue
        # Drop maigret/sherlock stub lines
        after = line[3:].lstrip().lower()
        if any(after.startswith(p) for p in (
            "db auto-update", "using sites database",
            "logging level", "fetched", "downloaded",
            "starting search", "checking", "results:",
            "database loaded", "username", "fields:",
        )):
            continue
        m = re.search(r'(https?://\S+)', line)
        if m:
            urls.append(m.group(1).rstrip("/"))
    return urls


def enum_maigret(handle: str, timeout: int = 90) -> List[str]:
    """Run maigret against a handle, return raw candidate URLs."""
    if not shutil.which("maigret"):
        return []
    out = _run_cmd(
        ["maigret", "--timeout", "10", "--top-sites", "200",
         "--no-color", "--no-progressbar", handle],
        timeout=timeout,
    )
    urls = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("[+]"):
            continue
        after = line[3:].lstrip().lower()
        if any(after.startswith(p) for p in (
            "db auto-update", "using sites database",
            "logging level", "fetched", "downloaded",
        )):
            continue
        m = re.search(r'(https?://\S+)', line)
        if m:
            urls.append(m.group(1).rstrip("/"))
    return urls


def enum_holehe(email: str, timeout: int = 60) -> List[str]:
    """Run holehe against an email, return list of services where the
    email is registered (as plain service names — not URLs).
    """
    if not shutil.which("holehe"):
        return []
    out = _run_cmd(
        ["holehe", "--no-color", "--only-used", email],
        timeout=timeout,
    )
    services = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("[+]"):
            # holehe output: "[+] amazon.com"
            svc = line[3:].strip()
            # Skip the totals line
            if "checked in" in svc or "websites" in svc:
                continue
            if svc and "." in svc:
                services.append(svc)
    return services


def enum_github_user(handle: str, timeout: int = 15) -> Dict[str, Any]:
    """Fetch a GitHub user's public profile via API.  Returns the
    parsed JSON or {} if user doesn't exist or call failed.
    """
    url = f"https://api.github.com/users/{handle}"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Zeus-OSINT/5.1",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.getcode() != 200:
                return {}
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return {}
    return {
        "login": data.get("login"),
        "name": data.get("name"),
        "bio": data.get("bio"),
        "company": data.get("company"),
        "location": data.get("location"),
        "blog": data.get("blog"),
        "email": data.get("email"),
        "twitter_username": data.get("twitter_username"),
        "public_repos": data.get("public_repos"),
        "followers": data.get("followers"),
        "created_at": data.get("created_at"),
        "html_url": data.get("html_url"),
        "avatar_url": data.get("avatar_url"),
    }


def enum_gravatar(email: str, timeout: int = 10) -> Dict[str, Any]:
    """Look up a Gravatar profile for an email.  Returns {} if no
    public profile is associated."""
    h = hashlib.md5(email.lower().strip().encode()).hexdigest()
    url = f"https://www.gravatar.com/{h}.json"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Zeus-OSINT/5.1",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.getcode() != 200:
                return {}
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return {}
    entries = data.get("entry", [])
    if not entries:
        return {}
    e = entries[0]
    return {
        "display_name": e.get("displayName"),
        "name": (e.get("name") or {}).get("formatted"),
        "location": e.get("currentLocation"),
        "bio": e.get("aboutMe"),
        "photo": e.get("thumbnailUrl"),
        "profile_url": e.get("profileUrl"),
        "verified_accounts": [
            {"service": a.get("shortname"), "url": a.get("url")}
            for a in e.get("accounts", [])
        ],
    }


# ═════════════════════════════════════════════════════════════════════
# UI helpers
# ═════════════════════════════════════════════════════════════════════

def banner():
    print(f"""
{YELLOW}╔══════════════════════════════════════════════════════════╗
║   ███████╗███████╗██╗   ██╗███████╗                          ║
║   ╚══███╔╝██╔════╝██║   ██║██╔════╝                          ║
║     ███╔╝ █████╗  ██║   ██║███████╗     ⚡                  ║
║    ███╔╝  ██╔══╝  ██║   ██║╚════██║                          ║
║   ███████╗███████╗╚██████╔╝███████║                          ║
║   ╚══════╝╚══════╝ ╚═════╝ ╚══════╝                          ║
║                                                          ║
║   {BOLD}Legal OSINT Search Engine  ·  v{VERSION}{RESET}{YELLOW}                ║
║   {DIM}name → handles → emails → confirmed profiles{YELLOW}        ║
║                                                          ║
║   {DIM}Public sources only.  RAM-only.  Wiped on exit.{YELLOW}    ║
╚══════════════════════════════════════════════════════════╝{RESET}
""")


def say(msg: str, color: str = WHITE):
    print(f"{color}   {msg}{RESET}")


def step(stage: str, msg: str):
    print(f"{CYAN}[{stage}]{RESET} {msg}")


def warn(msg: str):
    print(f"{YELLOW}   ⚠ {msg}{RESET}")


def err(msg: str):
    print(f"{RED}   ✕ {msg}{RESET}")


def ok(msg: str):
    print(f"{GREEN}   ✓ {msg}{RESET}")


def progress(stage: str, done: int, total: int, label: str = ""):
    """Single-line progress indicator."""
    pct = int(100 * done / total) if total else 0
    bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
    line = f"\r{CYAN}[{stage}]{RESET} {bar} {pct:3}% {DIM}{label[:40]}{RESET}"
    sys.stdout.write(line)
    sys.stdout.flush()
    if done >= total:
        print()


# ═════════════════════════════════════════════════════════════════════
# INTAKE — single screen, 4 fields, done in 10 seconds
# ═════════════════════════════════════════════════════════════════════

EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$')


def intake() -> Optional[Dict[str, Any]]:
    """One-screen intake.  Returns a dict of identifiers or None on abort."""
    print()
    print(f"{BOLD}{WHITE}   Subject identifiers{RESET}  "
          f"{DIM}— at least name OR handle required{RESET}")
    print(f"{DIM}   Press Enter to skip a field.{RESET}")
    print()
    try:
        name    = input(f"   {WHITE}Name (real or alias):{RESET}     ").strip()
        handle  = input(f"   {WHITE}Username / handle:{RESET}        ").strip()
        email   = input(f"   {WHITE}Email address:{RESET}            ").strip()
        country = input(f"   {WHITE}Country (optional):{RESET}       ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None

    if not name and not handle and not email:
        print()
        err("Need at least a name, handle, or email — nothing to search.")
        return None

    # Validate email
    if email and not EMAIL_RE.match(email):
        warn(f"  email '{email}' looks malformed — ignoring")
        email = ""

    return {
        "name": name,
        "handle": handle,
        "email": email,
        "country": country,
    }


# ═════════════════════════════════════════════════════════════════════
# PIPELINE
# ═════════════════════════════════════════════════════════════════════

class SearchResult:
    """A single confirmed profile finding."""
    __slots__ = ("url", "host", "handle_used", "evidence",
                 "verified", "bio", "display_name", "location",
                 "photo", "linked_profiles", "linked_site", "extra")

    def __init__(self, url: str, host: str, handle_used: str,
                 evidence: str, verified: bool):
        self.url = url
        self.host = host
        self.handle_used = handle_used
        self.evidence = evidence
        self.verified = verified
        self.bio: Optional[str] = None
        self.display_name: Optional[str] = None
        self.location: Optional[str] = None
        self.photo: Optional[str] = None
        self.linked_profiles: List[str] = []
        self.linked_site: Optional[str] = None
        self.extra: Dict[str, Any] = {}


def _normalize_url(u: str) -> str:
    """For dedup: lowercase, strip www., strip trailing slash,
    collapse https/http, collapse _ → -."""
    u = u.lower().rstrip("/")
    u = re.sub(r'^https?://', '', u)
    if u.startswith("www."):
        u = u[4:]
    if "/" in u:
        host, _, path = u.partition("/")
        path = path.replace("_", "-")
        u = host + "/" + path
    return u


def run_search(intake_data: Dict[str, Any]) -> Dict[str, Any]:
    """Top-level pipeline.  Returns a result-bundle dict for reporting."""
    t0 = time.time()
    name    = intake_data.get("name") or ""
    handle  = intake_data.get("handle") or ""
    email   = intake_data.get("email") or ""
    country = intake_data.get("country") or ""

    print()
    step("INTAKE", f"name={name!r}  handle={handle!r}  "
                   f"email={email!r}  country={country!r}")

    # ── Stage 1: HANDLE EXPANSION ────────────────────────────────
    handles_to_search: List[str] = []
    if handle:
        handles_to_search.append(handle)
    # Generate variants from name (cap at 4 total handles incl. user-given)
    auto = generate_handles(name, [], existing=set(handles_to_search))
    handles_to_search.extend(auto)
    handles_to_search = handles_to_search[:4]

    if handles_to_search:
        step("HANDLES",
             f"searching {len(handles_to_search)} handle(s): "
             f"{', '.join(handles_to_search)}")
    else:
        step("HANDLES", f"{DIM}no handles to search{RESET}")

    # ── Stage 2: ENUMERATION (parallel) ──────────────────────────
    # Fan out sherlock+maigret per handle and holehe per email.
    candidates_by_handle: Dict[str, Set[str]] = {h: set() for h in handles_to_search}
    holehe_hits: List[str] = []
    gravatar_data: Dict[str, Any] = {}

    print()
    step("ENUM", "fanning out sherlock + maigret + holehe in parallel...")
    enum_tasks: List[Tuple[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for h in handles_to_search:
            enum_tasks.append(("sherlock:" + h,
                                ex.submit(enum_sherlock, h, ENUM_TIMEOUT)))
            enum_tasks.append(("maigret:" + h,
                                ex.submit(enum_maigret, h, ENUM_TIMEOUT)))
        if email:
            enum_tasks.append(("holehe:" + email,
                                ex.submit(enum_holehe, email, 60)))
            enum_tasks.append(("gravatar:" + email,
                                ex.submit(enum_gravatar, email, 10)))

        # Stream progress
        done = 0
        total = len(enum_tasks)
        for label, fut in [(l, f) for (l, f) in enum_tasks]:
            try:
                r = fut.result(timeout=ENUM_TIMEOUT + 5)
            except Exception:
                r = None
            done += 1
            progress("ENUM", done, total, label.split(":")[0])
            if r is None:
                continue
            if label.startswith("sherlock:") or label.startswith("maigret:"):
                h = label.split(":", 1)[1]
                for u in r:
                    candidates_by_handle[h].add(u)
            elif label.startswith("holehe:"):
                holehe_hits = r
            elif label.startswith("gravatar:"):
                gravatar_data = r

    total_candidates = sum(len(s) for s in candidates_by_handle.values())
    ok(f"enumeration: {total_candidates} candidate URL(s), "
       f"{len(holehe_hits)} email-registered service(s)"
       + (", gravatar present" if gravatar_data else ""))

    # ── Stage 3: VERIFICATION (parallel HTTP fetch) ──────────────
    print()
    step("VERIFY", f"fetching each candidate URL to confirm handle is present...")

    # Flatten candidates with the handle they came from
    flat_candidates: List[Tuple[str, str]] = []
    seen_norm: Set[str] = set()
    for h, urls in candidates_by_handle.items():
        for u in urls:
            n = _normalize_url(u)
            if n in seen_norm:
                continue
            seen_norm.add(n)
            flat_candidates.append((u, h))

    # Priority sort: HIGH_VALUE first, then known platforms, then unknown
    def _prio(item):
        u, _ = item
        host = (urlparse(u).hostname or "").lower().lstrip("www.")
        if host in SOFT_404_PLATFORMS:
            return 99
        if any(host.endswith(p) for p in HIGH_VALUE_PLATFORMS):
            return 0
        if host in PLATFORM_VERIFY_RULES:
            return 5
        return 50

    flat_candidates.sort(key=_prio)
    # Cap to keep verification time bounded
    flat_candidates = flat_candidates[:80]

    results: List[SearchResult] = []
    body_by_url: Dict[str, str] = {}
    verdict_counts = {"confirmed": 0, "probable": 0, "noise": 0, "error": 0}

    with concurrent.futures.ThreadPoolExecutor(max_workers=VERIFY_PARALLEL) as ex:
        future_to_item = {
            ex.submit(verify_profile_url, u, h, PER_FETCH_TIMEOUT):
                (u, h) for (u, h) in flat_candidates
        }
        done = 0
        total = len(future_to_item)
        for fut in concurrent.futures.as_completed(future_to_item, timeout=120):
            u, h = future_to_item[fut]
            try:
                verdict, evidence, body = fut.result()
            except Exception:
                verdict, evidence, body = ("error", "exception", "")
            done += 1
            try:
                host = urlparse(u).hostname or ""
            except Exception:
                host = ""
            progress("VERIFY", done, total, host[:30])
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
            if verdict in ("confirmed", "probable"):
                results.append(SearchResult(
                    url=u, host=host.lower().lstrip("www."),
                    handle_used=h, evidence=evidence,
                    verified=(verdict == "confirmed"),
                ))
                if body and verdict == "confirmed":
                    body_by_url[u] = body

    ok(f"verification: {verdict_counts['confirmed']} confirmed, "
       f"{verdict_counts['probable']} probable, "
       f"{verdict_counts['noise']} noise, "
       f"{verdict_counts['error']} errors")

    # ── Stage 4: ENRICHMENT (parallel, only confirmed) ────────────
    confirmed_results = [r for r in results if r.verified]

    # Special case: github.com — use the API for richer info
    github_handles = set()
    for r in confirmed_results:
        if r.host == "github.com":
            m = re.match(r'https?://[^/]+/([^/?#]+)', r.url)
            if m:
                github_handles.add(m.group(1))

    github_profiles: Dict[str, Dict[str, Any]] = {}
    if confirmed_results or github_handles:
        print()
        step("ENRICH", f"pulling bio / location / linked profiles from "
                       f"{len(confirmed_results)} confirmed page(s)...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=ENRICH_PARALLEL) as ex:
            futures = []
            for r in confirmed_results:
                body = body_by_url.get(r.url, "")
                if body:
                    futures.append((r, ex.submit(enrich_profile, r.url, body)))
            for gh in github_handles:
                futures.append(("github_user:" + gh,
                                 ex.submit(enum_github_user, gh, 15)))

            done = 0
            total = len(futures)
            for tag, fut in futures:
                try:
                    info = fut.result(timeout=20)
                except Exception:
                    info = {}
                done += 1
                progress("ENRICH", done, total, "")
                if isinstance(tag, SearchResult):
                    r = tag
                    r.bio = info.get("bio") or r.bio
                    r.display_name = info.get("display_name") or r.display_name
                    r.location = info.get("location") or r.location
                    r.photo = info.get("photo") or r.photo
                    r.linked_profiles = info.get("linked_profiles", []) or r.linked_profiles
                    r.linked_site = info.get("linked_site") or r.linked_site
                elif isinstance(tag, str) and tag.startswith("github_user:"):
                    gh = tag.split(":", 1)[1]
                    if info:
                        github_profiles[gh] = info

    elapsed = time.time() - t0

    return {
        "intake": intake_data,
        "handles_searched": handles_to_search,
        "results": results,
        "github_profiles": github_profiles,
        "holehe_hits": holehe_hits,
        "gravatar": gravatar_data,
        "verdict_counts": verdict_counts,
        "elapsed_sec": elapsed,
    }


# ═════════════════════════════════════════════════════════════════════
# AI SUMMARY  (optional — only if Groq is available)
# ═════════════════════════════════════════════════════════════════════

def write_ai_summary(bundle: Dict[str, Any]) -> Optional[str]:
    """Have Groq write a short paragraph synthesizing what we found.
    Returns None if Groq is unavailable or fails."""
    if not GROQ_AVAILABLE:
        return None
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return None

    # Build a compact factual brief for the model
    intake_data = bundle["intake"]
    confirmed = [r for r in bundle["results"] if r.verified]
    if not confirmed and not bundle.get("github_profiles") and not bundle.get("holehe_hits"):
        return None

    lines = ["FACTUAL INPUT (everything below has been HTTP-verified):"]
    lines.append(f"Subject identifiers: {intake_data}")
    if bundle["holehe_hits"]:
        lines.append(f"Email registered on: {', '.join(bundle['holehe_hits'])}")
    for gh, prof in bundle.get("github_profiles", {}).items():
        lines.append(f"GitHub user '{gh}': {json.dumps(prof, default=str)}")
    if bundle["gravatar"]:
        lines.append(f"Gravatar profile: {json.dumps(bundle['gravatar'], default=str)}")
    for r in confirmed[:20]:
        info = {"url": r.url}
        for f in ("display_name", "bio", "location", "linked_site"):
            v = getattr(r, f)
            if v: info[f] = v
        lines.append(f"Confirmed profile: {json.dumps(info)}")

    brief = "\n".join(lines)
    prompt = (
        "You are Zeus, a legal OSINT analyst.  Below is FACTUAL verified "
        "intel about a subject.  Write a single short paragraph (4-6 "
        "sentences) summarizing what we know.  Stick STRICTLY to the "
        "facts shown.  DO NOT invent details.  DO NOT speculate about "
        "names not present.  If multiple platforms show the same name, "
        "note it.  If the GitHub account is brand-new (created < 60 "
        "days ago), say so as a caveat.  Plain prose, no headings.\n\n"
        + brief
    )
    try:
        client = Groq(api_key=api_key)
        for model in GROQ_MODELS:
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=400,
                    temperature=0.3,
                )
                text = resp.choices[0].message.content
                if text and text.strip():
                    return text.strip()
            except Exception:
                continue
    except Exception:
        return None
    return None


# ═════════════════════════════════════════════════════════════════════
# REPORT
# ═════════════════════════════════════════════════════════════════════

def render_report(bundle: Dict[str, Any]) -> None:
    intake_data = bundle["intake"]
    results = bundle["results"]
    confirmed = [r for r in results if r.verified]
    leads = [r for r in results if not r.verified]
    gh_profiles = bundle.get("github_profiles", {})
    gravatar = bundle.get("gravatar", {})
    holehe = bundle.get("holehe_hits", [])
    elapsed = bundle.get("elapsed_sec", 0)

    W = 70
    bar = "═" * W
    thin = "─" * W

    print()
    print(f"{YELLOW}╔{bar}╗{RESET}")
    title = f"  ZEUS v{VERSION}  ·  OSINT SEARCH REPORT"
    print(f"{YELLOW}║{RESET}{BOLD}{WHITE}{title}{' ' * (W - len(title))}{RESET}{YELLOW}║{RESET}")
    print(f"{YELLOW}╚{bar}╝{RESET}")
    print()

    # Verdict pill
    if confirmed:
        pill = f"{GREEN}\033[42m\033[97m{BOLD}  {len(confirmed)} CONFIRMED PROFILES  {RESET}"
    elif leads or holehe or gravatar:
        pill = f"{YELLOW}\033[43m\033[30m{BOLD}  LEADS ONLY  {RESET}"
    else:
        pill = f"{RED}\033[41m\033[97m{BOLD}  NO FINDINGS  {RESET}"
    print(f"  {pill}")
    print()

    # Subject card
    seed = (intake_data.get("name") or intake_data.get("handle")
            or intake_data.get("email") or "(unnamed)")
    print(f"  {WHITE}Subject:{RESET}    {CYAN}{seed}{RESET}")
    if intake_data.get("country"):
        print(f"  {WHITE}Country:{RESET}    {intake_data['country']}")
    print(f"  {WHITE}Searched:{RESET}   "
          f"{', '.join(bundle.get('handles_searched', [])) or '(no handles)'}")
    print(f"  {WHITE}Time:{RESET}       {int(elapsed)}s")
    print()

    # === CONFIRMED PROFILES (the main act) ===
    if confirmed:
        print(f"{GREEN}{BOLD}  ── CONFIRMED PROFILES ── verified by HTTP fetch{RESET}")
        print()
        # Sort: high-value platforms first
        def _sort_key(r: SearchResult):
            for i, p in enumerate(HIGH_VALUE_PLATFORMS):
                if r.host.endswith(p):
                    return (i, r.host)
            return (99, r.host)
        confirmed.sort(key=_sort_key)

        for r in confirmed:
            # URL line
            print(f"    {GREEN}✓{RESET}  {WHITE}{r.url}{RESET}")
            # Display name
            if r.display_name and r.display_name.lower() not in r.url.lower():
                print(f"       {DIM}name:{RESET}     {r.display_name}")
            # Location
            if r.location:
                print(f"       {DIM}location:{RESET} {r.location}")
            # Bio
            if r.bio:
                bio_clean = re.sub(r'\s+', ' ', r.bio).strip()
                if len(bio_clean) > 120:
                    bio_clean = bio_clean[:117] + "..."
                print(f"       {DIM}bio:{RESET}      {bio_clean}")
            # Linked external profiles
            if r.linked_profiles:
                shown = r.linked_profiles[:4]
                print(f"       {DIM}links:{RESET}    {', '.join(shown)}")
            if r.linked_site:
                print(f"       {DIM}website:{RESET}  {r.linked_site}")
            print()

    # === GITHUB DETAIL CARDS (richer than what the page enrich gets) ===
    if gh_profiles:
        print(f"{CYAN}{BOLD}  ── GITHUB DEEP-DIVE ──{RESET}")
        print()
        for gh, prof in gh_profiles.items():
            print(f"    {WHITE}{prof.get('html_url', '?')}{RESET}")
            if prof.get("name"):
                print(f"       {DIM}name:{RESET}     {prof['name']}")
            if prof.get("bio"):
                print(f"       {DIM}bio:{RESET}      {prof['bio']}")
            if prof.get("location"):
                print(f"       {DIM}location:{RESET} {prof['location']}")
            if prof.get("company"):
                print(f"       {DIM}company:{RESET}  {prof['company']}")
            if prof.get("blog"):
                print(f"       {DIM}blog:{RESET}     {prof['blog']}")
            if prof.get("email"):
                print(f"       {DIM}email:{RESET}    {prof['email']}")
            if prof.get("twitter_username"):
                print(f"       {DIM}twitter:{RESET}  @{prof['twitter_username']}")
            # Freshness flag
            if prof.get("created_at"):
                try:
                    created = datetime.datetime.fromisoformat(
                        prof["created_at"].replace("Z", "+00:00"))
                    age_days = (datetime.datetime.now(datetime.timezone.utc) - created).days
                    if age_days < 60:
                        print(f"       {YELLOW}⚠ freshness:{RESET} "
                              f"account is {age_days} days old "
                              f"(possible squat / impersonation)")
                    else:
                        print(f"       {DIM}joined:{RESET}   "
                              f"{created.date().isoformat()} "
                              f"({age_days} days ago)")
                except Exception:
                    pass
            if prof.get("public_repos"):
                print(f"       {DIM}repos:{RESET}    {prof['public_repos']}, "
                      f"followers: {prof.get('followers', 0)}")
            print()

    # === GRAVATAR ===
    if gravatar:
        print(f"{CYAN}{BOLD}  ── GRAVATAR (linked to email) ──{RESET}")
        print()
        if gravatar.get("display_name"):
            print(f"    {DIM}name:{RESET}     {gravatar['display_name']}")
        if gravatar.get("bio"):
            print(f"    {DIM}bio:{RESET}      {gravatar['bio']}")
        if gravatar.get("location"):
            print(f"    {DIM}location:{RESET} {gravatar['location']}")
        if gravatar.get("profile_url"):
            print(f"    {DIM}profile:{RESET}  {gravatar['profile_url']}")
        for acc in gravatar.get("verified_accounts", [])[:8]:
            print(f"    {DIM}linked:{RESET}   {acc.get('service'):12s}  "
                  f"{acc.get('url')}")
        print()

    # === HOLEHE (email registrations) ===
    if holehe:
        print(f"{CYAN}{BOLD}  ── EMAIL REGISTERED ON ──{RESET}")
        print(f"    {DIM}holehe found this email registered on these "
              f"services{RESET}")
        print()
        for h in holehe:
            print(f"    {GREEN}✓{RESET}  {h}")
        print()

    # === LEADS (unverified) ===
    if leads:
        print(f"{YELLOW}{BOLD}  ── UNVERIFIED LEADS ── handle present but "
              f"couldn't fully confirm{RESET}")
        print()
        for r in leads[:12]:
            print(f"    {YELLOW}·{RESET}  {DIM}{r.url}{RESET}")
        if len(leads) > 12:
            print(f"       {DIM}... and {len(leads)-12} more leads{RESET}")
        print()

    # === AI SUMMARY (optional) ===
    if bundle.get("ai_summary"):
        print(f"{MAGENTA}{BOLD}  ── AI SYNTHESIS ──{RESET}")
        print()
        for line in bundle["ai_summary"].splitlines():
            print(f"    {line}")
        print()

    # === FOOTER ===
    print(f"{YELLOW}  {thin}{RESET}")
    print(f"{YELLOW}  All findings are PUBLIC OSINT only.{RESET}")
    print(f"{YELLOW}  RAM-only — wiped on exit.  Copy what you need NOW.{RESET}")
    print(f"{YELLOW}  {thin}{RESET}")
    print()
    print(f"  {DIM}(Generated by Zeus v{VERSION} at "
          f"{datetime.datetime.now().isoformat(timespec='seconds')}){RESET}")
    print()


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def main():
    # Ensure workdir
    os.makedirs(WORKDIR, exist_ok=True)

    # Banner
    banner()

    # Tool availability check
    missing = [t for t in ("sherlock", "maigret", "holehe")
               if not shutil.which(t)]
    if missing:
        warn(f"missing tools: {', '.join(missing)} — will skip those steps")
        warn("install: pipx install sherlock-project maigret holehe")

    # Intake
    intake_data = intake()
    if intake_data is None:
        say("intake cancelled — nothing to search.", DIM)
        sys.exit(0)

    # Hard wall-clock cap
    def _timeout_handler(signum, frame):
        print()
        warn(f"hit hard timeout of {TOTAL_TIMEOUT_SEC}s — wrapping up")
        # Allow report generation to proceed; raising would skip it
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(TOTAL_TIMEOUT_SEC)

    # Run pipeline
    try:
        bundle = run_search(intake_data)
    except KeyboardInterrupt:
        print()
        warn("interrupted — generating partial report")
        bundle = {
            "intake": intake_data,
            "handles_searched": [],
            "results": [],
            "github_profiles": {},
            "holehe_hits": [],
            "gravatar": {},
            "verdict_counts": {},
            "elapsed_sec": 0,
        }
    finally:
        signal.alarm(0)

    # AI summary (best-effort)
    bundle["ai_summary"] = write_ai_summary(bundle)

    # Render
    render_report(bundle)

    # Cleanup workdir
    try:
        shutil.rmtree(WORKDIR, ignore_errors=True)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        sys.exit(130)
