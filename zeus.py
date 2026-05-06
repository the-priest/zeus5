#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║              ZEUS — AI Legal OSINT Aggregator v1.1               ║
║         Bare-metal Kali NetHunter  ·  Operator: The Priest       ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║   ZEUS is the third pillar of the Greek pantheon stack.          ║
║   ATHENA finds the path in (offense).                            ║
║   ARES verifies you've closed it (defense).                      ║
║   ZEUS aggregates everything legally findable about a subject    ║
║   from public sources only — read-only OSINT, no auth bypass,    ║
║   no stolen credentials, no system mutation.                     ║
║                                                                  ║
║   v1.1 — bug-fix pass on first real run                          ║
║   • Stripped IOC fanout (was leaking Ares defensive log sweeps). ║
║   • Strategist now strictly delegates via [HANDOFF];             ║
║     specialists actually run their tools.                        ║
║   • Email/domain regex now require a real TLD —                  ║
║     no more journal-filename / .log false positives.             ║
║   • Report cleanup pass rewritten as Zeus OSINT prose            ║
║     (subject overview, surfaced identifiers, coverage gaps),     ║
║     not Ares ATT&CK / containment / EDR.                         ║
║   • Person-intake trimmed to things you'd actually know          ║
║     (name, aliases, emails, phones, handles, URLs, region,       ║
║     images, notes).  No more PGP fingerprints / SSH key          ║
║     fingerprint / year-of-birth / employer / industry.           ║
║                                                                  ║
║   FRONT-LOADED INTAKE → AUTONOMOUS RUN → TERMINAL REPORT         ║
║   • At session start: lane gate + subject-type + lean            ║
║     identifier intake (only what you'd actually know).           ║
║   • Then: AUTO_MODE on — no y/n/q gates, no per-step prompts.    ║
║     Zeus runs the agent loop until done, capped at 50 turns or   ║
║     15 minutes wall-clock by default.                            ║
║   • Final: report printed to terminal.  No disk writes.          ║
║   • RAM-only: /tmp/zeus_<pid>/ wiped on exit.                    ║
║                                                                  ║
║   10 OSINT SPECIALISTS                                           ║
║   strategist (♛) · intake (🪪) · socialite (👤) · postman (📮) ·   ║
║   caller (📞) · registrar (🌐) · cartographer (🗺) ·              ║
║   archivist (📚) · dorker (🔎) · ledger (💰) · reporter (📋)       ║
║                                                                  ║
║   12 WORKFLOWS                                                   ║
║   Self-OSINT Footprint · Username Pivot · Email Exposure ·       ║
║   Phone Triage · Domain Due Diligence · Threat-Actor Track ·     ║
║   Bug-Bounty Recon · Crypto Address Trace · Image Metadata ·     ║
║   Wayback History · Company Due Diligence · Document Leakage    ║
║                                                                  ║
║   46 STRUCTURED OSINT TOOL BUILDERS                              ║
║   sherlock · maigret · socialscan · whatsmyname · holehe ·       ║
║   gravatar · github_user_api · github_keys · github_dork ·       ║
║   reddit_user_info · mastodon_lookup · bluesky_resolve ·         ║
║   phoneinfoga · whois · dig · subfinder · amass · crt.sh ·       ║
║   waybackurls · gau · exiftool · btc/eth_address_balance · ...   ║
║                                                                  ║
║   HARD REFUSALS                                                  ║
║   • Brute force / credential guessing (hydra, hashcat, etc.)     ║
║   • Stolen credential dumps (DeHashed credential queries)        ║
║   • Real-time location / home-address resolution                 ║
║   • Stalkerware aggregators (Spokeo, BeenVerified, etc.)         ║
║   • Voter rolls, doxbin, SS7, IMSI catchers                      ║
║   • Domestic-abuse-adjacent intent (ex-partner tracking)         ║
║   • Authenticated scrapers (instaloader, instagram-scraper)      ║
║   • Any system mutation (sudo, systemctl, iptables, etc.)        ║
║                                                                  ║
║   ARCHITECTURE (carried over from Ares skeleton)                 ║
║   • Tool dispatch with synonym-aware kwargs                      ║
║   • Smart context manager + [NEED] re-fetches                    ║
║   • Per-command timeouts                                         ║
║   • Loop-breaker: forced agent rotation on repeats               ║
║   • Groq provider chain (same key as Athena/Ares)                ║
║   • Optional GITHUB_TOKEN env var (free PAT, raises rate cap)    ║
║                                                                  ║
║   "Three names, three jobs.  One Priest."                        ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import sys
import re
import json
import time
import getpass
import signal
import inspect
import datetime
import subprocess
import ipaddress
import shutil
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Tuple, Set

try:
    from groq import Groq
except ImportError:
    print("FATAL: groq package not installed. Run: pip install groq")
    sys.exit(1)

try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False
    print("WARN: networkx not installed — pivot graph disabled. "
          "Run: pip install networkx --break-system-packages")

try:
    import readline  # noqa: F401  (enables arrow keys in input())
except ImportError:
    pass


# ═════════════════════════════════════════════════════════════════════
# VERSION & PROVIDER CHAIN  (Groq only, biggest→smallest)
# ═════════════════════════════════════════════════════════════════════

VERSION = "1.1"

# Strict size descending. Compound models last because they have their
# own internal multi-step behaviour that fights our DTT control flow.
PROVIDER_CHAIN = [
    ("openai/gpt-oss-120b",                            "GPT-OSS 120B"),
    ("llama-3.3-70b-versatile",                        "LLaMA 3.3 70B"),
    ("qwen/qwen3-32b",                                 "Qwen3 32B"),
    ("openai/gpt-oss-20b",                             "GPT-OSS 20B"),
    ("meta-llama/llama-4-scout-17b-16e-instruct",      "LLaMA 4 Scout 17B"),
    ("llama-3.1-8b-instant",                           "LLaMA 3.1 8B"),
    ("allam-2-7b",                                     "Allam 2 7B"),
    ("groq/compound",                                  "Groq Compound"),
    ("groq/compound-mini",                             "Compound Mini"),
]


# ═════════════════════════════════════════════════════════════════════
# PATHS, LIMITS, MARKERS
#
# Zeus is RAM-ONLY by design.  No persistent state directory, no disk
# logs, no saved reports.  Working files use a /tmp/zeus_<pid>/ path
# that gets rm -rf'd on shutdown.  Operator copies the final report
# from the terminal before quitting.
# ═════════════════════════════════════════════════════════════════════

# Volatile working dir — wiped on session end
INSTALL_DIR = f"/tmp/zeus_{os.getpid()}"
LOG_DIR     = os.path.join(INSTALL_DIR, "logs")
SCOPE_FILE  = os.path.join(INSTALL_DIR, "scope.json")
BOOT_LOCK   = f"/tmp/zeus_{os.getpid()}.lock"

# Smart context: keep more in memory, send less by default
MAX_HISTORY_MESSAGES   = 32   # how many turns kept in RAM
DEFAULT_HISTORY_SLICE  = 4    # how many sent to API by default
EXPANDED_HISTORY_SLICE = 10   # when stuck/yellow/red conf
MAX_OUTPUT_CHARS       = 5000
MAX_TOKENS_DEFAULT     = 2048
WORKFLOW_DONE          = "WORKFLOW_COMPLETE"

# How many [NEED] re-fetches allowed per turn (prevents runaway loops)
MAX_NEED_FETCHES = 2

# Stuck thresholds
STUCK_THRESHOLD      = 3   # repeats before pivot
NODE_ATTEMPT_LIMIT   = 4   # attempts on a single OTT node before mark dead-end

# ── Autonomous-mode caps ────────────────────────────────────────────
# Zeus runs the whole investigation without y/n/q gates.  These caps
# stop it running forever or hammering rate-limited APIs.
AUTO_MODE                = True       # global autonomous flag
MAX_AUTO_TURNS           = 50         # hard ceiling per session
MAX_WALL_CLOCK_SECONDS   = 15 * 60    # 15 min default cap
ON_FAILURE_STRATEGY      = "skip"     # skip | retry-once | abort
PROGRESS_REFRESH_SECONDS = 2          # how often the progress line refreshes


# ═════════════════════════════════════════════════════════════════════
# v7.2 — TIMEOUTS, SUDO MARKERS, BOOT LOCK TTL
# ═════════════════════════════════════════════════════════════════════

# Per-command timeout policy.  The bash subprocess is killed if it runs
# longer than the matched ceiling.  Pattern is regex-against-cmd; first
# match wins.  Default ceiling at the bottom.
COMMAND_TIMEOUTS = [
    # Forensics — heavy, slow tools
    (r'\bvolatility(3)?\b',                     1800),    # memory analysis
    (r'\b(yara|yara-scan|yara3)\b',             1800),    # large-scale yara scans
    (r'\b(clamscan|clamdscan)\b',               1800),    # full AV scans
    (r'\b(rkhunter|chkrootkit)\b',               900),
    (r'\blynis\s+audit',                         600),
    (r'\baide(\.wrapper)?\s+(--init|--check)',   900),    # file integrity baselining
    (r'\bdebsums\b',                             600),
    (r'\bbinwalk\s+-e?',                         300),
    (r'\bforemost\b',                            900),    # file carving
    (r'\bphoton\b|\b(autopsy|sleuthkit)\b',      900),
    # Network capture and analysis
    (r'\btcpdump\b.*-c\s+\d{4,}',                300),    # bounded capture
    (r'\btcpdump\b',                             120),
    (r'\b(tshark|wireshark-cli)\b',              300),
    (r'\bzeek\b|\bbro\b',                        600),
    (r'\bsuricata\b.*-r\s',                      900),    # offline pcap replay
    (r'\bnft\s+list\b|\biptables\s+-[LSv]',       30),
    # Log analysis
    (r'\bjournalctl\b.*--since\s',               120),
    (r'\bjournalctl\b',                           60),
    (r'\bgrep\s+-r\b.*\s/var/log',               180),
    (r'\b(ausearch|aureport)\b',                 180),
    (r'\b(last|lastb|lastlog)\b',                 30),
    # Process / network state
    (r'\b(ps|ss|netstat|lsof)\b',                 30),
    (r'\b(getcap|find\s+/.+-perm)',              180),
    # Hash / IOC enrichment
    (r'\b(sha256sum|md5sum|sha1sum)\b',          120),
    (r'\bhashdeep\b',                            300),
    (r'\bcurl\b.*virustotal|\bcurl\b.*abuseipdb', 30),
    (r'\bcurl\b',                                 30),
    # IDS rule mgmt
    (r'\bsuricata-update\b',                     180),
    (r'\b(sigma|chainsaw|hayabusa)\b',           600),
    # OSINT / external intel (kept short)
    (r'\b(whois|dig|host|nslookup)\b',            20),
]
DEFAULT_COMMAND_TIMEOUT = 300  # 5 min ceiling on anything else

# Markers in stdout/stderr that mean "needs root".  When detected after
# a non-sudo command, Zeus offers an automatic sudo retry.
SUDO_RETRY_MARKERS = [
    "operation not permitted",
    "permission denied",
    "you don't have permission",
    "you must be root",
    "must be run as root",
    "must be root",
    "requires root",
    "are you root",
    "cap_net_raw",
    "cap_net_admin",
    "cap_dac_read_search",
    "(may need root)",
    "raw sockets",
    "couldn't open device",
    "bind: permission denied",
    "socket: operation not permitted",
]

# Boot-check lock TTL.  Re-run the system check if older than this.
BOOT_LOCK_TTL_SECONDS = 6 * 3600   # 6 hours

# v7.2 — kwarg synonym map for ToolBuilder.  When the LLM emits an arg
# that doesn't match the builder signature, we try one of these
# synonyms BEFORE giving up.  Maps {builder_name: {wrong_name: right_name}}.
# A right_name of None means "drop silently — this is a no-op alias".
KWARG_SYNONYMS = {
    "sherlock_run": {
        "user":             "username",
        "handle":           "username",
        "name":             "username",
        "timeout":          "timeout_sec",
        "only_found":       "print_found_only",
    },
    "maigret_run": {
        "user":             "username",
        "handle":           "username",
        "name":             "username",
        "timeout":          "timeout_sec",
        "sites":            "top_sites",
        "site_count":       "top_sites",
    },
    "socialscan_run": {
        "user":             "target",
        "username":         "target",
        "email":            "target",
    },
    "whatsmyname_query": {
        "user":             "username",
        "handle":           "username",
    },
    "holehe_run": {
        "address":          "email",
        "mail":             "email",
    },
    "gravatar_lookup": {
        "address":          "email",
    },
    "github_email_search": {
        "address":          "email",
    },
    "email_dns_records": {
        "domain":           "email_or_domain",
        "email":            "email_or_domain",
        "address":          "email_or_domain",
    },
    "phoneinfoga_scan": {
        "phone":            "number",
        "tel":              "number",
        "msisdn":           "number",
    },
    "whois_lookup": {
        "domain":           "target",
        "ip":               "target",
        "host":             "target",
    },
    "dig_lookup": {
        "host":             "domain",
        "rrtype":           "record_type",
        "type":             "record_type",
        "rtype":            "record_type",
    },
    "host_lookup": {
        "domain":           "target",
        "ip":               "target",
        "name":             "target",
    },
    "subfinder_passive": {
        "host":             "domain",
        "target":           "domain",
        "quiet":            "silent",
    },
    "amass_passive": {
        "host":             "domain",
        "target":           "domain",
    },
    "assetfinder_run": {
        "host":             "domain",
        "target":           "domain",
    },
    "crt_sh_query": {
        "host":             "domain",
        "target":           "domain",
    },
    "reverse_ip_hackertarget": {
        "ip":               "ip_or_domain",
        "domain":           "ip_or_domain",
        "target":           "ip_or_domain",
    },
    "asn_lookup": {
        "address":          "ip",
        "host":             "ip",
    },
    "whatweb_passive": {
        "url":              "target",
        "host":             "target",
        "domain":           "target",
    },
    "http_headers": {
        "host":             "url",
        "target":           "url",
    },
    "waybackurls_run": {
        "host":             "domain",
        "target":           "domain",
        "limit":            "max_results",
    },
    "gau_run": {
        "host":             "domain",
        "target":           "domain",
        "limit":            "max_results",
    },
    "wayback_check": {
        "target":           "url",
    },
    "exiftool_run": {
        "image":            "image_path",
        "file":             "image_path",
        "path":             "image_path",
    },
    "exiftool_gps_only": {
        "image":            "image_path",
        "file":             "image_path",
        "path":             "image_path",
    },
    "github_user_api": {
        "user":             "username",
        "handle":           "username",
        "name":             "username",
    },
    "github_keys_check": {
        "user":             "username",
        "handle":           "username",
    },
    "github_repos_list": {
        "user":             "username",
        "handle":           "username",
        "order":            "sort",
    },
    "github_events": {
        "user":             "username",
        "handle":           "username",
    },
    "github_user_search": {
        "name":             "query",
        "term":             "query",
        "field":            "qualifier",
    },
    "reddit_user_info": {
        "user":             "username",
        "handle":           "username",
    },
    "mastodon_lookup": {
        "user":             "handle",
        "name":             "handle",
        "instance":         "server",
    },
    "bluesky_resolve": {
        "user":             "handle",
        "name":             "handle",
    },
    "google_dork_curated": {
        "host":             "domain",
        "target":           "domain",
        "type":             "dork_type",
        "kind":             "dork_type",
    },
    "github_dork": {
        "term":             "query",
        "search":           "query",
    },
    "btc_address_balance": {
        "addr":             "address",
        "wallet":           "address",
    },
    "btc_address_txs": {
        "addr":             "address",
        "wallet":           "address",
    },
    "eth_address_balance": {
        "addr":             "address",
        "wallet":           "address",
    },
    "blockchair_address": {
        "addr":             "address",
        "wallet":           "address",
        "network":          "chain",
        "ticker":           "chain",
    },
    "otx_indicator": {
        "ioc":              "indicator",
        "value":            "indicator",
        "type":             "indicator_type",
    },
    "threatfox_search": {
        "indicator":        "ioc",
        "value":            "ioc",
    },
    "urlhaus_check": {
        "target":           "url",
        "ioc":              "url",
    },
    "ipinfo_lookup": {
        "address":          "ip",
        "host":             "ip",
    },
    "curl_basic": {
        "target":           "url",
        "address":          "url",
        "ua":               "user_agent",
        "agent":            "user_agent",
        "head":             "head_only",
        "timeout":          "max_time",
    },
    "theharvester_passive": {
        "host":             "domain",
        "target":           "domain",
        "engines":          "sources",
    },
    "urlscan_search": {
        "host":             "domain",
        "target":           "domain",
    },
}


# ═════════════════════════════════════════════════════════════════════
# SAFETY LISTS — OSINT mode
#
# Zeus is read-only OSINT only.  It refuses anything that:
#   - bypasses authentication or login walls
#   - brute-forces or guesses credentials
#   - touches stolen credential dumps (HIBP yes/no is fine; dumps are not)
#   - asks for real-time location or street-level home address
#   - scrapes non-public data or ignores robots.txt
#   - touches stalkerware-adjacent people-search aggregators
#   - modifies the local system in any way
# ═════════════════════════════════════════════════════════════════════

# All system-modifying patterns from Athena/Ares are HARD REFUSALS in
# Zeus.  No double-confirm — the tool simply won't run them.
BANNED_COMMANDS = [
    "apt upgrade", "apt full-upgrade",
    "apt-get upgrade", "apt-get full-upgrade", "apt dist-upgrade",
    "apt install", "apt-get install",   # OSINT shouldn't install anything either
]
BANNED_UPGRADE_PACKAGES = ["phosh", "lightdm", "xfce", "x11", "gnome-shell"]

DESTRUCTIVE_COMMANDS = [
    # System destruction — same as Ares
    r'\brm\s+-rf\s+/',
    r'\brm\s+-rf\s+\*',
    r'\brm\s+-rf\s+~',
    r'\bdd\s+if=',
    r'\bmkfs\b',
    r'>\s*/dev/sd[a-z]',
    r':\(\)\{.*\|.*&.*\};:',
    r'\bchmod\s+-R\s+777\s+/',
    r'\bchown\s+-R.*\s+/',
    r'\bshutdown\b',
    r'\bhalt\b',
    r'\binit\s+0',
    r'\binit\s+6',
    r'\bpoweroff\b',
    # System mutation — Zeus refuses ALL of these (no double-confirm)
    r'\bsudo\b',                          # OSINT never needs root
    r'\bsystemctl\s+(stop|start|restart|disable|enable|mask|kill)',
    r'\bservice\s+\S+\s+(stop|start|restart)',
    r'\biptables\s+-[FXAID]',
    r'\bnft\s+(flush|delete|add)',
    r'\bufw\s+(enable|disable|allow|deny|reset)',
    r'\bfail2ban-client\s+(ban|unban|stop|start)',
    r'\bkill\s+-9',
    r'\bkillall\b',
    r'\bpkill\b',
    r'>\s*/etc/',
    r'\busermod\b',
    r'\bpasswd\b',
    r'\buseradd\b',
    r'\buserdel\b',
    r'\bchattr\b',
    r'\bauditctl\b',
]

# ── OSINT-specific refuse patterns ──────────────────────────────────
# These keywords/patterns inside a [CMD] or [TOOL] arg trigger a hard
# refusal.  This is the "never suggests anything illegal" guarantee.
OSINT_REFUSE_PATTERNS = [
    # Brute force / credential guessing
    r'\b(brute|bruteforce|brute-force|password.guess|pass.guess)\b',
    r'\bhydra\b', r'\bmedusa\b', r'\bpatator\b', r'\bncrack\b',
    r'\bhashcat\b', r'\bjohn\b\s+(?!the)',           # 'john the ripper' usage
    r'\bcrackmapexec\b', r'\bnxc\b\s+(smb|ssh|winrm|rdp|ftp)',
    r'\bkerbrute\b', r'\bsprayhound\b',
    # Authenticated / login bypass
    r'\b(login|signin|sign-in|auth).{0,8}(bypass|crack|skip)\b',
    r'\bcaptcha.{0,8}(solve|bypass|crack)\b',
    # Credential dump / stolen data access
    r'\bdehashed\b.*[\?&](password|hash)=',          # Querying actual passwords
    r'\bcombolists?\b', r'\bbreach.compilation\b',
    r'\bweleakinfo\b', r'\bsnusbase\b', r'\bleakcheck\b',
    # Real-time tracking / stalkerware
    r'\b(real.?time|live).{0,12}(track|locate|location|position)\w*',
    r'\bcell.tower\b', r'\bimsi.catcher\b',
    r'\b(home|street).{0,8}address.{0,8}(lookup|find|search)\b',
    # Authenticated scrapers — also blocked at the interactive gate, but
    # we double-up here so the refusal message is OSINT-flavoured.
    r'\binstaloader\b',
    r'\binstagram-scraper\b',
    r'\btwint\b\s+(-u|--username)',
    # Stalkerware-adjacent people-search aggregators
    r'\bspokeo\b', r'\btruepeoplesearch\b', r'\bbeenverified\b',
    r'\bintelius\b', r'\bpipl\b', r'\bzaba\b',
    r'\bpeoplefinder\b', r'\bwhitepages\.com\b',
    # Voter rolls (jurisdiction-restricted, mostly stalking)
    r'\bvoter.{0,4}roll\b', r'\bvoter.{0,4}registration\b',
    # SS7 / cellular intercept
    r'\bss7\b', r'\bssti\b.*sms', r'\bsms.intercept\b',
    # Doxbin / dox sites
    r'\bdoxbin\b', r'\bdoxxx?\b', r'\bdox.{0,4}drop\b',
    # CSAM / minors — instant refuse on any reference
    r'\bcsam\b', r'\bchild.{0,6}(porn|sex)\b',
    r'\bminor.{0,6}(track|locate|find.address)\b',
    # Domestic-abuse-adjacent intent (bidirectional — track may come
    # before or after the relationship word)
    r'\bex.{0,4}(girlfriend|boyfriend|wife|husband|partner)\b',
    r'\b(track|locate|find|stalk).{0,30}(ex.?(girlfriend|boyfriend|wife|husband|partner))\b',
    r'\brestrain.order\b.{0,30}(bypass|circumvent)\b',
    # Generic illegal-data flags
    r'\bstolen.{0,8}(data|credentials?|database)\b',
    r'\bleaked.{0,8}database\b.*[\?&]download',
]

# Empty — Zeus has nothing to double-confirm.  Either a command is
# safe (runs) or it's refused.  Kept as an empty list so the existing
# `_needs_double_confirm()` helper compiles.
DOUBLE_CONFIRM: List[str] = []

INTERACTIVE_BLOCKED = {
    "msfconsole":     "Zeus is OSINT-only — msfconsole is offensive tooling, not OSINT.",
    "mysql -u":       "Zeus shouldn't be hitting authenticated DBs — that's not public OSINT.",
    "psql":           "Zeus shouldn't be hitting authenticated DBs — that's not public OSINT.",
    "telnet":         "Zeus is OSINT-only — telnet to a target isn't OSINT.",
    "nc -l":          "Listener — not an OSINT operation.",
    "ncat -l":        "Listener — not an OSINT operation.",
    "vim ":           "Use: cat for non-interactive viewing.",
    "vi ":            "Use: cat for non-interactive viewing.",
    "nano ":          "Use: cat for non-interactive viewing.",
    "less ":          "Use: cat or head/tail.",
    "more ":          "Use: cat or head/tail.",
    "top":            "Use: ps aux for snapshot.",
    "htop":           "Use: ps aux for snapshot.",
    "ssh ":           "SSH interactive — not OSINT.",
    "ftp ":           "FTP interactive — not OSINT.",
    "watch ":         "watch loops forever — Zeus does its own polling.",
    "tail -f":        "tail -f hangs — not appropriate for OSINT runs.",
    # Authenticated scrapers explicitly blocked
    "instaloader":    "Authenticated Instagram scraping violates ToS — not legal OSINT.",
    "instagram-scraper": "Authenticated scraping violates ToS — refused.",
    "twint":          "Twint requires Twitter auth circumvention — refused.",
}


# ═════════════════════════════════════════════════════════════════════
# COMPREHENSIVE KALI TOOL REGISTRY
#
# Zeus uses this both to (a) tell the AI what's available so it stops
# proposing tools that don't exist, and (b) auto-install missing tools
# on demand.  Categorised for quick lookup by phase.
# ═════════════════════════════════════════════════════════════════════

KALI_TOOLS = {
    "log_analysis": [
        "journalctl", "ausearch", "aureport", "auditctl", "last", "lastb",
        "lastlog", "logwatch", "rsyslog", "syslog-ng", "fluent-bit",
        "grep", "awk", "sed", "jq", "logger",
    ],
    "process_state": [
        "ps", "pstree", "top", "htop", "pgrep", "pidof", "lsof", "ss",
        "netstat", "iotop", "vmstat", "fuser", "pmap", "smem",
        "atop", "glances",
    ],
    "host_hardening": [
        "lynis", "tiger", "chkrootkit", "rkhunter", "debsums", "aide",
        "tripwire", "samhain", "ossec", "wazuh-agent",
        "auditd", "audispd-plugins", "apparmor-utils", "selinux-utils",
        "openscap-utils", "scap-security-guide",
    ],
    "ids_ips": [
        "suricata", "suricata-update", "snort", "zeek", "bro",
        "fail2ban-client", "psad", "portsentry", "fwknop",
        "ufw", "iptables", "nftables", "nft", "iptables-save",
    ],
    "network_capture": [
        "tcpdump", "tshark", "dumpcap", "ngrep", "tcpflow", "tcpick",
        "tcpreplay", "argus", "ra", "rwfilter",
        "wireshark-cli", "termshark", "netsniff-ng",
    ],
    "network_state": [
        "ss", "netstat", "ip", "iproute2", "iftop", "nethogs",
        "bmon", "vnstat", "iptraf-ng", "nstat", "nload",
    ],
    "memory_forensics": [
        "volatility3", "vol", "volatility", "rekall", "lime-forensics",
        "avml", "memdump", "yara", "yarac",
    ],
    "disk_forensics": [
        "sleuthkit", "fls", "icat", "ils", "mmls", "fsstat", "tsk_recover",
        "autopsy", "foremost", "scalpel", "bulk_extractor",
        "testdisk", "photorec", "guymager", "dc3dd", "dcfldd",
        "ddrescue", "ewfacquire", "ewfverify",
    ],
    "malware_triage": [
        "yara", "yarac", "clamav", "clamscan", "freshclam", "clamdscan",
        "loki", "thor-lite", "capa", "die", "trid",
        "binwalk", "strings", "file", "exiftool", "pev",
        "objdump", "readelf", "nm", "ldd", "checksec",
    ],
    "ioc_enrichment": [
        "curl", "wget", "jq", "dig", "host", "whois", "abuseipdb-cli",
        "vt-cli", "shodan", "censys", "passivetotal-cli",
        "mitre-attack-cli", "stix-shifter",
    ],
    "identity_audit": [
        "getent", "id", "groups", "passwd", "chage", "faillog",
        "pwck", "grpck", "userdbctl", "loginctl",
        "ldapsearch", "samba-tool", "krb5-user",
    ],
    "file_integrity": [
        "aide", "tripwire", "samhain", "afick",
        "sha256sum", "sha512sum", "md5sum", "hashdeep",
        "debsums", "rpm", "pacman", "diff", "rsync",
    ],
    "siem_query": [
        "chainsaw", "hayabusa", "sigma-cli", "sigmac",
        "elasticsearch-cli", "logstash", "filebeat", "winlogbeat",
        "splunk-cli", "wazuh-cli",
    ],
    "container_audit": [
        "docker", "podman", "kubectl", "kube-bench", "trivy", "grype",
        "syft", "dockle", "clair", "anchore-cli",
        "falco", "tetragon", "crictl",
    ],
    "secrets_audit": [
        "trufflehog", "gitleaks", "detect-secrets", "ripsecrets",
        "ggshield", "git-secrets",
    ],
    "configuration_audit": [
        "openscap", "oscap", "lynis", "kube-bench",
        "checkov", "tfsec", "kics", "terrascan", "scoutsuite",
        "prowler", "cloudsploit",
    ],
    "rootkit_hunters": [
        "rkhunter", "chkrootkit", "lynis", "unhide", "tiger",
        "samhain", "aide", "debsums",
    ],
    "ssl_tls": [
        "sslscan", "sslyze", "testssl.sh", "openssl", "ssh-audit",
        "tlsx", "cipherscan",
    ],
    "dns_defense": [
        "dig", "host", "nslookup", "dnstop", "dnsmonster",
        "passivedns", "dnsrecon",
    ],
    "binary_inspection": [
        "strings", "file", "exiftool", "binwalk", "ldd",
        "objdump", "readelf", "nm", "checksec", "die",
        "radare2", "rizin", "ghidra-server", "r2",
    ],
    "live_response": [
        "lime-forensics", "avml", "fmem", "memdump",
        "lsof", "ss", "ps", "find", "stat",
        "getent", "last", "w", "who",
    ],
    "exfil_detection": [
        "tcpdump", "tshark", "zeek", "suricata", "argus",
        "rita", "passivedns", "joy",
    ],
    "kernel_audit": [
        "sysctl", "modprobe", "lsmod", "dmesg", "auditctl",
        "kernsec", "checksec", "kernel-hardening-checker",
    ],
    "misc_useful": [
        "curl", "wget", "nc", "ncat", "socat", "tmux", "screen",
        "jq", "tee", "xxd", "hexdump", "base64", "openssl",
        "tshark", "tcpdump", "git", "python3", "pip3",
        "sed", "awk", "grep", "find", "xargs",
    ],
}


def all_kali_tools_flat() -> List[str]:
    seen = set()
    flat = []
    for cat, tools in KALI_TOOLS.items():
        for t in tools:
            if t not in seen:
                seen.add(t)
                flat.append(t)
    return flat


def kali_tool_summary_for_prompt() -> str:
    """Compressed list for system prompts so AI knows what's available.

    Zeus filters KALI_TOOLS to categories that overlap with OSINT —
    DNS lookups, certificate inspection, binary/EXIF tools, and
    miscellaneous utilities.  Defensive categories (ids_ips,
    memory_forensics, disk_forensics, malware_triage, etc.) are
    suppressed: they have nothing to do with public-source OSINT
    and just bias the LLM toward the wrong tools.  The actual
    primary OSINT tools (sherlock / holehe / maigret / phoneinfoga
    / whois / subfinder / amass / exiftool) come from the structured
    tool registry below this block, not from KALI_TOOLS."""
    OSINT_RELEVANT_CATS = {
        "dns_defense",       # dig, host, nslookup — useful for registrar
        "ssl_tls",           # cert investigation
        "binary_inspection", # file, strings, exiftool — useful for cartographer
        "misc_useful",       # general utilities
    }
    parts = []
    for cat, tools in KALI_TOOLS.items():
        if cat not in OSINT_RELEVANT_CATS:
            continue
        # Trim to the most important per category to save tokens
        parts.append(f"  {cat}: {', '.join(tools[:10])}")
    return "KALI ARSENAL (OSINT-relevant subset):\n" + "\n".join(parts)


# ═════════════════════════════════════════════════════════════════════
# FINDING PATTERNS — strict, context-aware
#
# Lessons from v6.1: regex like `(?:password|pass)[:\s=]+(\S+)` matches
# the AI's own thinking ("...try password: helper...") and pollutes
# state.  v7.0 only runs these on raw subprocess stdout, never on the
# model's text.  Patterns are also tightened so noise like "200:not"
# (which came from "user:200, pass:not" in the AI's prose) can't match.
# ═════════════════════════════════════════════════════════════════════

FINDING_PATTERNS = {
    # IPv4 addresses (still useful — IOC IPs)
    "ip":        r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b',

    # Listening ports from ss/netstat: "tcp LISTEN ... :22 ..."
    "port":      r'(?:LISTEN|0\.0\.0\.0:|\*:|\[::\]:)(\d{1,5})\b',

    # Service+version on listening sockets via ss -tlnp output
    "svc":       r'users:\(\("([A-Za-z][A-Za-z0-9_\-]{1,40})"',

    # Suspicious user creation / login: "user X" tagged as account hits
    "account":   r'(?:^|\n|\s)(?:user|account|login|sAMAccountName|uid)[:\s=]+([a-zA-Z][a-zA-Z0-9_\.\-]{2,32})\b',

    # Hash values picked up while reviewing files (could be IOC or local)
    "hash":      r'(?:^|\n|\s|:|=)([a-fA-F0-9]{32,64})(?:\s|$|:)',

    # CVEs surfaced by audit tools (lynis / wesng / openscap)
    "cve":       r'\b(CVE-\d{4}-\d{4,7})\b',

    # Domains (broad — IOC enrichment / suspicious DNS)
    "domain":    r'\b([a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z]{2,})+)\b',

    # URLs (often IOCs in pcap / log analysis)
    "url":       r'(https?://[^\s\'"<>]+)',

    # MITRE ATT&CK technique IDs surfaced by sigma/chainsaw/hayabusa
    "attack_id": r'\b(T1[0-9]{3}(?:\.\d{3})?)\b',

    # YARA matches: "RuleName matched at /path/to/file"
    "yara_hit":  r'^([A-Za-z_][A-Za-z0-9_]+)\s+(/\S+)',

    # ClamAV / signature-based AV: "/path/file: Win.Trojan.Foo FOUND"
    "av_hit":    r'(/[\w\.\-/]+):\s+([A-Za-z][\w\.\-]+)\s+FOUND',

    # Suspicious processes (output of pstree / ps with embedded warnings)
    "suspicious_proc": r'(?:^|\s)((?:python\d?|bash|sh|perl|nc|ncat|socat)\s+-[ce]\s+["\']?[^"\'\s]{16,})',

    # Suricata fast.log alert format
    "suricata_alert": r'\[\*\*\]\s+\[(?:\d+:){2}\d+\]\s+(.+?)\s+\[\*\*\]',

    # Cron entries — look for schedules in non-standard files
    "cron_entry": r'^(?:\*|[0-9]{1,2}|[0-9]{1,2}-[0-9]{1,2}|\*/[0-9]+)\s+\S+\s+\S+\s+\S+\s+\S+\s+(.+)$',

    # SUID files (find -perm -4000 output)
    "suid":      r'^(/\S+)\s.*-rw[sx]r-[sx]r-[sx]',

    # Capabilities (getcap output: "/path/file = cap_xxx")
    "cap_grant": r'^(/\S+)\s+=\s+(cap_\w[\w\,\+\=]*)',

    # Failed auth events (sshd / pam)
    "auth_fail": r'(?:Failed password|authentication failure|Invalid user)\s+(?:for\s+)?(\S+)',

    # Successful sudo escalations (could be benign or IOC)
    "sudo_use":  r'sudo:\s+(\w+)\s+:\s+TTY=\S+\s+;\s+PWD=(\S+)\s+;\s+USER=(\S+)\s+;\s+COMMAND=',

    # Persistence — systemd unit files in non-standard locations
    "persistence": r'((?:/etc/systemd/system/|/lib/systemd/system/|/home/\S+/\.config/systemd/user/)[\w\-\.@]+\.(?:service|timer))',

    # Docker container IDs (suspicious or unauthorized)
    "container": r'\b([a-f0-9]{12,64})\s+\S+\s+(?:/|"\$/|\b(?:bash|sh|/bin)',

    # Email addresses (in logs — could be IOC or compromised user)
    "email":     r'\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b',

    # SSH private key markers (CRITICAL if found exposed)
    "ssh_key":   r'(-----BEGIN (?:RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----)',

    # AWS-style keys (DLP / secrets exposure)
    "aws_key":   r'\b(AKIA[0-9A-Z]{16})\b',
}

# These IPs are noise — don't add them as findings
IP_NOISE = {
    '0.0.0.0', '127.0.0.1', '255.255.255.255',
    '8.8.8.8', '8.8.4.4', '1.1.1.1', '1.0.0.1',
    '169.254.169.254',  # cloud metadata — noted elsewhere, not a finding
}

# Domains that are noise (shown in command outputs but not real findings)
DOMAIN_NOISE = {
    'localhost', 'example.com', 'google.com', 'cloudflare.com',
    'localdomain', 'arpa', 'in-addr.arpa',
}

# Valid TLD whitelist — used to reject false-positive domain/email
# matches like `boot.log`, `system@HASH.journal`, `lynis-report.dat`.
# A finding is rejected if the segment after the last dot is NOT in
# this set.  Includes all 2-letter country codes and the gTLDs Zeus
# is realistically going to encounter in OSINT output.
VALID_TLDS = {
    # Core gTLDs
    'com', 'org', 'net', 'info', 'biz', 'edu', 'gov', 'mil', 'int',
    'name', 'pro', 'museum', 'aero', 'coop', 'jobs', 'mobi', 'travel',
    'asia', 'cat', 'post', 'tel', 'xxx',
    # Tech / common new gTLDs
    'io', 'dev', 'app', 'ai', 'me', 'co', 'tv', 'fm', 'ly', 'gg', 'sh',
    'im', 'gl', 'st', 'vc', 'ws', 'cc', 'bz', 'la', 'mu', 'mn',
    'tech', 'online', 'site', 'store', 'shop', 'blog', 'cloud', 'host',
    'web', 'page', 'link', 'click', 'fyi', 'world', 'life', 'club',
    'today', 'news', 'email', 'team', 'works', 'space', 'live', 'press',
    'design', 'studio', 'media', 'pub', 'community', 'social',
    'xyz', 'top', 'art', 'tools', 'help', 'wtf', 'gay', 'lol',
    'codes', 'agency', 'company', 'business', 'services', 'systems',
    'network', 'group', 'global', 'tools', 'guru', 'engineer',
    'digital', 'video', 'photo', 'photography', 'gallery', 'review',
    'host', 'website', 'support', 'expert', 'directory', 'computer',
    'rocks', 'ninja', 'guru', 'cool', 'one', 'zero', 'plus',
    # All 2-letter country code TLDs
    'ac','ad','ae','af','ag','ai','al','am','ao','aq','ar','as','at',
    'au','aw','ax','az','ba','bb','bd','be','bf','bg','bh','bi','bj',
    'bm','bn','bo','br','bs','bt','bw','by','bz','ca','cd','cf','cg',
    'ch','ci','ck','cl','cm','cn','co','cr','cu','cv','cw','cx','cy',
    'cz','de','dj','dk','dm','do','dz','ec','ee','eg','er','es','et',
    'eu','fi','fj','fk','fm','fo','fr','ga','gb','gd','ge','gf','gg',
    'gh','gi','gl','gm','gn','gp','gq','gr','gs','gt','gu','gw','gy',
    'hk','hn','hr','ht','hu','id','ie','il','im','in','io','iq','ir',
    'is','it','je','jm','jo','jp','ke','kg','kh','ki','km','kn','kp',
    'kr','kw','ky','kz','la','lb','lc','li','lk','lr','ls','lt','lu',
    'lv','ly','ma','mc','md','me','mg','mh','mk','ml','mm','mn','mo',
    'mp','mq','mr','ms','mt','mu','mv','mw','mx','my','mz','na','nc',
    'ne','nf','ng','ni','nl','no','np','nr','nu','nz','om','pa','pe',
    'pf','pg','ph','pk','pl','pm','pn','pr','ps','pt','pw','py','qa',
    're','ro','rs','ru','rw','sa','sb','sc','sd','se','sg','sh','si',
    'sk','sl','sm','sn','so','sr','ss','st','sv','sx','sy','sz','tc',
    'td','tf','tg','th','tj','tk','tl','tm','tn','to','tr','tt','tv',
    'tw','tz','ua','ug','uk','us','uy','uz','va','vc','ve','vg','vi',
    'vn','vu','wf','ws','ye','yt','za','zm','zw',
}

def _has_valid_tld(value: str) -> bool:
    """Return True iff the segment after the last '.' is a recognised
    TLD.  Used to reject filename-shaped false positives like
    `boot.log`, `lynis-report.dat`, `system.journal`."""
    if "." not in value:
        return False
    tld = value.rsplit(".", 1)[-1].lower()
    if not tld or len(tld) > 12:
        return False
    return tld in VALID_TLDS


# Sensitive paths to flag as "exposed_path" findings if found in output
SENSITIVE_PATH_PATTERNS = [
    r'\.ssh/',
    r'\.bash_history',
    r'\.bashrc\b',
    r'\.git/',
    r'\.env\b',
    r'\.aws/',
    r'wp-config\.php',
    r'config\.php',
    r'/etc/passwd',
    r'/etc/shadow',
    r'/etc/hosts',
    r'id_rsa\b',
    r'id_ed25519\b',
    r'id_ecdsa\b',
    r'authorized_keys',
    r'\.htpasswd',
    r'web\.config',
    r'database\.yml',
    r'application\.properties',
    r'\.npmrc\b',
    r'\.docker/config\.json',
    r'\.kube/config',
]


# Common locations for defensive rulesets — used as defaults when the
# AI requests a yara / sigma run and doesn't specify a path.
YARA_RULE_PATHS = [
    "/usr/share/yara/rules",
    "/var/lib/yara",
    "/opt/yara-rules",
    "/etc/yara",
]

SIGMA_RULE_PATHS = [
    "/usr/share/sigma/rules",
    "/opt/sigma/rules",
    "/var/lib/sigma",
]

CLAMAV_DB_PATHS = [
    "/var/lib/clamav",
    "/var/clamav",
]

# Known noisy / benign processes — don't flag these as suspicious_proc
PROCESS_BENIGN = {
    "systemd", "init", "kthreadd", "kworker", "ksoftirqd", "rcu_sched",
    "migration", "watchdog", "sshd", "rsyslogd", "cron", "dhclient",
    "NetworkManager", "wpa_supplicant", "polkitd", "udevd",
    "snapd", "agetty", "login", "bash", "zsh", "fish", "dbus-daemon",
}

# Critical paths — alerts if writes detected here without authorization
CRITICAL_WATCHED_PATHS = [
    "/etc/passwd",
    "/etc/shadow",
    "/etc/sudoers",
    "/etc/sudoers.d/",
    "/etc/ssh/sshd_config",
    "/etc/ssh/ssh_config",
    "/root/.ssh/authorized_keys",
    "/etc/cron.d/",
    "/etc/cron.daily/",
    "/etc/cron.hourly/",
    "/etc/systemd/system/",
    "/lib/systemd/system/",
    "/etc/ld.so.preload",
    "/etc/pam.d/",
    "/etc/security/",
    "/etc/iptables/",
    "/etc/nftables.conf",
]


# ═════════════════════════════════════════════════════════════════════
# MITRE ATT&CK MAPPING (v7.1)
#
# Auto-tag commands and findings with technique IDs so reports can be
# grouped by ATT&CK technique — the professional standard for pentest
# deliverables.  Pattern-based: command substring or finding type
# triggers the tag.  First match wins.
# ═════════════════════════════════════════════════════════════════════

MITRE_TECHNIQUES = [
    # Zeus uses OSINT categories instead of ATT&CK techniques.  Same
    # tuple shape (regex, id, name, tactic) so the existing tagging
    # pipeline keeps working unchanged — but the "technique_id" is
    # now an OSINT category code.

    # Username / handle pivots
    (r'\b(sherlock|maigret|socialscan|whatsmyname)\b',
        "OSINT-SOCIAL", "Social Presence Pivot",     "SOCIAL_PRESENCE"),
    (r'github\.com/[^/\s]+(\.keys|\.gpg)',
        "OSINT-SOCIAL", "GitHub Public Key Pivot",   "SOCIAL_PRESENCE"),
    (r'reddit\.com/user/|/api/v1/accounts/lookup|bsky\.app',
        "OSINT-SOCIAL", "Public Social API Lookup",  "SOCIAL_PRESENCE"),

    # Email triage
    (r'\bholehe\b|\bsocialscan\b.*@',
        "OSINT-EMAIL",  "Email Account Enumeration", "LEAKED_INTEL_PUBLIC"),
    (r'gravatar\.com.*\.json',
        "OSINT-EMAIL",  "Gravatar Public Profile",   "REPUTATION"),

    # Phone OSINT
    (r'\bphoneinfoga\b',
        "OSINT-PHONE",  "Phone Number OSINT",        "PUBLIC_RECORDS"),

    # Domain / DNS / WHOIS
    (r'\bwhois\b',
        "OSINT-DOMAIN", "WHOIS Registration",        "DOMAIN_FOOTPRINT"),
    (r'\bdig\b\s|\bhost\b\s|\bnslookup\b',
        "OSINT-DNS",    "DNS Records",               "INFRASTRUCTURE"),
    (r'\b(subfinder|amass|assetfinder|findomain)\b',
        "OSINT-SUBS",   "Subdomain Enumeration",     "DOMAIN_FOOTPRINT"),
    (r'crt\.sh',
        "OSINT-CERT",   "Certificate Transparency",  "DOMAIN_FOOTPRINT"),
    (r'bgpview\.io|api\.cymru',
        "OSINT-ASN",    "ASN / BGP Lookup",          "INFRASTRUCTURE"),
    (r'hackertarget\.com.*reverseip',
        "OSINT-RDNS",   "Reverse-IP Lookup",         "INFRASTRUCTURE"),
    (r'\bwhatweb\b',
        "OSINT-TECH",   "Web Tech Fingerprint",      "INFRASTRUCTURE"),

    # Archive history
    (r'\b(waybackurls|gau)\b|web\.archive\.org|archive\.org/wayback',
        "OSINT-ARCH",   "Archive History Recovery",  "ARCHIVE_HISTORY"),
    (r'urlscan\.io',
        "OSINT-URL",    "URL History Lookup",        "ARCHIVE_HISTORY"),

    # Image / EXIF
    (r'\bexiftool\b',
        "OSINT-EXIF",   "Image Metadata Extraction", "METADATA"),

    # GitHub
    (r'api\.github\.com/users|\bgithub\.com/[^/]+\.(keys|gpg)',
        "OSINT-GH",     "GitHub User OSINT",         "SOCIAL_PRESENCE"),
    (r'api\.github\.com/search/code',
        "OSINT-GH-CODE","GitHub Code Search",        "LEAKED_INTEL_PUBLIC"),

    # Dorking
    (r'site:|inurl:|intext:|filetype:',
        "OSINT-DORK",   "Search-Engine Dork",        "LEAKED_INTEL_PUBLIC"),

    # Blockchain
    (r'blockchain\.info|blockstream\.info|blockchair|etherscan\.io',
        "OSINT-CHAIN",  "Public Blockchain Query",   "CRYPTO_LEDGER"),

    # IOC enrichment / threat intel (threat-actor lane)
    (r'otx\.alienvault\.com|threatfox-api|urlhaus-api|abuse\.ch|mb-api',
        "OSINT-IOC",    "IOC Enrichment Lookup",     "REPUTATION"),
    (r'ipinfo\.io',
        "OSINT-GEOIP",  "IP Geolocation",            "INFRASTRUCTURE"),
    (r'shodan\.io',
        "OSINT-SHODAN", "Shodan Host Lookup",        "INFRASTRUCTURE"),

    # Public records / corporate
    (r'opencorporates\.com|sec\.gov|company-information\.service',
        "OSINT-CORP",   "Corporate Filings",         "PUBLIC_RECORDS"),

    # Gravatar / reputation
    (r'gravatar\.com',
        "OSINT-AVATAR", "Public Profile Photo",      "REPUTATION"),
]

# Tag findings by their type when no command pattern matched
MITRE_BY_FINDING = {
    "ip":              ("OSINT-INFRA",  "Public IP",                 "INFRASTRUCTURE"),
    "port":            ("OSINT-INFRA",  "Open Port (passive)",       "INFRASTRUCTURE"),
    "svc":             ("OSINT-TECH",   "Service Fingerprint",       "INFRASTRUCTURE"),
    "account":         ("OSINT-SOCIAL", "Social Account",            "SOCIAL_PRESENCE"),
    "hash":            ("OSINT-IOC",    "Hash IOC",                  "REPUTATION"),
    "cve":             ("OSINT-VULN",   "CVE Reference",             "REPUTATION"),
    "ssh_key":         ("OSINT-KEY",    "SSH Public Key",            "SOCIAL_PRESENCE"),
    "aws_key":         ("OSINT-LEAK",   "AWS Key Leakage",           "LEAKED_INTEL_PUBLIC"),
    "email":           ("OSINT-EMAIL",  "Email Address",             "SOCIAL_PRESENCE"),
    "domain":          ("OSINT-DOMAIN", "Domain Name",               "DOMAIN_FOOTPRINT"),
    "url":             ("OSINT-URL",    "URL Reference",             "ARCHIVE_HISTORY"),
    "subdomain":       ("OSINT-SUBS",   "Subdomain",                 "DOMAIN_FOOTPRINT"),
    "phone":           ("OSINT-PHONE",  "Phone Number",              "PUBLIC_RECORDS"),
    "username":        ("OSINT-SOCIAL", "Username Hit",              "SOCIAL_PRESENCE"),
    "github_user":     ("OSINT-GH",     "GitHub Username",           "SOCIAL_PRESENCE"),
    "exif_gps":        ("OSINT-EXIF",   "GPS in EXIF",               "METADATA"),
    "wayback_url":     ("OSINT-ARCH",   "Archived URL",              "ARCHIVE_HISTORY"),
    "btc_address":     ("OSINT-CHAIN",  "Bitcoin Address",           "CRYPTO_LEDGER"),
    "eth_address":     ("OSINT-CHAIN",  "Ethereum Address",          "CRYPTO_LEDGER"),
    "asn":             ("OSINT-ASN",    "Autonomous System",         "INFRASTRUCTURE"),
    "cert_san":        ("OSINT-CERT",   "Cert Subject Alt Name",     "DOMAIN_FOOTPRINT"),
    "attack_id":       ("",             "OSINT category surfaced",   ""),
}


def attack_id_for_command(cmd: str) -> Optional[Tuple[str, str, str]]:
    """Return (technique_id, name, tactic) for a command, or None."""
    if not cmd:
        return None
    for pattern, tid, name, tactic in MITRE_TECHNIQUES:
        try:
            if re.search(pattern, cmd, re.IGNORECASE):
                return (tid, name, tactic)
        except re.error:
            continue
    return None


def attack_id_for_finding(ftype: str) -> Optional[Tuple[str, str, str]]:
    """Return (technique_id, name, tactic) for a finding type, or None."""
    return MITRE_BY_FINDING.get(ftype)


# Exit-code semantics for run_command return values
EXEC_SESSION_EXIT       = "__SESSION_EXIT__"
EXEC_INTERACTIVE_BLOCKED = "__INTERACTIVE_BLOCKED__"
EXEC_REJECTED           = "__COMMAND_REJECTED__"
EXEC_DESTRUCTIVE        = "__DESTRUCTIVE_REFUSED__"


# ═════════════════════════════════════════════════════════════════════
# KNOWLEDGE BASE — extended from v6.1 with Zeus-specific patterns
# ═════════════════════════════════════════════════════════════════════

KB = {}

KB[1] = r"""
S1 OSINT OPERATOR MINDSET (THE PRIEST'S):
Every finding is public, attributable, and reproducible.  No private
data, no auth bypass, no stolen dumps.  If a tool wants a credential
to "see more," that's the line — Zeus stops there.

Three discipline rules:
  1. PIVOT — Every identifier is a seed for the next.  email → handles,
     handle → other handles, domain → subdomains → cert SANs.
  2. CORROBORATE — One source is a lead, two is a finding.  Never
     report a single-source claim as confirmed.
  3. PRESERVE — Public sources can disappear.  Capture wayback URLs
     and snapshot timestamps as proof-of-existence at time of search.

Lane discipline (declared at session start, enforced for the whole run):
  self-osint     → audit your own footprint, refuse third-party PII pivots
  threat-actor   → handles + infrastructure, NOT real names of operators
  journalism     → public-interest justification logged in report
  due-diligence  → entity-focused (companies/domains), not personal
  bug-bounty     → scope file required, refuse out-of-scope assets
  training       → CTF or known-test-target only, no live people

When in doubt: refuse, log the reason, move on."""

KB[2] = r"""
S2 USERNAME / HANDLE PIVOTING:
Goal: given one handle, surface every platform the same person operates
on.  Quality > coverage — false positives waste turns.

sherlock USERNAME --print-found --no-color --timeout 10
   ~ 400 platforms, fast, network-bound.  Skip with --site if a known
   false-positive site is making noise.

maigret USERNAME --no-color --timeout 10 --json simple -n 50
   Slower but smarter (looks at page content, not just status code).
   Best for ambiguous handles.  -n caps results.

socialscan EMAIL_OR_HANDLE
   Account-availability checker — "is this username taken on X?"  Useful
   inverse to confirm an account EXISTS where sherlock was uncertain.

whatsmyname (curated DB):  curl -s https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json
   Pull the JSON, iterate sites, build URLs per site.  Lower
   false-positive rate than sherlock for some platforms.

PIVOT RULES:
  Found `github:USER` → curl https://api.github.com/users/USER → email,
     bio, followers (some users leak email here).
  Found `reddit:USER` → curl https://www.reddit.com/user/USER/about.json
     → karma, account age, public comments.
  Found a profile bio mentioning a URL → run domain workflow on it.
  Found a profile mentioning a city → record but DO NOT pivot to
     address lookups.

CORROBORATION:
  A handle on one platform is a lead.  Same handle + same display name
  + same avatar (perceptual hash match) on a second platform is
  confirmation of identity-of-handle (not identity-of-person)."""

KB[3] = r"""
S3 EMAIL TRIAGE (free / public only):
The email branch tells you WHERE an email is registered, not WHAT
breaches it appeared in.  HIBP charges $3.50/month and Zeus is
free-only — so we use registration-discovery tools instead.

holehe EMAIL --only-used --no-color
   Probes ~120 sites by triggering their "forgot password" flow and
   reading the response.  Tells you the email IS registered without
   revealing the password.  Public OSINT, ToS-respecting.

mosint EMAIL                         (if available — Go-based, more sources)

Email validity (RFC + MX + SMTP probe — non-intrusive):
   curl -s "https://api.eva.pingutil.com/email?email=ADDR"
   dig MX DOMAIN_OF_EMAIL +short

Gravatar exposure:
   md5=$(echo -n EMAIL | md5sum | cut -d' ' -f1)
   curl -s -o /dev/null -w "%{http_code}" "https://www.gravatar.com/$md5"
     200 = profile exists publicly.  Pivot to:
     curl -s "https://www.gravatar.com/$md5.json" | jq

PGP keyserver lookup:
   curl -s "https://keys.openpgp.org/vks/v1/by-email/ADDR"
   gpg --keyserver hkps://keys.openpgp.org --search-keys EMAIL

GitHub commit-history email pivot:
   gh search commits --author-email EMAIL --json repository
   (or unauthenticated:)
   curl -s "https://api.github.com/search/commits?q=author-email:EMAIL"
       -H "Accept: application/vnd.github.cloak-preview"

DKIM/DMARC posture of the sending domain:
   dig TXT _dmarc.DOMAIN +short
   dig TXT DOMAIN +short | grep -i 'spf'

REFUSED:
  - Querying credential dumps for "the password belonging to this email"
  - Hunter.io / Snov.io paid scrapes (paid + ToS issues)
  - Any service that claims to "expose breach contents" """

KB[4] = r"""
S4 PHONE OSINT (carrier + country + line-type only):
Phone OSINT free-only is limited.  We do NOT pull subscriber names,
addresses, or carrier-internal records.  We DO pull what's structurally
public from the number itself.

phoneinfoga scan -n +14155551234 --no-color
   Country / carrier / line type / timezone / format validation.
   Footprint module hits public mentions on Google + social, but those
   are noisy — verify before recording as findings.

Manual lookups:
   curl -s "https://lookups.twilio.com/v1/PhoneNumbers/+14155551234?Type=carrier"
        # NOT free-only (paid Twilio key needed) — REFUSED in Zeus
   curl -s "http://apilayer.net/api/validate?access_key=$NUMVERIFY_KEY&number=..."
        # NumVerify free tier (100/mo) — OK if user has key, gracefully
        # skip if not.

Number → social media pivot:
   Some platforms expose "is this number registered?" via their signup
   forms.  Holehe-style probing is what we'd want, but for phones it's
   HIGH ToS risk and Zeus refuses it.  Stick to phoneinfoga's metadata.

REFUSED:
  - Reverse-phone-lookup people-search aggregators (Spokeo et al.)
  - Real-time location / cell-tower triangulation (illegal anyway)
  - SS7 anything
  - SMS interception
  - Voicemail probing"""

KB[5] = r"""
S5 DOMAIN / INFRASTRUCTURE FOOTPRINT:
The richest free OSINT lane.  Public DNS + cert transparency + WHOIS
+ archive.org + passive scanning.

WHOIS / RDAP:
   whois DOMAIN | head -60
   curl -s "https://rdap.org/domain/DOMAIN" | jq

DNS surface:
   dig DOMAIN ANY +short
   dig DOMAIN MX TXT NS SOA CAA +short
   dig _dmarc.DOMAIN TXT +short
   host -t any DOMAIN

Subdomain enumeration (passive only — no scanning):
   subfinder -d DOMAIN -silent -all
   amass enum -passive -d DOMAIN -silent
   curl -s "https://crt.sh/?q=%25.DOMAIN&output=json" | jq -r '.[].name_value' | sort -u
   curl -s "https://api.certspotter.com/v1/issuances?domain=DOMAIN&include_subdomains=true&expand=dns_names" | jq

Reverse DNS / IP attribution:
   dig -x IP +short
   curl -s "https://api.hackertarget.com/reverseiplookup/?q=IP" | head

Wayback / archive recovery:
   curl -s "http://web.archive.org/cdx/search/cdx?url=DOMAIN&output=json&limit=200" | jq
   waybackurls DOMAIN
   gau DOMAIN

Tech fingerprinting (passive, no auth):
   curl -sI https://DOMAIN | head -30
   webanalyze -host DOMAIN -silent

Cert SAN sweep (find sister domains via shared certs):
   echo | openssl s_client -connect DOMAIN:443 -servername DOMAIN 2>/dev/null \
     | openssl x509 -noout -text | grep -E 'DNS:|CN ='

REFUSED:
  - Active port scanning (nmap, masscan) of third-party infra without
    bug-bounty scope file
  - Vulnerability scanning (nikto, nuclei) of third-party
  - Authenticated API access to anything (Shodan/Censys are OK if
    operator provides key, but not Zeus default)"""

KB[6] = r"""
S6 ARCHIVE / WAYBACK RECOVERY:
Public-source preservation.  When something disappears from the live
web, archive.org usually has it.

Wayback Machine (CDX API):
   # All snapshots:
   curl -s "http://web.archive.org/cdx/search/cdx?url=URL&output=json&limit=1000"
   # Just unique pages on a domain:
   curl -s "http://web.archive.org/cdx/search/cdx?url=DOMAIN/*&output=text&fl=original&collapse=urlkey"

waybackurls — fast harvester (Go):  echo DOMAIN | waybackurls
gau — Get All URLs (also Go):       echo DOMAIN | gau --threads 5

Specific snapshot fetch:
   curl -s "http://web.archive.org/web/TIMESTAMP/URL"
       TIMESTAMP format: YYYYMMDDHHMMSS, e.g. 20200115083000

archive.today (sister archive, sometimes has what wayback doesn't):
   curl -s "https://archive.org/wayback/available?url=URL&timestamp=YYYYMMDD"

Google cache (last-resort, ToS-borderline):
   curl -s "https://webcache.googleusercontent.com/search?q=cache:URL"
       Google deprecated public cache in 2024 — usually 404 now.

DELETED CONTENT RECOVERY (legitimate self-OSINT use):
   - Find every wayback snapshot of YOUR own old site:
     waybackurls yourdomain.com | sort -u
   - Reddit removed comments via pushshift mirror:
     curl -s "https://api.pullpush.io/reddit/search/comment/?author=USER"
   - Twitter/X deleted via politwoops (politicians only)

PIVOT RULES:
  Old wayback snapshot mentions an email/handle not in current site →
     queue the new identifier for username-pivot workflow.
  Old subdomain in wayback URLs not in current DNS → queue for
     orphaned-subdomain check (legitimate DNS query, not scan)."""

KB[7] = r"""
S7 IMAGE METADATA + EXIF:
Zeus is free-only so reverse-image search (TinEye/Yandex/Google Lens)
is largely off the menu.  EXIF is local and free.

Local extraction:
   exiftool IMG.jpg
   exiftool -gps:all -datetimeoriginal -make -model -software IMG.jpg
   exiftool -j IMG.jpg | jq

Strip-and-show common fields:
   exiftool -GPSLatitude -GPSLongitude -DateTimeOriginal -Make -Model \
            -Software -CreatorTool IMG.jpg

GPS sanity:
   GPS coordinates in EXIF reveal where the photo was taken.  RECORD
   the coords — DO NOT auto-resolve them to a street address.  Coords
   are public OSINT; address resolution turns it into geolocation
   doxxing.  Operator can paste the coords into a map themselves.

Camera serial-number cluster:
   serial=$(exiftool -SerialNumber IMG.jpg -s3)
   # Public databases of "photos shot with same camera serial" exist
   # via Flickr search — legit pivot for stolen-camera recovery, but
   # also abusable.  Zeus surfaces the serial as a finding without
   # auto-querying camera-fingerprint sites.

Perceptual hash (for "same image elsewhere" comparisons OPERATOR
makes manually):
   python3 -c "import imagehash, PIL.Image; print(imagehash.phash(PIL.Image.open('IMG.jpg')))"

REFUSED:
  - Face matching against any face-recognition service (PimEyes,
    FaceCheck.ID etc) — these aggregate public photos but are
    privacy-hostile and stalker-favored.
  - Reverse-image search via paid APIs.
  - Auto-resolution of GPS to a postal address."""

KB[8] = r"""
S8 GITHUB OSINT (free, with optional GITHUB_TOKEN env var):
GitHub is the single richest free OSINT source for technical people.
Public profiles, commit history, and accidentally-exposed secrets all
live here.

Setup:
   export GITHUB_TOKEN=ghp_xxx     # free PAT, scopes: read:user, public_repo
   AUTH="-H 'Authorization: Bearer $GITHUB_TOKEN'"

User profile:
   curl -s $AUTH "https://api.github.com/users/USER" | jq
   curl -s $AUTH "https://api.github.com/users/USER/events/public" | jq
       Reveals what they push, what time of day, what timezone behavior.

SSH key fingerprints (cross-correlate to identity):
   curl -s "https://github.com/USER.keys"
   curl -s "https://api.github.com/users/USER/keys" | jq

PGP keys:
   curl -s "https://github.com/USER.gpg"

Email leak via commits (no token needed):
   curl -s "https://api.github.com/users/USER/events/public" \
     | jq -r '.[].payload.commits[]?.author.email' | sort -u

Commit search across all of GitHub:
   curl -s $AUTH "https://api.github.com/search/commits?q=author-email:EMAIL" \
        -H "Accept: application/vnd.github.cloak-preview" | jq
   gh search commits --author-email EMAIL --json repository

Org enumeration:
   curl -s $AUTH "https://api.github.com/orgs/ORG/members" | jq
   curl -s $AUTH "https://api.github.com/orgs/ORG/repos?per_page=100" | jq

SECRETS DETECTION (own-asset only — finds your own leaked keys):
   gitleaks detect --source=PATH --no-banner --redact
   trufflehog filesystem PATH --only-verified

GitHub dorking (search across public code):
   gh search code "author:USER aws_secret_access_key"
   curl -s $AUTH "https://api.github.com/search/code?q=org:ORG+filename:.env"

REFUSED:
  - Searching for OTHER people's secrets to use them
  - Cloning private repos via stolen tokens
  - Bypassing GitHub's rate limits via account rotation"""

KB[9] = r"""
S9 PUBLIC BLOCKCHAIN OSINT:
All transactions on public blockchains are open.  Address-clustering
and tx-history is fully legal OSINT — chain analytics is a real
profession (Chainalysis, Elliptic, TRM Labs).  Free tier covers most.

Bitcoin (no key required):
   curl -s "https://blockchain.info/rawaddr/ADDR?limit=50" | jq
   curl -s "https://blockchair.com/api/btc/dashboards/address/ADDR" | jq
   curl -s "https://mempool.space/api/address/ADDR" | jq
   curl -s "https://mempool.space/api/address/ADDR/txs" | jq

Ethereum + ERC-20 (etherscan free tier — gracefully skips without key):
   curl -s "https://api.etherscan.io/api?module=account&action=balance&address=ADDR&apikey=$ETHERSCAN_KEY"
   curl -s "https://api.etherscan.io/api?module=account&action=txlist&address=ADDR&apikey=$ETHERSCAN_KEY"

Free Ethereum (no key needed):
   curl -s "https://api.blockchair.com/ethereum/dashboards/address/ADDR" | jq
   curl -s "https://api.ethplorer.io/getAddressInfo/ADDR?apiKey=freekey" | jq

Solana (free):
   curl -s "https://api.blockchair.com/solana/raw/account/ADDR" | jq

Address clustering / counterparty analysis (heuristic, free):
   - Pull all txs for the address
   - Group counterparties by frequency
   - Flag known exchange / mixer / sanctions addresses

PIVOT RULES:
  ENS reverse: curl -s "https://api.ensideas.com/ens/resolve/ADDR" | jq
     ENS name → social handle pivot (often same handle across services)
  Lens / Farcaster handle pivots from address (free public APIs)

REFUSED:
  - Linking an address to a real-world identity beyond what the operator
    has provided as a seed
  - De-anonymising mixer outputs (technically OSINT but operator-intent
    matters; Zeus refuses unless lane = threat-actor-tracking with a
    declared malicious-actor seed)"""

KB[10] = r"""
S10 GOOGLE / GITHUB DORKING (curated, safe patterns only):
Dorks find indexed-but-unintended public exposure.  Used for
self-OSINT (find YOUR own leaks) and authorized bug bounty.

Self-OSINT dorks (replace TARGET with your domain/email/handle):

   site:github.com TARGET filename:.env
   site:github.com TARGET extension:pem
   site:github.com TARGET BEGIN RSA PRIVATE KEY
   site:pastebin.com TARGET
   site:s3.amazonaws.com TARGET
   site:trello.com inurl:b/ TARGET
   intext:TARGET filetype:log
   intext:"TARGET@" filetype:xls OR filetype:xlsx OR filetype:csv

Domain-asset exposure dorks:
   site:DOMAIN ext:php inurl:?
   site:DOMAIN inurl:admin
   site:DOMAIN ext:bak OR ext:old OR ext:backup
   site:DOMAIN intitle:"index of"
   inurl:DOMAIN inurl:.git/config

Personal-info exposure dorks (self-OSINT only):
   "FULL NAME" "@gmail.com"          # email leakage
   "FULL NAME" "DOB" OR "born"
   "FULL NAME" CITY filetype:pdf

Curated GitHub dorks (legitimate self-leak detection):
   filename:.env DB_PASSWORD
   filename:credentials aws_secret
   filename:.npmrc _authToken
   extension:json google_api
   "BEGIN RSA PRIVATE KEY"

EXECUTION (free, ToS-aware):
   Manual via Google search UI — Zeus prints the dork strings, operator
   pastes them.  Programmatic Google Search via SerpAPI etc is paid,
   so Zeus emits the dork list as a finding instead of running it.

   GitHub dorks ARE programmatic (free with GITHUB_TOKEN):
     gh search code "DORK_STRING"
     curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
       "https://api.github.com/search/code?q=DORK"

REFUSED:
  - Dorks that target a third party with stalker intent
  - Dorks designed to find live credentials of others
  - Bulk dork lists from "OSINT framework" sites without curation"""

KB[11] = r"""
S11 DECISION TREES — when a branch is dry, pivot to:

EMAIL has no holehe hits → try:
  - PGP keyserver
  - Gravatar md5
  - GitHub commit search
  - Wayback search of email-as-string on whole web

USERNAME has no sherlock/maigret hits → try:
  - Variant generation: USER, USER1, _USER_, USER_, the.USER
  - GitHub direct: https://github.com/USER, /USER.keys, /USER.gpg
  - Reddit JSON: /user/USER/about.json
  - Keybase: keys.openpgp.org by-username

DOMAIN has no subfinder hits → try:
  - crt.sh exhaustive
  - certspotter
  - wayback CDX with collapse=urlkey
  - DNS history via securitytrails (paid — skip)
  - ASN lookup → reverse via hackertarget free API

PHONE phoneinfoga unhelpful → already at the wall.  Free phone OSINT
is limited.  Record what we have and stop — don't waste turns.

CRYPTO ADDRESS empty / dust-only → check sister chains:
  - Same address on BTC, BCH, BSV (legacy)
  - ETH address often appears on Polygon / Arbitrum / Optimism / Base
  - ENS reverse may reveal handle

When the LLM can't find a next step, emit WORKFLOW_COMPLETE — Zeus
moves on to the next OTT node."""

KB[12] = r"""
S12 LANE DISCIPLINE & REFUSAL TRIGGERS:
Lane was declared at session start.  Every command must be consistent
with that lane's allowed scope.

self-osint:
  ✓ Operator's own identifiers, email, handles, photos
  ✗ Third-party PII, even if "they're a friend who said it's OK"
  → If LLM proposes pivoting to a third party, REFUSE and log

threat-actor:
  ✓ Handles, infra, malware family, threat-feed source
  ✗ Real names of attackers (handles only)
  ✗ Doxxing-flavored attribution beyond infra

journalism:
  ✓ Public-records lookups on declared subject
  ✓ Cross-platform handle tracking of public figures
  ✗ Family members of subject (unless public co-implicated)
  ✗ Home address resolution

due-diligence:
  ✓ Companies, domains, executives' public business profiles
  ✓ SEC EDGAR, OpenCorporates, Companies House
  ✗ Personal life of executives beyond business context

bug-bounty:
  ✓ Targets in declared scope (subdomains of in-scope domain)
  ✗ Out-of-scope assets, employees, social engineering data

training:
  ✓ Known-test-targets (HTB OSINT challenges, TryHackMe, CTF)
  ✗ Live people, live infrastructure

UNIVERSAL HARD REFUSALS (any lane):
  ✗ Real-time location of any human
  ✗ Home address of any human
  ✗ Stalkerware aggregators (Spokeo, BeenVerified, etc.)
  ✗ Credential-dump access (HIBP yes/no is OK; combolists are not)
  ✗ Voter rolls
  ✗ Any minor as subject
  ✗ Domestic-partner targeting language ("ex", "track them")
  ✗ Anything matching OSINT_REFUSE_PATTERNS

When in doubt: refuse, log reason, move on."""

KB[13] = r"""
S13 REPORTING HYGIENE:
Final report is printed to terminal at session end.  No disk write.

Structure:
  Header        — subject summary, lane, duration, identifier inventory
  Findings      — grouped by OSINT category (SOCIAL_PRESENCE,
                  DOMAIN_FOOTPRINT, etc), each with provenance
  Pivots        — derived identifiers (new emails, handles, domains
                  surfaced during the run)
  Coverage      — which platforms were checked, hit/miss table
  Confidence    — per finding: high (multi-source) / medium (single
                  source confirmed) / low (single source, unverified)
  Manual TODO   — things Zeus surfaced but couldn't auto-verify
  Tooling       — list of tools that ran, with version markers
  Disclaimer    — "all findings are public OSINT, lane was X, retained
                  in RAM only and discarded on exit"

EVERY finding records:
  - value (the actual datum)
  - ftype (handle / email / domain / etc.)
  - source_cmd (exact shell command that produced it)
  - osint_category (SOCIAL_PRESENCE etc.)
  - confidence (high/medium/low)
  - timestamp (when discovered)

NO finding is reported without source_cmd.  No AI hallucinations enter
the report — only regex-extracted matches from real subprocess output.

DROP rules:
  - Drop low-confidence findings if not corroborated within 5 turns
  - Drop findings that exactly match the seed identifier (the operator
    already knows their own email; reporting it back is noise)
  - De-duplicate aggressively (case-insensitive for handles/domains)"""

KB[14] = r"""
S14 PUBLIC-RECORDS ETHICS (free / legal sources):
Public records are public, but not all "publicly accessible" data is
ethical to aggregate.  Zeus errs conservative.

OK — zero friction:
  - SEC EDGAR (US public company filings)
  - Companies House (UK)
  - OpenCorporates (multi-jurisdiction company filings)
  - OFAC SDN list (US sanctions)
  - UK HMT consolidated sanctions list
  - WHOIS (now mostly redacted post-GDPR)
  - DNS / cert transparency
  - Court PACER (paid per-page; skip default)

OK with operator confirmation (lane-dependent):
  - LinkedIn public profiles (within ToS via search engines, not
    authenticated scraping)
  - Mastodon / Bluesky / Lemmy public posts
  - Personal blogs / GitHub READMEs

REFUSED — even though "publicly accessible":
  - Stalkerware-style people-search aggregators (see S12)
  - Scraped voter rolls
  - Doxbin and similar dump sites
  - Combolists / credential dumps
  - Telegram-channel personal-data leaks
  - Court records of minors

Jurisdictional reminder: GDPR (EU/UK), PIPEDA (Canada), CCPA
(California), and POPIA (South Africa) all restrict aggregation of
personal data even from public sources.  Zeus can't enforce
jurisdiction — the operator's responsibility — but the refuse list
above keeps the worst pitfalls off-limits."""

KB[15] = r"""
S15 OSINT METHODOLOGY (loop structure):
Standard run after intake:

1. INTAKE — operator declares lane, subject type, every identifier
   they have.  Zeus doesn't proceed without this.

2. ENUMERATE — for each declared identifier, queue the appropriate
   specialist:
     email   → Postman
     handle  → Socialite
     phone   → Caller
     domain  → Registrar
     image   → Cartographer
     crypto  → Ledger
     name    → Strategist (decides which tools by lane)

3. PIVOT — every new identifier surfaced gets re-queued.  Pivots stop
   at depth 2 by default (avoid combinatorial explosion).

4. CORROBORATE — each finding is checked against a second source
   where possible.  Single-source findings flagged "medium confidence."

5. ARCHIVE — every URL in findings gets a wayback CDX query so we
   record proof-of-existence.

6. REPORT — Reporter consolidates, dedupes, groups, prints.

7. PURGE — on exit: rm -rf /tmp/zeus_<pid>/, clear in-RAM state.

PIVOT DEPTH GUARDRAIL:
  depth 0 — operator's seed identifiers
  depth 1 — direct pivots from seeds (sherlock hits → handles)
  depth 2 — pivots-of-pivots (handle → email in profile bio)
  depth 3+ — REFUSED unless lane = threat-actor and operator has
             explicitly raised the depth cap

HARD CAPS:
  MAX_AUTO_TURNS = 50 turns
  MAX_WALL_CLOCK_SECONDS = 900 (15 min)
  Per-tool timeout enforced (sherlock 5min, holehe 3min, etc.)"""

KB[16] = r"""
S16 IOC PIVOTING (threat-actor lane primarily):
When the subject is a threat actor (not a person), pivots run on
infrastructure rather than identity.

Domain → IP:
   dig DOMAIN +short
   curl -s "https://api.hackertarget.com/dnslookup/?q=DOMAIN"

IP → other domains hosted there:
   curl -s "https://api.hackertarget.com/reverseiplookup/?q=IP"

Hash IOCs:
   curl -s "https://mb-api.abuse.ch/api/v1/" --data 'query=get_info&hash=HASH'
   curl -s "https://urlhaus-api.abuse.ch/v1/payload/" --data "sha256_hash=HASH"

URL IOCs:
   curl -s "https://urlhaus-api.abuse.ch/v1/url/" --data "url=URL"

Threat-feed enrichment:
   curl -s "https://otx.alienvault.com/api/v1/indicators/IPv4/IP/general"
   curl -s "https://otx.alienvault.com/api/v1/indicators/domain/DOMAIN/general"
   curl -s "https://threatfox-api.abuse.ch/api/v1/" --data '{"query":"search_ioc","search_term":"IOC"}'

C2 attribution:
   - DNS history of suspect domain (shows when it changed IPs)
   - Cert SAN sweep (reveals sister C2 domains sharing a cert)
   - WHOIS history (registrant patterns across actor's domains)
   - Wayback CDX (catches landing pages before takedown)

Malware family pivots (operator declares family at intake):
   Look up known IOCs on:
     - VirusTotal community submissions (free public results, no key)
     - MalwareBazaar by tag/family
     - Bazaar yara_rule_id endpoint
     - URLhaus by tag

ALL endpoints above are FREE public threat-intel feeds.  Several have
free API keys for higher rate limits — Zeus reads them from env vars
(OTX_KEY, ABUSEIPDB_KEY) and gracefully degrades when missing."""

WORKFLOW_KB_MAP = {
    "1":  [1, 2, 3, 5, 8, 10, 13, 15],   # Self-OSINT Footprint
    "2":  [1, 2, 8, 11, 13, 15],         # Username Pivot
    "3":  [1, 3, 8, 11, 13, 15],         # Email Exposure
    "4":  [1, 4, 11, 13, 15],            # Phone Triage
    "5":  [1, 5, 6, 11, 13, 15],         # Domain Due Diligence
    "6":  [1, 12, 16, 13, 15],           # Threat-Actor
    "7":  [1, 5, 8, 10, 13, 15],         # Bug-Bounty Recon
    "8":  [1, 9, 13, 15],                # Crypto Trace
    "9":  [1, 7, 13, 15],                # Image Metadata
    "10": [1, 6, 13, 15],                # Wayback Sweep
    "11": [1, 5, 14, 13, 15],            # Company Due Diligence
    "12": [1, 10, 6, 13, 15],            # Document Leakage
}

KEYWORD_KB_MAP = {
    "username|handle|sherlock|maigret|whatsmyname|socialscan": [2],
    "email|mail|holehe|gravatar|mx|spf|dmarc": [3],
    "phone|number|phoneinfoga|carrier|voip": [4],
    "domain|whois|dns|subdomain|crt\\.sh|certificate|asn|subfinder|amass": [5],
    "wayback|archive|gau|waybackurls|cached|history": [6],
    "image|exif|metadata|gps|camera|jpg|jpeg|png": [7],
    "github|gist|repo|commit|ssh.key|gpg.key": [8],
    "blockchain|crypto|bitcoin|btc|ethereum|eth|wallet|address": [9],
    "dork|leak|exposed|secret|env\\b|config|api.key": [10],
    "lane|self.osint|threat.actor|journalism|due.diligence|bug.bounty": [12],
    "report|coverage|methodology|disclaimer": [13],
    "company|corporate|sec|edgar|opencorporates|filing": [14],
    "ioc|otx|threatfox|urlhaus|abuse\\.ch|shodan|ipinfo": [16],
}


def get_kb_sections(workflow_key: Optional[str] = None,
                    prompt_text: str = "",
                    agent_role: str = "") -> str:
    """Return only the KB sections relevant to this workflow / agent / prompt."""
    section_nums = {1}  # mindset always

    if workflow_key and workflow_key in WORKFLOW_KB_MAP:
        section_nums.update(WORKFLOW_KB_MAP[workflow_key])

    # Agent-role-driven KB selection
    role_map = {
        "intake":             [1, 11, 12, 13, 15],
        "socialite":          [2, 8, 11, 13],
        "postman":            [3, 8, 11, 13],
        "caller":             [4, 11, 13],
        "registrar":          [5, 6, 11, 13, 16],
        "cartographer":       [7, 11, 13],
        "archivist":          [6, 11, 13],
        "dorker":             [10, 8, 11, 13],
        "ledger":             [9, 11, 13],
        "reporter":           [13, 15],
        "strategist":         [1, 12, 15],
    }
    if agent_role in role_map:
        section_nums.update(role_map[agent_role])

    if prompt_text and len(section_nums) <= 2:
        lower = prompt_text.lower()
        for pattern, nums in KEYWORD_KB_MAP.items():
            if re.search(pattern, lower):
                section_nums.update(nums)

    if len(section_nums) == 1:
        section_nums.update([2, 14])

    parts = []
    for num in sorted(section_nums):
        if num in KB:
            parts.append(KB[num])
    return "\n\n".join(parts)


# ═════════════════════════════════════════════════════════════════════
# AGENT SPECIFICATIONS
#
# Each agent is a specialist system-prompt fragment.  Zeus's
# dispatcher picks one based on the current PTT node's phase.
# Picking a specialist is NOT a separate LLM call — the dispatcher
# is deterministic, so this is "multi-agent" in design without paying
# the rate-limit cost of multi-agent at runtime.
# ═════════════════════════════════════════════════════════════════════

AGENT_SPECS = {

    "strategist": {
        "name": "STRATEGIST",
        "icon": "♛",
        "color": "33",  # gold
        "persona": (
            "You are Zeus' Strategist agent.  You are the ROUTER, not "
            "an executor.  Your only job is to read the OSINT Task "
            "Tree (OTT) and pick the right specialist to run next.  "
            "You NEVER call tools, you NEVER write shell commands, "
            "you NEVER do OSINT yourself.  You delegate.  Every turn."
        ),
        "extra_rules": (
            "STRICT OUTPUT FORMAT — exactly these tags, nothing else:\n"
            "[THOUGHT]<one short paragraph: which specialist fits the "
            "next OTT node, and why>[/THOUGHT]\n"
            "[HANDOFF]<one of: intake, socialite, postman, caller, "
            "registrar, cartographer, archivist, dorker, ledger, "
            "reporter>[/HANDOFF]\n"
            "[CONF]<green|yellow|red>[/CONF]\n"
            "\n"
            "FORBIDDEN: [CMD], [TOOL], [ARGS] — never use these.  "
            "If you write a shell command, it will be discarded and "
            "you will be re-prompted.\n"
            "\n"
            "ROUTING GUIDE:\n"
            "  handles / usernames                → socialite\n"
            "  email addresses                    → postman\n"
            "  phone numbers                      → caller\n"
            "  domains / subdomains / DNS         → registrar\n"
            "  image files (EXIF / GPS)           → cartographer\n"
            "  wayback / archive recovery         → archivist\n"
            "  Google / GitHub dorks              → dorker\n"
            "  BTC / ETH / SOL addresses          → ledger\n"
            "  consolidate findings into report   → reporter\n"
            "  validate intake / lane policy      → intake\n"
            "\n"
            "TERMINATION: when every identifier branch has been "
            "exhausted by its specialist (status=done or dead_end), "
            "emit [CMD]WORKFLOW_COMPLETE[/CMD] alone — that is the "
            "ONLY time you may write [CMD]."
        ),
    },

    "intake": {
        "name": "INTAKE SPECIALIST",
        "icon": "🪪",
        "color": "97",
        "persona": (
            "You are Zeus' Intake specialist.  You receive the raw "
            "operator intake form and translate it into OTT branches.  "
            "You categorise each identifier (handle / email / phone / "
            "domain / image / crypto / key-fingerprint), validate it "
            "(e.g., email format, domain syntax), and queue the "
            "appropriate first-wave commands.  You also enforce the "
            "lane gate — if the lane is self-osint and the identifiers "
            "obviously belong to a public figure, you flag that."
        ),
        "extra_rules": (
            "Output: a series of [TOOL] / [CMD] decisions plus [HANDOFF] "
            "to the right specialist for each branch.  NEVER output a "
            "command that violates OSINT_REFUSE_PATTERNS.  When you "
            "have categorised everything and queued first-wave "
            "commands, emit WORKFLOW_COMPLETE so the strategist "
            "advances."
        ),
    },

    "socialite": {
        "name": "SOCIAL PRESENCE",
        "icon": "👤",
        "color": "35",  # magenta
        "persona": (
            "You are Zeus' Social Presence specialist.  Username / "
            "handle pivoting across hundreds of public platforms.  "
            "Sherlock, Maigret, Socialscan, WhatsMyName, plus public "
            "API endpoints (GitHub, Reddit, Mastodon, Bluesky)."
        ),
        "extra_rules": (
            "Always use --print-found / --only-used flags so output "
            "is just hits.  Verify hits via the URL itself (some "
            "platforms return 200 with soft-404).  When you find a "
            "GitHub handle, immediately follow up with .keys / .gpg / "
            "/users/USER/repos pulls.  When you find a Mastodon "
            "handle, lookup via /api/v1/accounts/lookup.  Pivot every "
            "real-name find back to email/domain searches via "
            "[HANDOFF]postman[/HANDOFF] or [HANDOFF]registrar[/HANDOFF]."
        ),
    },

    "postman": {
        "name": "EMAIL TRIAGE",
        "icon": "📮",
        "color": "36",  # cyan
        "persona": (
            "You are Zeus' Email Triage specialist.  Public email "
            "OSINT — holehe (which sites is this email registered on), "
            "MX/SPF/DMARC, gravatar, GitHub email-search.  HIBP is "
            "PAID and excluded from Zeus.  Your job is to enumerate "
            "active accounts and email-provider fingerprint."
        ),
        "extra_rules": (
            "Never run anything that takes a password as input.  Never "
            "query DeHashed/Snusbase/WeLeakInfo/combolists for actual "
            "credentials.  When you find an email's domain is custom, "
            "[HANDOFF]registrar[/HANDOFF] for full domain footprint.  "
            "When you find sites via holehe, [HANDOFF]socialite[/HANDOFF] "
            "to look for the username on those same sites."
        ),
    },

    "caller": {
        "name": "PHONE OSINT",
        "icon": "📞",
        "color": "32",  # green
        "persona": (
            "You are Zeus' Phone OSINT specialist.  phoneinfoga + "
            "free number-validation APIs.  You return: country, "
            "carrier, line type, VoIP yes/no.  That is the ceiling "
            "for free legal phone OSINT."
        ),
        "extra_rules": (
            "Never attempt SS7 / IMSI / cell-tower / real-time location.  "
            "If the phone belongs to a registered business (hint from "
            "the operator's intake notes), pivot via OpenCorporates "
            "search-by-phone — that's legit public records.  Otherwise "
            "stop at carrier+country+VoIP-detection."
        ),
    },

    "registrar": {
        "name": "DOMAIN FOOTPRINT",
        "icon": "🌐",
        "color": "34",  # blue
        "persona": (
            "You are Zeus' Domain Footprint specialist.  WHOIS, DNS, "
            "subdomain enumeration (passive only — subfinder, amass "
            "passive, assetfinder, findomain), certificate transparency "
            "(crt.sh), reverse-IP, ASN lookup."
        ),
        "extra_rules": (
            "Passive only.  No active subdomain bruteforcing — that's "
            "noisy and ToS-hostile.  Always start with crt.sh + "
            "subfinder; only escalate to amass if the first wave is "
            "thin.  When you find new subdomains, [HANDOFF]archivist"
            "[/HANDOFF] to wayback-check them.  When you find an "
            "interesting endpoint, [HANDOFF]dorker[/HANDOFF] for "
            "leaked-config search on it."
        ),
    },

    "cartographer": {
        "name": "METADATA",
        "icon": "🗺",
        "color": "94",
        "persona": (
            "You are Zeus' Metadata specialist.  EXIF extraction from "
            "image files the operator provided.  No reverse-image "
            "search — those are paid APIs.  Your output is GPS, camera "
            "model, software, dates, copyright tags."
        ),
        "extra_rules": (
            "Use exiftool only.  Strip ANSI before parsing.  When GPS "
            "coordinates are present, format as 'lat, lon' but NEVER "
            "geocode to a street address.  Print 'GPS present at "
            "approx CITY, COUNTRY' if you can resolve via a free "
            "reverse-geocode (Nominatim free tier — but only if the "
            "operator's lane is self-osint or threat-actor)."
        ),
    },

    "archivist": {
        "name": "ARCHIVE HISTORY",
        "icon": "📚",
        "color": "90",
        "persona": (
            "You are Zeus' Archive specialist.  waybackurls, gau, "
            "archive.org direct queries, Google cache.  Your job is "
            "to find historical / deleted / cached content for a "
            "domain or username."
        ),
        "extra_rules": (
            "Always sort -u and head -50 your output — wayback returns "
            "thousands of URLs.  Look for: old endpoints not in current "
            "sitemap, removed PR pages, deleted profile pages, old "
            "subdomains.  Cite the exact archive snapshot URL for any "
            "finding."
        ),
    },

    "dorker": {
        "name": "DORK HUNTER",
        "icon": "🔎",
        "color": "31",  # red
        "persona": (
            "You are Zeus' Dork Hunter.  Curated Google + GitHub dork "
            "patterns to find leaked secrets, exposed configs, and "
            "documents.  Self-OSINT focused (find your own leaks)."
        ),
        "extra_rules": (
            "Stick to the curated whitelist of dork patterns: env "
            "files, log files, private keys, AWS/GCP keys, wp-config, "
            ".git/config exposure.  Never craft dorks targeting "
            "individuals' personal data.  Never run mass-enum dorks "
            "across an entire city or country."
        ),
    },

    "ledger": {
        "name": "BLOCKCHAIN",
        "icon": "💰",
        "color": "33",  # gold
        "persona": (
            "You are Zeus' Blockchain specialist.  Public chain "
            "analysis — Bitcoin, Ethereum, Litecoin, Dogecoin via free "
            "block explorers (blockchain.info, blockstream.info, "
            "blockchair, etherscan).  Address clustering heuristics."
        ),
        "extra_rules": (
            "Every chain query is fully legal — public ledger.  When "
            "an address has a public attribution (forum post, tweet, "
            "blog), include that in the finding.  Don't guess "
            "attributions.  Cluster via common-input ownership only "
            "when confidence is high."
        ),
    },

    "reporter": {
        "name": "REPORTER",
        "icon": "📋",
        "color": "97",
        "persona": (
            "You are Zeus' Reporter agent.  At session end you "
            "consolidate every finding into a clean terminal-printed "
            "report.  Operator copies what they want.  Nothing is "
            "saved to disk."
        ),
        "extra_rules": (
            "Output structure: Subject Summary, Findings by OSINT "
            "category (SOCIAL_PRESENCE, DOMAIN_FOOTPRINT, PUBLIC_RECORDS, "
            "INFRASTRUCTURE, METADATA, REPUTATION, ARCHIVE_HISTORY, "
            "CRYPTO_LEDGER), Coverage Matrix, Suggested Manual Follow-ups, "
            "Methodology, Legality Disclaimer.  Cite the exact "
            "tool+query for every finding."
        ),
    },
}


# Phase → preferred agent role mapping (used by deterministic dispatcher)
PHASE_TO_AGENT = {
    # Intake
    "intake":           "intake",
    "initial":          "intake",
    "categorise":       "intake",
    # Strategy
    "strategy":         "strategist",
    "route":            "strategist",
    # Social
    "social":           "socialite",
    "username":         "socialite",
    "handle":           "socialite",
    "profile":          "socialite",
    # Email
    "email":            "postman",
    "mail":             "postman",
    # Phone
    "phone":            "caller",
    "phone_osint":      "caller",
    # Domain
    "domain":           "registrar",
    "subdomain":        "registrar",
    "dns":              "registrar",
    "whois":            "registrar",
    "infrastructure":   "registrar",
    "cert":             "registrar",
    # Image
    "image":            "cartographer",
    "exif":             "cartographer",
    "metadata":         "cartographer",
    "geo":              "cartographer",
    # Archive
    "archive":          "archivist",
    "wayback":          "archivist",
    "history":          "archivist",
    # Dorking
    "dork":             "dorker",
    "dorking":          "dorker",
    "leak_hunt":        "dorker",
    "secret_hunt":      "dorker",
    # Crypto
    "crypto":           "ledger",
    "blockchain":       "ledger",
    "wallet":           "ledger",
    # Reporting
    "report":           "reporter",
    "summary":          "reporter",
}


# ═════════════════════════════════════════════════════════════════════
# PENTESTING TASK TREE (PTT)
#
# Hierarchical state.  Each node tracks status / confidence / findings /
# attempts / tool / parent / children.  Replaces v6.1's flat findings
# dict.  The whole tree gets serialised to natural language for system
# prompts so the LLM sees the entire engagement state every turn.
# ═════════════════════════════════════════════════════════════════════

@dataclass
class Finding:
    """Source-tagged finding.  Phantoms can't sneak in because every
    finding records the exact subprocess command that produced it.
    v7.1: now carries optional MITRE ATT&CK technique tag."""
    fid:       int
    value:     str
    ftype:     str               # ip, port, user, hash, cred, cve, ...
    source_cmd: str              # the shell command that produced this
    node_id:    str              # which PTT node was active
    verified:   bool = False
    notes:      str = ""
    timestamp:  str = ""
    attack_id:  str = ""         # v7.1 — MITRE ATT&CK technique ID
    attack_name: str = ""        # v7.1 — human-readable name
    attack_tactic: str = ""      # v7.1 — tactic category

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PTTNode:
    nid:        str               # dotted id, e.g. "1.2.3"
    title:      str
    phase:      str               # recon, enum, web, ad, linux_post, ...
    status:     str = "todo"      # todo, in_progress, done, dead_end, skipped
    confidence: str = "green"     # green, yellow, red
    parent_id:  Optional[str] = None
    children:   List[str] = field(default_factory=list)
    findings:   List[int] = field(default_factory=list)
    attempts:   int = 0
    last_cmd:   str = ""
    notes:      str = ""

    @property
    def depth(self) -> int:
        return self.nid.count(".")


class PTT:
    """OSINT Task Tree (DTT).

    Provides:
      - hierarchical task state
      - findings storage with source-tagging
      - natural-language serialiser for LLM prompts
      - terminal renderer for the REPL
      - dead-end detection + sibling lookup for backtracking
    """

    STATUS_GLYPH = {
        "todo":         "○",
        "in_progress":  "◐",
        "done":         "●",
        "dead_end":     "✗",
        "skipped":      "─",
    }
    CONF_COLOR = {
        "green":  "32",
        "yellow": "33",
        "red":    "31",
    }

    def __init__(self, goal: str = "Compromise target"):
        self.nodes: Dict[str, PTTNode] = {}
        self.findings: List[Finding] = []
        self._next_finding_id = 1
        self.root_id = "0"
        # Root node represents the overall mission
        self.nodes[self.root_id] = PTTNode(
            nid=self.root_id, title=goal, phase="root", status="in_progress"
        )

    # ─── Tree construction ─────────────────────────────────────────

    def add_node(self, parent_id: str, title: str, phase: str,
                 status: str = "todo") -> str:
        if parent_id not in self.nodes:
            raise ValueError(f"Unknown parent: {parent_id}")
        parent = self.nodes[parent_id]
        idx = len(parent.children) + 1
        nid = f"{parent_id}.{idx}" if parent_id != self.root_id else str(idx)
        node = PTTNode(nid=nid, title=title, phase=phase,
                       status=status, parent_id=parent_id)
        self.nodes[nid] = node
        parent.children.append(nid)
        return nid

    # ─── Status & status helpers ────────────────────────────────────

    def set_status(self, nid: str, status: str):
        if nid in self.nodes:
            self.nodes[nid].status = status

    def set_confidence(self, nid: str, conf: str):
        if nid in self.nodes and conf in ("green", "yellow", "red"):
            self.nodes[nid].confidence = conf

    def increment_attempts(self, nid: str):
        if nid in self.nodes:
            self.nodes[nid].attempts += 1

    def set_last_cmd(self, nid: str, cmd: str):
        if nid in self.nodes:
            self.nodes[nid].last_cmd = cmd[:200]

    # ─── Active node + frontier selection ──────────────────────────

    def find_in_progress(self) -> Optional[PTTNode]:
        for n in self.nodes.values():
            if n.status == "in_progress" and n.nid != self.root_id:
                return n
        return None

    def find_next_pending(self) -> Optional[PTTNode]:
        """Depth-first: return first todo node, preferring deeper subtrees."""
        # Sort by depth descending so deepest todos go first when their
        # parents are in_progress (we want to finish current branch).
        active = self.find_in_progress()
        if active:
            # Look at children of the active node first
            for cid in active.children:
                cn = self.nodes.get(cid)
                if cn and cn.status == "todo":
                    return cn
        # Otherwise just return any todo, shallow-first
        todos = [n for n in self.nodes.values()
                 if n.status == "todo" and n.nid != self.root_id]
        if not todos:
            return None
        todos.sort(key=lambda n: (n.depth, n.nid))
        return todos[0]

    def find_pending_siblings(self, nid: str) -> List[PTTNode]:
        n = self.nodes.get(nid)
        if not n or not n.parent_id:
            return []
        parent = self.nodes[n.parent_id]
        return [self.nodes[cid] for cid in parent.children
                if cid != nid and self.nodes[cid].status == "todo"]

    def all_done(self) -> bool:
        for n in self.nodes.values():
            if n.nid == self.root_id:
                continue
            if n.status in ("todo", "in_progress"):
                return False
        return True

    # ─── Findings ──────────────────────────────────────────────────

    def add_finding(self, value: str, ftype: str, source_cmd: str,
                    node_id: str, verified: bool = False,
                    notes: str = "") -> int:
        # de-dup by (ftype, value)
        for f in self.findings:
            if f.ftype == ftype and f.value == value:
                # Promote verification status if this run verified it
                if verified and not f.verified:
                    f.verified = True
                    f.source_cmd = source_cmd
                if node_id not in [f.node_id]:
                    pass  # keep first node that found it
                return f.fid
        fid = self._next_finding_id
        self._next_finding_id += 1
        f = Finding(fid=fid, value=value, ftype=ftype,
                    source_cmd=source_cmd, node_id=node_id,
                    verified=verified, notes=notes,
                    timestamp=datetime.datetime.now().isoformat(timespec="seconds"))
        self.findings.append(f)
        if node_id in self.nodes:
            self.nodes[node_id].findings.append(fid)
        return fid

    def get_findings_by_type(self, ftype: str,
                             only_verified: bool = False) -> List[Finding]:
        result = []
        for f in self.findings:
            if f.ftype != ftype:
                continue
            if only_verified and not f.verified:
                continue
            result.append(f)
        return result

    def get_unverified(self) -> List[Finding]:
        return [f for f in self.findings if not f.verified]

    def get_verified(self) -> List[Finding]:
        return [f for f in self.findings if f.verified]

    def drop_unverified(self):
        """Cleanup pass: remove findings that were never verified.
        Called once at report-generation time."""
        kept = [f for f in self.findings if f.verified]
        self.findings = kept

    # ─── Serialisation for LLM prompts ─────────────────────────────

    def to_natural_language(self, max_chars: int = 2000) -> str:
        """Render the tree as nested bullets for the system prompt.
        Compact form; deeper nodes get less verbose status."""
        lines = ["OSINT TASK TREE:"]
        root = self.nodes[self.root_id]
        lines.append(f"[{self.root_id}] {root.title}")
        for cid in root.children:
            self._serialise_subtree(cid, lines, indent=1)
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... [tree truncated for context]"
        return text

    def _serialise_subtree(self, nid: str, lines: List[str], indent: int):
        n = self.nodes.get(nid)
        if not n:
            return
        glyph = self.STATUS_GLYPH.get(n.status, "?")
        prefix = "  " * indent
        line = f"{prefix}{glyph} [{n.nid}] {n.title} ({n.phase}, status={n.status}"
        if n.attempts:
            line += f", attempts={n.attempts}"
        if n.findings:
            line += f", findings={len(n.findings)}"
        line += ")"
        lines.append(line)
        for cid in n.children:
            self._serialise_subtree(cid, lines, indent + 1)

    # ─── Terminal renderer (pretty print) ──────────────────────────

    def to_terminal(self) -> str:
        """Coloured tree for the REPL."""
        out = []
        root = self.nodes[self.root_id]
        out.append(f"\033[35m\033[1m  ♔ MISSION: {root.title}\033[0m")
        for i, cid in enumerate(root.children):
            is_last = (i == len(root.children) - 1)
            self._render_subtree(cid, out, prefix="  ", is_last=is_last)
        return "\n".join(out)

    def _render_subtree(self, nid: str, out: List[str],
                        prefix: str, is_last: bool):
        n = self.nodes.get(nid)
        if not n:
            return
        connector = "└─" if is_last else "├─"
        glyph = self.STATUS_GLYPH.get(n.status, "?")
        conf_color = self.CONF_COLOR.get(n.confidence, "37")

        # Color glyph by status
        status_colors = {
            "todo":        "90",
            "in_progress": "33",
            "done":        "32",
            "dead_end":    "31",
            "skipped":     "90",
        }
        gc = status_colors.get(n.status, "37")

        line = (
            f"{prefix}{connector}\033[{gc}m{glyph}\033[0m "
            f"\033[{conf_color}m[{n.nid}]\033[0m "
            f"\033[97m{n.title}\033[0m "
            f"\033[90m({n.phase})\033[0m"
        )
        if n.findings:
            line += f" \033[36m·{len(n.findings)}f\033[0m"
        if n.attempts:
            line += f" \033[90m·a{n.attempts}\033[0m"
        out.append(line)

        new_prefix = prefix + ("   " if is_last else "│  ")
        for i, cid in enumerate(n.children):
            child_last = (i == len(n.children) - 1)
            self._render_subtree(cid, out, new_prefix, child_last)

    # ─── Aggregate views (replaces v6.1 flat findings dict) ────────

    def findings_by_type_dict(self,
                              only_verified: bool = False) -> Dict[str, List[str]]:
        """Backward-compat view: legacy code expects a dict."""
        d: Dict[str, List[str]] = {}
        for f in self.findings:
            if only_verified and not f.verified:
                continue
            d.setdefault(f.ftype, []).append(f.value)
        return d


# ═════════════════════════════════════════════════════════════════════
# UTILITIES
# ═════════════════════════════════════════════════════════════════════

def get_lhost() -> str:
    try:
        r = subprocess.run(
            "hostname -I | awk '{print $1}'",
            shell=True, capture_output=True, text=True
        )
        ip = r.stdout.strip()
        if ip and ip != "127.0.0.1":
            return ip
    except Exception:
        pass
    return "127.0.0.1"


def ensure_yara_rules() -> Optional[str]:
    """Return the first YARA rule directory that exists on the system,
    or None if no ruleset is installed.  Mirrors Athena's ensure_rockyou
    pattern but for defensive rulesets.  Caller (ToolBuilder.yara_scan)
    falls back to scanning without rules if this returns None."""
    for path in YARA_RULE_PATHS:
        if os.path.isdir(path):
            return path
    return None


def cmd_exists(cmd: str) -> bool:
    try:
        r = subprocess.run(f"which {cmd} 2>/dev/null", shell=True,
                           capture_output=True, text=True)
        return bool(r.stdout.strip())
    except Exception:
        return False


def get_default_yara_rules() -> Optional[str]:
    """Find a sensible default rules file or directory.  Used when the
    LLM emits a yara_scan call without specifying `rules`."""
    return ensure_yara_rules()


def get_default_sigma_rules() -> Optional[str]:
    for path in SIGMA_RULE_PATHS:
        if os.path.isdir(path):
            return path
    return None


def install_if_missing(tool: str) -> bool:
    if cmd_exists(tool):
        return True
    try:
        print(f"\033[33m   Auto-installing {tool}...\033[0m")
        subprocess.run(
            f"sudo apt install -y {tool} 2>/dev/null",
            shell=True, capture_output=True, text=True, timeout=90
        )
        return cmd_exists(tool)
    except Exception:
        return False


def detect_sensitive_paths(output: str) -> List[str]:
    found = []
    for pattern in SENSITIVE_PATH_PATTERNS:
        if re.search(pattern, output):
            cleaned = pattern.replace('\\', '').strip('/')
            if cleaned not in found:
                found.append(cleaned)
    return found


# ─── Source-tagged finding extraction ─────────────────────────────────
#
# Critical fix from v6.1: ONLY runs against raw subprocess stdout.
# Never against AI's prose.  Every finding records the command that
# produced it.  Strict context-aware patterns prevent the "200:not"
# style phantom credentials.
# ─────────────────────────────────────────────────────────────────────

def extract_findings_from_stdout(output: str,
                                 source_cmd: str,
                                 ptt: PTT,
                                 active_node_id: str) -> int:
    """Run regex patterns over RAW subprocess stdout only.

    Returns: number of new findings added.
    """
    if not output or len(output) < 20:
        return 0

    # Strip ANSI codes — they confuse regex
    clean = re.sub(r'\033\[[0-9;]*m', '', output)
    clean = re.sub(r'\x1b\[[0-9;]*m', '', clean)

    new_count = 0

    for ftype, pattern in FINDING_PATTERNS.items():
        try:
            matches = re.findall(pattern, clean, re.IGNORECASE | re.MULTILINE)
        except re.error:
            continue
        if not matches:
            continue

        for m in matches:
            if isinstance(m, tuple):
                # Tuple from groups — pick the first non-empty
                items = [x for x in m if x and len(str(x).strip()) > 1]
            else:
                items = [m] if m else []

            for raw in items:
                val = str(raw).strip().rstrip('.,;:)\'')

                # Quick noise filter
                if len(val) < 2:
                    continue

                if ftype == "ip" and val in IP_NOISE:
                    continue

                if ftype == "domain":
                    if val.lower() in DOMAIN_NOISE:
                        continue
                    # Filter noise like "etc.local", "1.2.3.4"
                    if re.match(r'^\d+\.\d+\.\d+\.\d+$', val):
                        continue
                    if "." not in val:
                        continue
                    # ZEUS: reject filename-shaped false positives
                    # (boot.log, system.journal, lynis-report.dat).
                    # Domain must end in a recognised TLD.
                    if not _has_valid_tld(val):
                        continue
                    # Reject if any non-final segment looks like a hex
                    # blob ≥ 16 chars (journal-file pattern).
                    parts = val.split(".")
                    if any(len(p) >= 16 and re.fullmatch(r'[a-f0-9]+', p)
                           for p in parts[:-1]):
                        continue

                if ftype == "email":
                    # Email must end in a recognised TLD too —
                    # otherwise `system@HASH.journal` slips through.
                    domain_part = val.split("@", 1)[-1] if "@" in val else ""
                    if not _has_valid_tld(domain_part):
                        continue
                    # Reject if local-part or domain-part contains a
                    # hex blob ≥ 16 chars (systemd journal pattern).
                    if re.search(r'[a-f0-9]{16,}', val):
                        continue

                if ftype == "account":
                    # Drop generic placeholders that show up in prose / docs
                    if val.lower() in {"user", "username", "admin", "test",
                                       "example", "yourname"}:
                        continue
                    if len(val) < 3:
                        continue

                if ftype == "hash":
                    # Make sure this is hex-only and right length
                    if not re.fullmatch(r'[a-fA-F0-9]+', val):
                        continue
                    if len(val) not in (32, 40, 56, 64):
                        continue

                # Add to PTT (auto de-dups)
                fid_before = ptt._next_finding_id
                fid = ptt.add_finding(value=val, ftype=ftype,
                                source_cmd=source_cmd,
                                node_id=active_node_id)
                if ptt._next_finding_id > fid_before:
                    new_count += 1
                    # Auto-tag with ATT&CK technique
                    # Prefer command-based pattern, fall back to ftype-based
                    tag = attack_id_for_command(source_cmd) or attack_id_for_finding(ftype)
                    if tag and ptt.findings:
                        f_obj = ptt.findings[-1]
                        if f_obj.fid == fid:
                            f_obj.attack_id, f_obj.attack_name, f_obj.attack_tactic = tag

    # Detect critical-path writes / exposures separately
    for path in detect_sensitive_paths(clean):
        fid_before = ptt._next_finding_id
        ptt.add_finding(value=path, ftype="persistence",
                        source_cmd=source_cmd, node_id=active_node_id,
                        notes="critical path touched")
        if ptt._next_finding_id > fid_before:
            new_count += 1
            tag = attack_id_for_finding("persistence")
            if tag and ptt.findings:
                f_obj = ptt.findings[-1]
                f_obj.attack_id, f_obj.attack_name, f_obj.attack_tactic = tag

    return new_count


def auto_cve_lookup(output: str) -> str:
    """When a CVE appears in output, surface defender-relevant advisories
    rather than offensive exploit code.  Looks up the local NVD cache via
    the lynis cvelookup helper if present; otherwise just normalises the
    CVE for the operator's [THOUGHT] block."""
    cve_matches = re.findall(r'CVE-\d{4}-\d+', output, re.IGNORECASE)
    if not cve_matches:
        return ""
    seen = set()
    results = []
    for cve in cve_matches[:5]:
        cve = cve.upper()
        if cve in seen:
            continue
        seen.add(cve)
        # Defender advisory — keep it short.  Real lookup happens via
        # the AI agent issuing a curl to NVD or Vulners.
        results.append(
            f"\n\033[34m[CVE TO TRIAGE: {cve}]\033[0m\n"
            f"  Patch?    apt list --upgradable | grep -E 'security|cve' "
            f"or check vendor advisory.\n"
            f"  Detect?   sigma-cli rule search '{cve}' | "
            f"chainsaw against EVTX.\n"
            f"  Lookup:   curl -s "
            f"'https://services.nvd.nist.gov/rest/json/cves/2.0?cveId={cve}' | jq '.vulnerabilities[0].cve.descriptions[0].value'"
        )
    return "".join(results)


# (Removed in v1.1: analyze_and_suggest_exploit — Ares' defensive CVE
# triage helper.  Zeus is OSINT-only, doesn't enumerate CVEs from
# authenticated sources, so this never fired on real Zeus runs.  Call
# site in run_command was also removed.  See zeus5 commit history.)


def compress_output_for_history(output: str,
                                is_exploit_result: bool = False) -> str:
    """Aggressive compression of terminal output for AI context.
    Exploit results are kept intact (creds/shells matter)."""
    if is_exploit_result:
        return output[:MAX_OUTPUT_CHARS]

    output = re.sub(r'\033\[[0-9;]*m', '', output)
    output = re.sub(r'\x1b\[[0-9;]*m', '', output)
    lines = output.split('\n')

    junk = re.compile(r'|'.join([
        r'^Stats: ', r'^SYN Stealth Scan Timing', r'^\s*$',
        r'^Reading database', r'^Preparing to unpack',
        r'^Selecting previously', r'^Unpacking ',
        r'^Setting up ', r'^Processing triggers',
        r'^\(Reading database', r'^Get:\d', r'^Hit:\d', r'^Ign:\d',
        r'^Fetched ', r'^WARNING:.*Cannot open MAC',
        r'^Starting Nmap', r'^Nmap done:', r'^Nmap scan report',
    ]))

    cleaned, last = [], None
    for line in lines:
        line = line.rstrip()
        if junk.search(line):
            continue
        if line == last:
            continue
        if len(line) > 240:
            line = line[:240] + "..."
        cleaned.append(line)
        last = line

    result = '\n'.join(cleaned).strip()
    if len(result) > 1800:
        head = result[:800]
        tail = result[-600:]
        result = f"{head}\n[...{len(result)-1400} chars trimmed...]\n{tail}"
    return result or "(no useful output)"


# ─── Visual helpers ───────────────────────────────────────────────────

def hr(width: int = 64, char: str = "─", color: str = "90") -> str:
    return f"\033[{color}m{char * width}\033[0m"


def header_box(text: str, color: str = "35", width: int = 64) -> str:
    """v7.1 — heavier, two-line title bar that looks like a real UI panel."""
    inner = f" {text} ".center(width - 2)
    return (
        f"\033[{color}m╭{'─'*(width-2)}╮\n"
        f"│\033[1m{inner}\033[0m\033[{color}m│\n"
        f"╰{'─'*(width-2)}╯\033[0m"
    )


def panel(title: str, lines: List[str],
          color: str = "35", width: int = 66) -> str:
    """v7.1 — generic bordered panel with title bar.  Used everywhere
    we want a consistent app-like look."""
    out = []
    title_text = f" {title} "
    pad_left = (width - 2 - len(title_text)) // 2
    pad_right = width - 2 - len(title_text) - pad_left
    out.append(f"\033[{color}m╭{'─'*pad_left}\033[1m{title_text}\033[0m"
               f"\033[{color}m{'─'*pad_right}╮\033[0m")
    for ln in lines:
        # strip ANSI to compute true length
        visible = re.sub(r'\033\[[\d;]*m', '', ln)
        pad = max(0, width - 2 - len(visible))
        out.append(f"\033[{color}m│\033[0m {ln}{' ' * (pad - 1)}\033[{color}m│\033[0m")
    out.append(f"\033[{color}m╰{'─'*(width-2)}╯\033[0m")
    return "\n".join(out)


def status_line(model: str, agent: str, node: str,
                findings: int, verified: int) -> str:
    return (
        f"\033[90m[\033[97mmodel\033[90m] \033[36m{model}  "
        f"\033[90m[\033[97magent\033[90m] \033[33m{agent}  "
        f"\033[90m[\033[97mnode\033[90m] \033[97m{node}  "
        f"\033[90m[\033[97mfindings\033[90m] "
        f"\033[32m{verified}\033[90m/\033[97m{findings}\033[0m"
    )


def status_bar(target: str, agent: str, model: str,
               verified: int, unverified: int,
               techniques: int, scope_on: bool, width: int = 66) -> str:
    """v7.1 — persistent status bar shown at top of certain views.
    Like a window-chrome strip."""
    scope_pill = "\033[32m●SCOPE\033[0m" if scope_on else "\033[90m○scope\033[0m"
    target_short = (target[:14] + "…") if len(target) > 15 else target
    bar = (f"\033[97m▍\033[0m \033[36m{target_short:<15}\033[0m "
           f"\033[90m│\033[0m \033[33m{agent:<8}\033[0m "
           f"\033[90m│\033[0m \033[36m{model[:14]:<14}\033[0m "
           f"\033[90m│\033[0m \033[32m✓{verified}\033[0m\033[90m/\033[33m?{unverified}\033[0m "
           f"\033[90m│\033[0m \033[31mOSINT ×{techniques}\033[0m "
           f"\033[90m│\033[0m {scope_pill}")
    visible = re.sub(r'\033\[[\d;]*m', '', bar)
    pad = max(0, width - len(visible))
    return f"\033[100m\033[97m {bar} {' '*pad}\033[0m"


def confidence_pill(conf: str) -> str:
    """v7.1 — visually-strong confidence indicator."""
    if conf == "green":
        return "\033[42m\033[97m\033[1m  GREEN ▶ EXECUTE  \033[0m"
    if conf == "yellow":
        return "\033[43m\033[30m\033[1m  YELLOW · CAUTION  \033[0m"
    if conf == "red":
        return "\033[41m\033[97m\033[1m  RED ✕ HOLD  \033[0m"
    return f"\033[100m\033[97m  {conf.upper()}  \033[0m"


def progress_bar(current: int, total: int, width: int = 24,
                 fill: str = "█", empty: str = "░") -> str:
    """v7.1 — text progress bar."""
    if total <= 0:
        return f"\033[90m{empty * width}\033[0m"
    pct = min(1.0, current / total)
    filled = int(pct * width)
    pct_text = f"{int(pct * 100):>3}%"
    return (f"\033[32m{fill * filled}\033[90m{empty * (width - filled)}"
            f"\033[0m \033[97m{pct_text}\033[0m \033[90m({current}/{total})\033[0m")


def kbd(label: str) -> str:
    """v7.1 — keycap-style button for prompts."""
    return f"\033[100m\033[97m {label} \033[0m"


def section(title: str, color: str = "35") -> str:
    """v7.1 — minimal section header with side rules."""
    line = "─" * 4
    return (f"\033[{color}m{line}\033[0m  \033[{color}m\033[1m{title}\033[0m  "
            f"\033[{color}m{'─' * (60 - len(title))}\033[0m")


def finding_card(f: Finding) -> str:
    """One-line card for a finding in the 'findings' command.
    Shows ATT&CK technique tag if present."""
    icon_map = {
        "ip":           "🌐",
        "port":         "🔌",
        "svc":          "⚙",
        "account":      "👤",
        "hash":         "🔐",
        "cve":          "💥",
        "domain":       "🏷",
        "url":          "🔗",
        "yara_hit":     "🧬",
        "av_hit":       "🦠",
        "suspicious_proc": "⚠",
        "suricata_alert":  "🚨",
        "cron_entry":   "⏰",
        "suid":         "🛂",
        "cap_grant":    "🛂",
        "auth_fail":    "🔒",
        "sudo_use":     "👮",
        "persistence":  "📌",
        "container":    "📦",
        "email":        "📧",
        "ssh_key":      "🗝",
        "aws_key":      "☁",
        "attack_id":    "🎯",
    }
    icon = icon_map.get(f.ftype, "•")
    verified_mark = "\033[32m●\033[0m" if f.verified else "\033[90m○\033[0m"
    val_short = f.value[:50] + ("…" if len(f.value) > 50 else "")
    attack_tag = (f" \033[36m{f.attack_id}\033[0m"
                  if f.attack_id else "")
    return (
        f"  {verified_mark} {icon}  \033[97m{f.ftype:<14}\033[0m "
        f"\033[36m{val_short}\033[0m "
        f"\033[90m[{f.node_id}]\033[0m{attack_tag}"
    )


def fancy_header(text: str, color: str = "35") -> str:
    width = max(len(text) + 4, 40)
    line = "─" * width
    padded = text.center(width - 2)
    return (
        f"\033[{color}m╭{line}╮\n"
        f"│ \033[1m{padded}\033[0m\033[{color}m │\n"
        f"╰{line}╯\033[0m"
    )


# ─────────────────────────────────────────────────────────────────────
# v7.2 — boxed UI primitives
#
# Goal: every event a turn produces gets its own titled box, so the
# operator can scan a session log at a glance.  Boxes are 70 cols wide
# (most phone terminals/SSH sessions render this well).  All boxes use
# the `panel()` building block so they share a consistent look.
# ─────────────────────────────────────────────────────────────────────

BOX_W = 70


def _visible_len(s: str) -> int:
    """Length without ANSI escapes."""
    return len(re.sub(r'\033\[[\d;]*m', '', s))


def _wrap_for_box(text: str, inner_width: int) -> List[str]:
    """Wrap a paragraph for box rendering (ANSI-aware)."""
    out: List[str] = []
    for raw_line in str(text).splitlines() or [""]:
        if not raw_line.strip():
            out.append("")
            continue
        # Greedy word-wrap — doesn't account for mid-word ANSI but
        # we only call this on plain text in practice.
        words = raw_line.split(" ")
        cur = ""
        for w in words:
            test = (cur + " " + w).strip() if cur else w
            if _visible_len(test) <= inner_width:
                cur = test
            else:
                if cur:
                    out.append(cur)
                # If a single word is too long, hard-cut
                while _visible_len(w) > inner_width:
                    out.append(w[:inner_width])
                    w = w[inner_width:]
                cur = w
        if cur:
            out.append(cur)
    return out


def _box(title: str, body_lines: List[str], color: str = "35",
         width: int = BOX_W, title_right: str = "") -> str:
    """Generic titled box.  Title on left, optional metadata on right.
    Body lines are taken verbatim (caller wraps if needed)."""
    inner = width - 2
    title_text = f" {title} " if title else ""
    right_text = f" {title_right} " if title_right else ""
    used = len(title_text) + len(right_text)
    fill = max(2, inner - used)
    top = (f"\033[{color}m╭{'─'*1}\033[0m\033[1m{title_text}\033[0m"
           f"\033[{color}m{'─'*fill}\033[0m"
           f"\033[1m{right_text}\033[0m"
           f"\033[{color}m{'─'*1}╮\033[0m")
    out = [top]
    for ln in body_lines:
        vis = _visible_len(ln)
        pad = max(0, inner - 2 - vis)
        out.append(f"\033[{color}m│\033[0m {ln}{' ' * pad} \033[{color}m│\033[0m")
    out.append(f"\033[{color}m╰{'─'*inner}╯\033[0m")
    return "\n".join(out)


def turn_box(turn_no: int, target: str, agent_role: str, model: str,
             verified: int, unverified: int, techniques: int,
             node_id: str, width: int = BOX_W) -> str:
    """v7.2 — header box for each agent turn."""
    spec = AGENT_SPECS.get(agent_role, AGENT_SPECS["strategist"])
    target_short = (target[:18] + "…") if len(target) > 19 else target
    metas = [
        f"target \033[36m{target_short}\033[0m",
        f"node \033[97m{node_id or '—'}\033[0m",
        f"\033[32m✓{verified}\033[0m\033[90m/\033[33m?{unverified}\033[0m",
        f"\033[31mOSINT ×{techniques}\033[0m",
        f"\033[90m{model}\033[0m",
    ]
    body = ["  " + "  \033[90m·\033[0m  ".join(metas),
            f"  \033[{spec['color']}m\033[1m{spec['icon']} {spec['name']}\033[0m"]
    return _box(f"TURN {turn_no}", body, color="35",
                width=width, title_right=f"v{VERSION}")


def thought_card(thought: str, agent_role: str, width: int = BOX_W) -> str:
    """v7.2 — boxed agent thought block."""
    spec = AGENT_SPECS.get(agent_role, AGENT_SPECS["strategist"])
    inner = width - 4
    lines = _wrap_for_box(thought, inner)
    if not lines:
        lines = ["(no reasoning produced)"]
    body = []
    for ln in lines:
        body.append(f"\033[{spec['color']}m▎\033[0m \033[90m\033[3m{ln}\033[0m")
    return _box("THOUGHT", body, color=spec["color"], width=width)


def dispatch_card(tool: str, shell_str: str, attack_id: str = "",
                  attack_name: str = "", remap_note: str = "",
                  width: int = BOX_W) -> str:
    """v7.2 — boxed structured tool dispatch."""
    inner = width - 4
    body = [f"  \033[36m{tool}\033[0m \033[90m→\033[0m"]
    for ln in _wrap_for_box(shell_str, inner - 2):
        body.append(f"  \033[97m{ln}\033[0m")
    if remap_note:
        body.append(f"  \033[90m\033[3m{remap_note}\033[0m")
    title_right = ""
    if attack_id:
        title_right = f"{attack_id} {attack_name[:22]}"
    return _box("DISPATCH", body, color="36", width=width,
                title_right=title_right)


def command_card(shell_str: str, conf: str = "green", attack_id: str = "",
                 attack_name: str = "", verify: bool = False,
                 width: int = BOX_W) -> str:
    """v7.2 — proposed command, with confidence pill inline."""
    inner = width - 4
    pill_map = {
        "green":  "\033[42m\033[97m\033[1m GREEN ▶ \033[0m",
        "yellow": "\033[43m\033[30m\033[1m YELLOW · \033[0m",
        "red":    "\033[41m\033[97m\033[1m RED ✕ \033[0m",
    }
    pill = pill_map.get(conf, "\033[100m\033[97m  ?  \033[0m")
    body = []
    for ln in _wrap_for_box(shell_str, inner - 2):
        body.append(f"  \033[97m\033[1m{ln}\033[0m")
    body.append("")
    body.append(f"  conf: {pill}")
    title = "VERIFICATION" if verify else "COMMAND"
    color = "31" if verify else "35"
    title_right = ""
    if attack_id:
        title_right = f"{attack_id} {attack_name[:22]}"
    return _box(title, body, color=color, width=width,
                title_right=title_right)


def result_box(output: str, *, lines_shown: int = 12,
               width: int = BOX_W) -> str:
    """v7.2 — boxed command result, with truncation indicator."""
    inner = width - 4
    raw_lines = output.splitlines()
    shown = raw_lines[:lines_shown]
    truncated = len(raw_lines) > lines_shown
    body: List[str] = []
    for ln in shown:
        # Truncate per-line at inner-2 visible chars
        vis = _visible_len(ln)
        if vis > inner - 2:
            ln = ln[:inner - 4] + "…"
        body.append(f"  {ln}")
    if truncated:
        body.append(f"  \033[90m\033[3m… +{len(raw_lines) - lines_shown} "
                    f"more line(s) (full output stored for AI context)\033[0m")
    if not body:
        body = ["  \033[90m(no output)\033[0m"]
    return _box("RESULT", body, color="32", width=width)


def error_alert(title: str, message: str, hint: str = "",
                width: int = BOX_W) -> str:
    """v7.2 — bold red boxed alert for blocked / failed states."""
    inner = width - 4
    body: List[str] = []
    for ln in _wrap_for_box(message, inner - 2):
        body.append(f"  \033[31m{ln}\033[0m")
    if hint:
        body.append("")
        for ln in _wrap_for_box(hint, inner - 2):
            body.append(f"  \033[33m\033[1m▸\033[0m \033[97m{ln}\033[0m")
    return _box(f"⛔ {title}", body, color="31", width=width)


def findings_card(new_count: int, items: List[str], width: int = BOX_W) -> str:
    """v7.2 — boxed summary of newly extracted findings from one cmd."""
    inner = width - 4
    body: List[str] = []
    for it in items[:10]:
        if _visible_len(it) > inner - 2:
            it = it[:inner - 4] + "…"
        body.append(f"  {it}")
    if len(items) > 10:
        body.append(f"  \033[90m… +{len(items) - 10} more\033[0m")
    if not body:
        body = ["  \033[90m(no extractable findings this turn)\033[0m"]
    return _box(f"FINDINGS +{new_count}", body, color="32", width=width)


def thinking_indicator(model_name: str = "") -> str:
    """v7.1 — single-line indicator shown while LLM is thinking."""
    suffix = f" \033[90m· {model_name}\033[0m" if model_name else ""
    return f"\033[35m   ◆ ZEUS thinking…\033[0m{suffix}"


def boot_sequence_lines() -> List[str]:
    """Cinematic boot lines printed on startup."""
    graph_glyph = "\033[32m✓\033[0m" if HAS_NETWORKX else "\033[33m⚠\033[0m"
    graph_msg = ("\033[32mnetworkx ready\033[0m" if HAS_NETWORKX else
                 "\033[33mnetworkx missing — disabled\033[0m")
    gh_msg = "\033[32mGITHUB_TOKEN set\033[0m" if os.environ.get("GITHUB_TOKEN") else "\033[33mno GITHUB_TOKEN — rate-limited\033[0m"
    return [
        "\033[90m   [boot]\033[0m \033[32m✓\033[0m  loading OSINT cognitive matrix",
        f"\033[90m   [boot]\033[0m \033[32m✓\033[0m  ephemeral working dir: {INSTALL_DIR}",
        "\033[90m   [boot]\033[0m \033[32m✓\033[0m  initialising OSINT Task Tree",
        f"\033[90m   [boot]\033[0m \033[32m✓\033[0m  registering {len(AGENT_SPECS)} specialist agents",
        f"\033[90m   [boot]\033[0m \033[32m✓\033[0m  registering {len(TOOL_DISPATCH)} OSINT tools",
        f"\033[90m   [boot]\033[0m \033[32m✓\033[0m  loading {len(MITRE_TECHNIQUES)} OSINT category mappings",
        f"\033[90m   [boot]\033[0m {graph_glyph}  pivot graph: {graph_msg}",
        f"\033[90m   [boot]\033[0m  ·  GitHub auth: {gh_msg}",
        "\033[90m   [boot]\033[0m \033[32m✓\033[0m  smart-context manager online",
        f"\033[90m   [boot]\033[0m \033[32m✓\033[0m  AUTONOMOUS MODE armed (max {MAX_AUTO_TURNS} turns / {MAX_WALL_CLOCK_SECONDS//60} min wall-clock)",
        "\033[90m   [boot]\033[0m \033[32m✓\033[0m  RAM-only · no disk persistence",
        "\033[90m   [boot]\033[0m \033[32m✓\033[0m  legal-OSINT refusal patterns loaded",
        "\033[90m   [boot]\033[0m \033[32m✓\033[0m  Groq provider chain primed",
    ]


# ═════════════════════════════════════════════════════════════════════
# SPEAKER-ROLE HELPERS (v7.1 user-friendly UX layer)
#
# Every line printed to the operator should answer "who's saying this?"
# at a glance.  Five voices:
#
#   PRIEST  — the operator (you).  Input prompts only.
#   ZEUS  — the framework itself (target setup, reports, errors).
#   AGENT   — the LLM specialist's reasoning / decision.
#   EXEC    — a command being proposed / executed.
#   SYS     — system-level info (warnings, hints, dim notes).
#
# Each voice has a fixed colour + glyph so the operator knows instantly
# who's talking without parsing whole lines.
# ═════════════════════════════════════════════════════════════════════

# ANSI colour shorthands
_C_PRIEST = "\033[35m"   # magenta — operator
_C_ZEUS = "\033[96m"   # bright cyan — framework voice
_C_AGENT  = "\033[33m"   # yellow — LLM agent
_C_EXEC   = "\033[97m"   # bright white — commands
_C_SYS    = "\033[90m"   # grey — system/dim notes
_C_OK     = "\033[32m"   # green — success
_C_WARN   = "\033[33m"   # yellow — warning
_C_ERR    = "\033[31m"   # red — error
_C_RESET  = "\033[0m"
_C_BOLD   = "\033[1m"
_C_DIM    = "\033[2m"


def say_zeus(message: str, *, indent: int = 3):
    """Framework voice — Zeus talking AS the system, not as an agent."""
    pad = " " * indent
    print(f"{pad}{_C_ZEUS}{_C_BOLD}◈ ZEUS{_C_RESET}{_C_ZEUS}  {message}{_C_RESET}")


def say_agent(message: str, agent_role: str = "agent", *, indent: int = 3):
    """Specialist agent voice — the LLM's reasoning."""
    spec = AGENT_SPECS.get(agent_role, AGENT_SPECS["strategist"])
    icon = spec["icon"]
    color = spec["color"]
    pad = " " * indent
    print(f"{pad}\033[{color}m{_C_BOLD}{icon} {spec['name'].split()[0]}{_C_RESET}"
          f"\033[{color}m  {message}{_C_RESET}")


def say_priest_prompt(prompt: str = "") -> str:
    """Render the priest input prompt (returns the formatted string for input())."""
    return f"  {_C_PRIEST}{_C_BOLD}⚔ priest{_C_RESET}{_C_PRIEST} ›{_C_RESET} {prompt}"


def say_sys(message: str, *, color: str = "90", indent: int = 3):
    """Generic system message (warnings, hints, info)."""
    pad = " " * indent
    print(f"{pad}\033[{color}m▸ {message}{_C_RESET}")


def say_dim(message: str, *, indent: int = 3):
    """Faint informational line."""
    pad = " " * indent
    print(f"{pad}{_C_SYS}{message}{_C_RESET}")


def say_ok(message: str, *, indent: int = 3):
    pad = " " * indent
    print(f"{pad}{_C_OK}✓ {message}{_C_RESET}")


def say_warn(message: str, *, indent: int = 3):
    pad = " " * indent
    print(f"{pad}{_C_WARN}⚠ {message}{_C_RESET}")


def say_err(message: str, *, indent: int = 3):
    pad = " " * indent
    print(f"{pad}{_C_ERR}✕ {message}{_C_RESET}")


def say_thought(message: str, agent_role: str = "agent", *, indent: int = 6):
    """The LLM's chain-of-thought.  Distinct from agent decisions —
    this is the dim italic 'thinking aloud' voice."""
    pad = " " * indent
    color = AGENT_SPECS.get(agent_role, AGENT_SPECS["strategist"])["color"]
    # Each line of thought gets a small marker
    for line in message.split("\n"):
        line = line.strip()
        if not line:
            continue
        print(f"{pad}\033[{color}m\033[2m│{_C_RESET} \033[90m\033[3m{line}{_C_RESET}")


def speakers_legend() -> str:
    """Tiny legend bar showing what each voice means.  Printed once
    at the top of the help so the operator learns the symbol set."""
    return (
        f"   {_C_SYS}voices:{_C_RESET}  "
        f"{_C_PRIEST}{_C_BOLD}⚔ priest{_C_RESET} {_C_SYS}you{_C_RESET}  "
        f"{_C_ZEUS}{_C_BOLD}◈ ZEUS{_C_RESET} {_C_SYS}framework{_C_RESET}  "
        f"{_C_AGENT}{_C_BOLD}🚨 TRIAGE{_C_RESET} {_C_SYS}AI agent{_C_RESET}  "
        f"{_C_EXEC}▌{_C_RESET} {_C_SYS}command{_C_RESET}  "
        f"{_C_OK}✓{_C_RESET} {_C_SYS}ok{_C_RESET}  "
        f"{_C_WARN}⚠{_C_RESET} {_C_SYS}warn{_C_RESET}  "
        f"{_C_ERR}✕{_C_RESET} {_C_SYS}error{_C_RESET}"
    )


# ═════════════════════════════════════════════════════════════════════
# TOOL WRAPPER LAYER  (ToolBuilder)
#
# Typed builders that produce shell strings.  The LLM picks the tool +
# arguments, we build the command.  This kills the v6.1 problem of the
# AI typing `nano`, `msfconsole` (interactive), `ssh user@host`, etc.,
# because the wrappers inherently produce non-interactive forms.
#
# All wrappers return a ready-to-execute shell string.
# ═════════════════════════════════════════════════════════════════════

class ToolBuilder:

    # ── Username / handle pivots ──────────────────────────────────────

    @staticmethod
    def sherlock_run(username: str,
                     timeout_sec: int = 10,
                     print_found_only: bool = True,
                     output_path: Optional[str] = None) -> str:
        out = output_path or f"/tmp/zeus_{os.getpid()}/sherlock_{username}.txt"
        flags = f"--timeout {timeout_sec} --no-color"
        if print_found_only:
            flags += " --print-found"
        return (f"mkdir -p /tmp/zeus_{os.getpid()} && "
                f"sherlock {flags} -o {out} {username} 2>&1 | "
                f"grep -E '^\\[\\+\\]' | head -100")

    @staticmethod
    def maigret_run(username: str,
                    timeout_sec: int = 10,
                    top_sites: int = 200) -> str:
        return (f"maigret --timeout {timeout_sec} --top-sites {top_sites} "
                f"--no-color --no-progressbar {username} 2>&1 | "
                f"grep -E '\\[\\+\\]|FOUND' | head -100")

    @staticmethod
    def socialscan_run(target: str) -> str:
        # Works for username OR email
        return f"socialscan {target} 2>&1 | head -80"

    @staticmethod
    def whatsmyname_query(username: str) -> str:
        return (f"curl -s --max-time 30 "
                f"'https://whatsmyname.app/api/v1/usernames/{username}' "
                f"| jq -r '.[] | select(.exists==true) | "
                f".name + \" | \" + .url'")

    # ── Email triage (no HIBP — paid; no DeHashed — refused) ─────────

    @staticmethod
    def holehe_run(email: str, only_used: bool = True) -> str:
        flag = "--only-used" if only_used else ""
        return f"holehe --no-color {flag} {email} 2>&1 | head -80"

    @staticmethod
    def gravatar_lookup(email: str) -> str:
        # md5 of lowercased trimmed email → public gravatar
        return (f"H=$(echo -n '{email.lower().strip()}' | md5sum | cut -d' ' -f1) "
                f"&& curl -s --max-time 10 \"https://www.gravatar.com/$H.json\" "
                f"| jq '.entry[0] // \"no public gravatar\"'")

    @staticmethod
    def github_email_search(email: str) -> str:
        token = "${GITHUB_TOKEN:-}"
        auth = f"-H \"Authorization: token $GITHUB_TOKEN\"" if True else ""
        return (f"curl -s --max-time 15 "
                f"-H \"Accept: application/vnd.github.v3+json\" "
                f"$([ -n \"$GITHUB_TOKEN\" ] && echo "
                f"\"-H 'Authorization: token $GITHUB_TOKEN'\") "
                f"'https://api.github.com/search/users?q={email}+in:email' "
                f"| jq '.items[]? | {{login, html_url, name}}'")

    @staticmethod
    def email_dns_records(email_or_domain: str) -> str:
        # Strip @ if email; query SPF/DMARC/MX
        return (f"D=$(echo '{email_or_domain}' | awk -F@ '{{print $NF}}') && "
                f"echo \"== MX ==\" && dig +short MX $D && "
                f"echo \"== TXT (SPF) ==\" && dig +short TXT $D | grep -i spf && "
                f"echo \"== DMARC ==\" && dig +short TXT _dmarc.$D")

    # ── Phone OSINT ───────────────────────────────────────────────────

    @staticmethod
    def phoneinfoga_scan(number: str) -> str:
        return f"phoneinfoga scan -n '{number}' 2>&1 | head -50"

    # ── Domain / DNS / WHOIS ──────────────────────────────────────────

    @staticmethod
    def whois_lookup(target: str) -> str:
        return (f"whois {target} 2>&1 | "
                f"grep -iE 'registrar|created|updated|expir|name server|registrant' "
                f"| head -30")

    @staticmethod
    def dig_lookup(domain: str, record_type: str = "ANY") -> str:
        if record_type.upper() == "ANY":
            return (f"echo '== A ==' && dig +short {domain} A && "
                    f"echo '== AAAA ==' && dig +short {domain} AAAA && "
                    f"echo '== MX ==' && dig +short {domain} MX && "
                    f"echo '== TXT ==' && dig +short {domain} TXT && "
                    f"echo '== NS ==' && dig +short {domain} NS && "
                    f"echo '== CAA ==' && dig +short {domain} CAA")
        return f"dig +short {domain} {record_type}"

    @staticmethod
    def host_lookup(target: str) -> str:
        return f"host {target} 2>&1"

    @staticmethod
    def subfinder_passive(domain: str, silent: bool = True) -> str:
        flag = "-silent" if silent else ""
        return f"subfinder {flag} -all -d {domain} 2>/dev/null | sort -u | head -100"

    @staticmethod
    def amass_passive(domain: str) -> str:
        return f"amass enum -passive -d {domain} -silent 2>/dev/null | sort -u | head -100"

    @staticmethod
    def assetfinder_run(domain: str) -> str:
        return f"assetfinder --subs-only {domain} 2>/dev/null | sort -u | head -100"

    @staticmethod
    def crt_sh_query(domain: str) -> str:
        return (f"curl -s --max-time 30 'https://crt.sh/?q=%25.{domain}&output=json' "
                f"| jq -r '.[]?.name_value' 2>/dev/null | sort -u | head -100")

    @staticmethod
    def reverse_ip_hackertarget(ip_or_domain: str) -> str:
        return (f"curl -s --max-time 15 "
                f"'https://api.hackertarget.com/reverseiplookup/?q={ip_or_domain}'")

    @staticmethod
    def asn_lookup(ip: str) -> str:
        return (f"curl -s --max-time 10 'https://api.bgpview.io/ip/{ip}' "
                f"| jq '.data | {{ip: .ip, asn: .rir_allocation.asn, "
                f"prefix: .prefix, country: .country_code, "
                f"isp: .rir_allocation.country_code}}'")

    @staticmethod
    def whatweb_passive(target: str) -> str:
        return f"whatweb -a 1 --no-errors '{target}' 2>&1 | head -10"

    @staticmethod
    def http_headers(url: str) -> str:
        return f"curl -s -I --max-time 10 -L '{url}' | head -25"

    # ── Archive history ──────────────────────────────────────────────

    @staticmethod
    def waybackurls_run(domain: str, max_results: int = 50) -> str:
        return f"waybackurls {domain} 2>/dev/null | sort -u | head -{max_results}"

    @staticmethod
    def gau_run(domain: str, max_results: int = 50) -> str:
        return f"gau --threads 5 {domain} 2>/dev/null | sort -u | head -{max_results}"

    @staticmethod
    def wayback_check(url: str) -> str:
        return (f"curl -s --max-time 10 "
                f"'http://archive.org/wayback/available?url={url}' | jq")

    # ── Image / EXIF ─────────────────────────────────────────────────

    @staticmethod
    def exiftool_run(image_path: str) -> str:
        return f"exiftool '{image_path}' 2>&1 | head -60"

    @staticmethod
    def exiftool_gps_only(image_path: str) -> str:
        return (f"exiftool -gps:all -datetime:all -make -model -software "
                f"-copyright '{image_path}' 2>&1")

    # ── GitHub OSINT (free with optional GITHUB_TOKEN) ───────────────

    @staticmethod
    def github_user_api(username: str) -> str:
        return (f"curl -s --max-time 10 "
                f"-H 'Accept: application/vnd.github.v3+json' "
                f"$([ -n \"$GITHUB_TOKEN\" ] && echo "
                f"\"-H 'Authorization: token $GITHUB_TOKEN'\") "
                f"'https://api.github.com/users/{username}' | jq "
                f"'{{login, name, email, company, blog, location, "
                f"bio, twitter_username, public_repos, public_gists, "
                f"followers, created_at}}'")

    @staticmethod
    def github_keys_check(username: str) -> str:
        return (f"echo '== SSH keys ==' && "
                f"curl -s --max-time 10 'https://github.com/{username}.keys' && "
                f"echo '== GPG keys ==' && "
                f"curl -s --max-time 10 'https://github.com/{username}.gpg' "
                f"| head -20")

    @staticmethod
    def github_repos_list(username: str, sort: str = "pushed") -> str:
        return (f"curl -s --max-time 15 "
                f"$([ -n \"$GITHUB_TOKEN\" ] && echo "
                f"\"-H 'Authorization: token $GITHUB_TOKEN'\") "
                f"'https://api.github.com/users/{username}/repos?per_page=30&sort={sort}' "
                f"| jq -r '.[] | \"\\(.name) | \\(.description // \"-\") | "
                f"\\(.html_url) | pushed=\\(.pushed_at)\"' | head -30")

    @staticmethod
    def github_events(username: str) -> str:
        return (f"curl -s --max-time 15 "
                f"$([ -n \"$GITHUB_TOKEN\" ] && echo "
                f"\"-H 'Authorization: token $GITHUB_TOKEN'\") "
                f"'https://api.github.com/users/{username}/events/public' "
                f"| jq -r '.[] | \"\\(.type) | \\(.repo.name) | \\(.created_at)\"' "
                f"| head -20")

    @staticmethod
    def github_user_search(query: str, qualifier: str = "in:fullname") -> str:
        # qualifier: in:fullname | in:email | in:login
        return (f"curl -s --max-time 15 "
                f"$([ -n \"$GITHUB_TOKEN\" ] && echo "
                f"\"-H 'Authorization: token $GITHUB_TOKEN'\") "
                f"'https://api.github.com/search/users?q={query}+{qualifier}' "
                f"| jq -r '.items[]? | \"\\(.login) | \\(.html_url)\"' | head -20")

    # ── Reddit / Mastodon / Bluesky public APIs ──────────────────────

    @staticmethod
    def reddit_user_info(username: str) -> str:
        return (f"curl -s --max-time 10 "
                f"-A 'zeus-osint/1.0' "
                f"'https://www.reddit.com/user/{username}/about.json' "
                f"| jq '.data | {{name, created_utc, total_karma, "
                f"comment_karma, link_karma, is_employee, has_verified_email, "
                f"public_description: .subreddit.public_description}}'")

    @staticmethod
    def mastodon_lookup(handle: str, server: str = "mastodon.social") -> str:
        # handle format: name@server  (or just name with default server)
        if "@" in handle:
            name, server = handle.split("@", 1)
        else:
            name = handle
        return (f"curl -s --max-time 10 "
                f"'https://{server}/api/v1/accounts/lookup?acct={name}' "
                f"| jq '{{display_name, username, url, note, "
                f"created_at, followers_count, statuses_count}}'")

    @staticmethod
    def bluesky_resolve(handle: str) -> str:
        return (f"curl -s --max-time 10 "
                f"'https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor={handle}' "
                f"| jq '{{did, handle, displayName, description, "
                f"followersCount, followsCount, postsCount}}'")

    # ── Curated dorks (whitelist only) ───────────────────────────────

    @staticmethod
    def google_dork_curated(domain: str, dork_type: str = "env") -> str:
        # Actual google search requires browser/captcha — Zeus can't
        # programmatically scrape Google.  This function PRINTS the
        # exact dork URL the operator should open manually.  No scraping.
        dorks = {
            "env":         f"site:{domain} ext:env",
            "log":         f"site:{domain} ext:log",
            "config":      f"site:{domain} ext:cfg OR ext:conf OR ext:config",
            "key":         f"site:{domain} intext:'BEGIN RSA PRIVATE KEY'",
            "api_key":     f"site:{domain} intext:'api_key' OR intext:'apikey'",
            "aws_secret":  f"site:{domain} intext:'AWS_SECRET'",
            "wp_config":   f"site:{domain} inurl:wp-config",
            "git_config":  f"site:{domain} inurl:.git/config",
            "pastebin":    f"site:pastebin.com {domain}",
        }
        dork = dorks.get(dork_type, dorks["env"])
        return (f"echo 'Open in browser:'; "
                f"echo 'https://www.google.com/search?q='"
                f"$(python3 -c \"import urllib.parse;print(urllib.parse.quote('{dork}'))\")")

    @staticmethod
    def github_dork(query: str) -> str:
        # Search code on GitHub — works with API, requires token
        return (f"if [ -z \"$GITHUB_TOKEN\" ]; then "
                f"echo 'WARNING: GITHUB_TOKEN not set — skipping GitHub code search.'; "
                f"echo 'Set GITHUB_TOKEN in env to enable this branch.'; "
                f"else curl -s --max-time 15 "
                f"-H 'Authorization: token $GITHUB_TOKEN' "
                f"-H 'Accept: application/vnd.github.v3+json' "
                f"'https://api.github.com/search/code?q={query}' "
                f"| jq -r '.items[]? | \"\\(.repository.full_name) | "
                f"\\(.path) | \\(.html_url)\"' | head -20; fi")

    # ── Blockchain / public ledger ───────────────────────────────────

    @staticmethod
    def btc_address_balance(address: str) -> str:
        return (f"curl -s --max-time 15 "
                f"'https://blockstream.info/api/address/{address}' | jq")

    @staticmethod
    def btc_address_txs(address: str) -> str:
        return (f"curl -s --max-time 15 "
                f"'https://blockstream.info/api/address/{address}/txs' "
                f"| jq -r '.[] | \"\\(.txid) | block=\\(.status.block_height) | "
                f"\\(.status.block_time)\"' | head -10")

    @staticmethod
    def eth_address_balance(address: str) -> str:
        return (f"curl -s --max-time 15 "
                f"'https://api.blockchair.com/ethereum/dashboards/address/{address}' "
                f"| jq '.data')")

    @staticmethod
    def blockchair_address(address: str, chain: str = "bitcoin") -> str:
        # chain: bitcoin, ethereum, litecoin, dogecoin, etc
        return (f"curl -s --max-time 15 "
                f"'https://api.blockchair.com/{chain}/dashboards/address/{address}' "
                f"| jq '.data | to_entries | .[0].value.address'")

    # ── IOC enrichment (threat-actor lane) ───────────────────────────

    @staticmethod
    def otx_indicator(indicator: str, indicator_type: str = "domain") -> str:
        # type: IPv4, IPv6, domain, hostname, file, url
        return (f"curl -s --max-time 15 "
                f"'https://otx.alienvault.com/api/v1/indicators/{indicator_type}/{indicator}/general' "
                f"| jq '{{pulse_count: .pulse_info.count, "
                f"reputation, country_name, asn, indicator_type}}'")

    @staticmethod
    def threatfox_search(ioc: str) -> str:
        return (f"curl -s --max-time 15 "
                f"-X POST 'https://threatfox-api.abuse.ch/api/v1/' "
                f"-d '{{\"query\":\"search_ioc\",\"search_term\":\"{ioc}\"}}' "
                f"| jq '.data[]? | {{ioc, malware, confidence_level, "
                f"first_seen, threat_type}}'")

    @staticmethod
    def urlhaus_check(url: str) -> str:
        return (f"curl -s --max-time 15 "
                f"'https://urlhaus-api.abuse.ch/v1/url/' "
                f"-d 'url={url}' | jq")

    @staticmethod
    def ipinfo_lookup(ip: str) -> str:
        return f"curl -s --max-time 10 'https://ipinfo.io/{ip}/json' | jq"

    # ── Generic helpers ──────────────────────────────────────────────

    @staticmethod
    def curl_basic(url: str,
                   head_only: bool = False,
                   user_agent: str = "zeus-osint/1.0",
                   silent: bool = True,
                   max_time: int = 15) -> str:
        flags = []
        if silent:
            flags.append("-s")
        if head_only:
            flags.append("-I")
        flags.append(f"-A '{user_agent}'")
        flags.append(f"--max-time {max_time}")
        return f"curl {' '.join(flags)} '{url}'"

    @staticmethod
    def theharvester_passive(domain: str, sources: str = "crtsh,duckduckgo,rapiddns,otx") -> str:
        return f"theHarvester -d {domain} -b {sources} -l 200 2>&1 | tail -80"

    @staticmethod
    def urlscan_search(domain: str) -> str:
        return (f"curl -s --max-time 15 "
                f"$([ -n \"$URLSCAN_KEY\" ] && echo \"-H 'API-Key: $URLSCAN_KEY'\") "
                f"'https://urlscan.io/api/v1/search/?q=domain:{domain}' "
                f"| jq '.results[]? | {{url: .page.url, ip: .page.ip, "
                f"server: .page.server, time: .task.time}}' | head -30")


TOOL_DISPATCH = {
    # Username pivots
    "sherlock_run":           ToolBuilder.sherlock_run,
    "maigret_run":            ToolBuilder.maigret_run,
    "socialscan_run":         ToolBuilder.socialscan_run,
    "whatsmyname_query":      ToolBuilder.whatsmyname_query,
    # Email triage
    "holehe_run":             ToolBuilder.holehe_run,
    "gravatar_lookup":        ToolBuilder.gravatar_lookup,
    "github_email_search":    ToolBuilder.github_email_search,
    "email_dns_records":      ToolBuilder.email_dns_records,
    # Phone
    "phoneinfoga_scan":       ToolBuilder.phoneinfoga_scan,
    # Domain / DNS / WHOIS
    "whois_lookup":           ToolBuilder.whois_lookup,
    "dig_lookup":             ToolBuilder.dig_lookup,
    "host_lookup":            ToolBuilder.host_lookup,
    "subfinder_passive":      ToolBuilder.subfinder_passive,
    "amass_passive":          ToolBuilder.amass_passive,
    "assetfinder_run":        ToolBuilder.assetfinder_run,
    "crt_sh_query":           ToolBuilder.crt_sh_query,
    "reverse_ip_hackertarget": ToolBuilder.reverse_ip_hackertarget,
    "asn_lookup":             ToolBuilder.asn_lookup,
    "whatweb_passive":        ToolBuilder.whatweb_passive,
    "http_headers":           ToolBuilder.http_headers,
    # Archive
    "waybackurls_run":        ToolBuilder.waybackurls_run,
    "gau_run":                ToolBuilder.gau_run,
    "wayback_check":          ToolBuilder.wayback_check,
    # Image / EXIF
    "exiftool_run":           ToolBuilder.exiftool_run,
    "exiftool_gps_only":      ToolBuilder.exiftool_gps_only,
    # GitHub
    "github_user_api":        ToolBuilder.github_user_api,
    "github_keys_check":      ToolBuilder.github_keys_check,
    "github_repos_list":      ToolBuilder.github_repos_list,
    "github_events":          ToolBuilder.github_events,
    "github_user_search":     ToolBuilder.github_user_search,
    # Reddit / Mastodon / Bluesky
    "reddit_user_info":       ToolBuilder.reddit_user_info,
    "mastodon_lookup":        ToolBuilder.mastodon_lookup,
    "bluesky_resolve":        ToolBuilder.bluesky_resolve,
    # Dorking (curated)
    "google_dork_curated":    ToolBuilder.google_dork_curated,
    "github_dork":            ToolBuilder.github_dork,
    # Blockchain
    "btc_address_balance":    ToolBuilder.btc_address_balance,
    "btc_address_txs":        ToolBuilder.btc_address_txs,
    "eth_address_balance":    ToolBuilder.eth_address_balance,
    "blockchair_address":     ToolBuilder.blockchair_address,
    # IOC enrichment (threat-actor lane)
    "otx_indicator":          ToolBuilder.otx_indicator,
    "threatfox_search":       ToolBuilder.threatfox_search,
    "urlhaus_check":          ToolBuilder.urlhaus_check,
    "ipinfo_lookup":          ToolBuilder.ipinfo_lookup,
    # Generic
    "curl_basic":             ToolBuilder.curl_basic,
    "theharvester_passive":   ToolBuilder.theharvester_passive,
    "urlscan_search":         ToolBuilder.urlscan_search,
}


# Primary binary lookup per tool name.  Used by dispatch to do a
# pre-flight `which` check before generating the shell string.
TOOL_BINARY = {
    "sherlock_run":           "sherlock",
    "maigret_run":            "maigret",
    "socialscan_run":         "socialscan",
    "whatsmyname_query":      "curl",
    "holehe_run":             "holehe",
    "gravatar_lookup":        "curl",
    "github_email_search":    "curl",
    "email_dns_records":      "dig",
    "phoneinfoga_scan":       "phoneinfoga",
    "whois_lookup":           "whois",
    "dig_lookup":             "dig",
    "host_lookup":            "host",
    "subfinder_passive":      "subfinder",
    "amass_passive":          "amass",
    "assetfinder_run":        "assetfinder",
    "crt_sh_query":           "curl",
    "reverse_ip_hackertarget": "curl",
    "asn_lookup":             "curl",
    "whatweb_passive":        "whatweb",
    "http_headers":           "curl",
    "waybackurls_run":        "waybackurls",
    "gau_run":                "gau",
    "wayback_check":          "curl",
    "exiftool_run":           "exiftool",
    "exiftool_gps_only":      "exiftool",
    "github_user_api":        "curl",
    "github_keys_check":      "curl",
    "github_repos_list":      "curl",
    "github_events":          "curl",
    "github_user_search":     "curl",
    "reddit_user_info":       "curl",
    "mastodon_lookup":        "curl",
    "bluesky_resolve":        "curl",
    "google_dork_curated":    "curl",
    "github_dork":            "curl",
    "btc_address_balance":    "curl",
    "btc_address_txs":        "curl",
    "eth_address_balance":    "curl",
    "blockchair_address":     "curl",
    "otx_indicator":          "curl",
    "threatfox_search":       "curl",
    "urlhaus_check":          "curl",
    "ipinfo_lookup":          "curl",
    "curl_basic":             "curl",
    "theharvester_passive":   "theHarvester",
    "urlscan_search":         "curl",
}


def _tool_binary_present(tool_name: str) -> Tuple[bool, str]:
    """Return (present, install_hint).  Zeus degrades gracefully when a
    tool is missing — that branch just gets skipped with a note in the
    final report."""
    binary = TOOL_BINARY.get(tool_name)
    if binary is None:
        return (True, "")
    if cmd_exists(binary):
        return (True, "")
    alt_map = {
        "sherlock":      "Install: pipx install sherlock-project (or apt install sherlock)",
        "maigret":       "Install: pipx install maigret",
        "socialscan":    "Install: pipx install socialscan",
        "holehe":        "Install: pipx install holehe",
        "phoneinfoga":   "Install: download from github.com/sundowndev/phoneinfoga/releases",
        "subfinder":     "Install: go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest",
        "amass":         "Install: apt install amass  (or go install github.com/owasp-amass/amass/v4/...@master)",
        "assetfinder":   "Install: go install github.com/tomnomnom/assetfinder@latest",
        "waybackurls":   "Install: go install github.com/tomnomnom/waybackurls@latest",
        "gau":           "Install: go install github.com/lc/gau/v2/cmd/gau@latest",
        "exiftool":      "Install: apt install libimage-exiftool-perl",
        "whatweb":       "Install: apt install whatweb",
        "theHarvester":  "Install: apt install theharvester  (or pipx install theHarvester)",
        "whois":         "Install: apt install whois",
        "dig":           "Install: apt install dnsutils  (provides dig + host + nslookup)",
        "host":          "Install: apt install dnsutils",
        "curl":          "Install: apt install curl",
    }
    alt = alt_map.get(binary) or f"Install: apt install {binary}"
    return (False, alt)


def _apply_kwarg_synonyms(name: str, args: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Map common LLM-emitted synonyms to the real builder param names.
    Returns (cleaned_args, list_of_remappings_done) for visibility."""
    syn_map = KWARG_SYNONYMS.get(name, {})
    remapped: List[str] = []
    out: Dict[str, Any] = {}
    for k, v in args.items():
        if k in syn_map:
            real = syn_map[k]
            if real is None:
                # silent drop — this is a recognised no-op alias
                remapped.append(f"{k}=<dropped>")
                continue
            # Avoid clobbering an explicit real-name arg
            if real not in args:
                out[real] = v
                remapped.append(f"{k}→{real}")
            else:
                # both supplied — prefer the canonical one already present
                remapped.append(f"{k}=<duplicate of {real}, ignored>")
        else:
            out[k] = v
    return (out, remapped)


def dispatch_tool(name: str, args_json: str) -> Tuple[Optional[str], Optional[str]]:
    """Resolve a [TOOL]/[ARGS] pair into a shell command.

    Returns (shell_string, msg) tuple:
      • (shell, None)         — clean success
      • (shell, "NOTE: ...")  — success with a note (e.g. synonyms remapped)
      • (None, "ERROR: ...")  — hard failure; caller MUST feed this back
                                 to the LLM in the next prompt so it can
                                 correct rather than loop.
    """
    if name not in TOOL_DISPATCH:
        available = ", ".join(sorted(TOOL_DISPATCH.keys()))
        return (None,
                f"ERROR: unknown tool '{name}'. Available: {available}. "
                f"Use [CMD] for ad-hoc commands.")

    # v7.2 — pre-flight binary check
    present, alt = _tool_binary_present(name)
    if not present:
        return (None,
                f"ERROR: tool '{name}' not installed on this system. "
                f"{alt}  Pivot to a different tool or use [CMD] with "
                f"something already available.")

    try:
        args = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError as e:
        return (None,
                f"ERROR: bad JSON in [ARGS] for {name}: {e}. "
                f"Example: [ARGS]{{\"target\":\"10.0.0.5\"}}[/ARGS]")

    if not isinstance(args, dict):
        return (None,
                f"ERROR: [ARGS] must be a JSON object, got "
                f"{type(args).__name__}")

    # v7.2 — apply known synonyms first
    args, remapped = _apply_kwarg_synonyms(name, args)

    # Now check for kwargs that are STILL unknown after synonym mapping.
    fn = TOOL_DISPATCH[name]
    try:
        sig = inspect.signature(fn)
        # Builder methods may use _foo "private" params for synonym
        # forwarding (e.g. _scan_type).  These are valid kwargs.
        valid = set(sig.parameters.keys())
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD
                             for p in sig.parameters.values())
        unknown = [k for k in args.keys() if k not in valid] if not accepts_kwargs else []
    except (ValueError, TypeError):
        unknown = []

    if unknown:
        # Hard error fed back to LLM with the actual valid args listed
        try:
            sig = inspect.signature(fn)
            valid_list = []
            for pname, p in sig.parameters.items():
                if pname.startswith("_"):
                    continue  # hidden synonym slots
                if p.default is inspect.Parameter.empty:
                    valid_list.append(pname)
                else:
                    valid_list.append(f"{pname}={p.default!r}")
            valid_str = ", ".join(valid_list)
        except Exception:
            valid_str = "(introspection failed)"
        return (None,
                f"ERROR: {name} got unknown arg(s): {', '.join(unknown)}. "
                f"Valid args: {valid_str}. "
                f"Use [CMD] if {name} doesn't fit your need.")

    try:
        shell_str = fn(**args)
    except TypeError as e:
        # Missing required arg, or other signature problem
        try:
            sig = inspect.signature(fn)
            required = [p for p, info in sig.parameters.items()
                        if info.default is inspect.Parameter.empty
                        and not p.startswith("_")]
            return (None,
                    f"ERROR: bad args for {name}: {e}. Required: "
                    f"{', '.join(required) if required else '(none)'}.")
        except Exception:
            return (None, f"ERROR: bad args for {name}: {e}")
    except Exception as e:
        return (None, f"ERROR: {name} builder error: {e}")

    if not shell_str or not isinstance(shell_str, str):
        return (None, f"ERROR: {name} returned no command string")

    # Soft note for remappings (success path)
    if remapped:
        return (shell_str, f"NOTE: arg synonyms remapped: {', '.join(remapped)}")

    return (shell_str, None)


def tool_registry_for_prompt() -> str:
    """Compact registry summary so the LLM knows what's available
    structured.  Inspects each builder's signature to surface the
    expected args without us hardcoding it twice."""
    lines = ["STRUCTURED TOOLS (use [TOOL]name[/TOOL][ARGS]json[/ARGS]):"]
    for name, fn in sorted(TOOL_DISPATCH.items()):
        try:
            sig = inspect.signature(fn)
            params = []
            for pname, p in sig.parameters.items():
                # v7.2 — hide private synonym-forwarding params
                if pname.startswith("_"):
                    continue
                if p.default is inspect.Parameter.empty:
                    params.append(pname)
                else:
                    default = p.default
                    if isinstance(default, str):
                        params.append(f"{pname}='{default[:25]}'")
                    else:
                        params.append(f"{pname}={default}")
            lines.append(f"  {name}({', '.join(params)})")
        except (ValueError, TypeError):
            lines.append(f"  {name}(...)")
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════
# SCOPE / RoE ENFORCEMENT (v7.1)
#
# Scope is loaded from ~/.zeus/scope.json (created on first run if
# missing).  Defines allowed CIDRs, allowed/blocked domains, and time
# windows.  Out-of-scope commands are refused before they hit
# subprocess.  Critical for legitimate engagements bound by SOWs.
# ═════════════════════════════════════════════════════════════════════

DEFAULT_SCOPE = {
    "enabled":  False,
    "allowed_cidrs":   ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
    "blocked_cidrs":   [],
    "allowed_domains": [],     # ["target.com", "*.target.com"]
    "blocked_domains": [],
    "time_window": {           # ISO-8601 strings; empty = no window
        "start": "",
        "end":   "",
    },
    "note": (
        "Set 'enabled' to true to enforce.  Out-of-scope commands "
        "will be refused before execution.  Wildcards (*.example.com) "
        "supported in domains.  Time window applies in local TZ."
    ),
}


@dataclass
class ScopeConfig:
    enabled: bool = False
    allowed_cidrs:   List[str] = field(default_factory=list)
    blocked_cidrs:   List[str] = field(default_factory=list)
    allowed_domains: List[str] = field(default_factory=list)
    blocked_domains: List[str] = field(default_factory=list)
    time_start: str = ""
    time_end:   str = ""

    @classmethod
    def load(cls, path: str = SCOPE_FILE) -> "ScopeConfig":
        # Create default if missing
        if not os.path.exists(path):
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    json.dump(DEFAULT_SCOPE, f, indent=2)
            except Exception:
                pass
            return cls()
        try:
            with open(path) as f:
                data = json.load(f)
            tw = data.get("time_window", {}) or {}
            return cls(
                enabled=data.get("enabled", False),
                allowed_cidrs=data.get("allowed_cidrs", []),
                blocked_cidrs=data.get("blocked_cidrs", []),
                allowed_domains=data.get("allowed_domains", []),
                blocked_domains=data.get("blocked_domains", []),
                time_start=tw.get("start", ""),
                time_end=tw.get("end", ""),
            )
        except Exception as e:
            print(f"\033[33m   Scope file error ({e}) — proceeding with no scope\033[0m")
            return cls()

    def _domain_matches(self, host: str, patterns: List[str]) -> bool:
        host = host.lower().strip()
        for pat in patterns:
            pat = pat.lower().strip()
            if pat.startswith("*."):
                if host == pat[2:] or host.endswith(pat[1:]):
                    return True
            elif pat == host:
                return True
        return False

    def _ip_in_cidrs(self, ip: str, cidrs: List[str]) -> bool:
        try:
            ip_obj = ipaddress.ip_address(ip)
        except ValueError:
            return False
        for cidr in cidrs:
            try:
                if ip_obj in ipaddress.ip_network(cidr, strict=False):
                    return True
            except ValueError:
                continue
        return False

    def _check_time_window(self) -> Tuple[bool, str]:
        if not self.time_start and not self.time_end:
            return (True, "")
        now = datetime.datetime.now()
        if self.time_start:
            try:
                start = datetime.datetime.fromisoformat(self.time_start)
                if now < start:
                    return (False, f"Before window start ({self.time_start})")
            except ValueError:
                pass
        if self.time_end:
            try:
                end = datetime.datetime.fromisoformat(self.time_end)
                if now > end:
                    return (False, f"After window end ({self.time_end})")
            except ValueError:
                pass
        return (True, "")

    def check(self, cmd: str, target_hint: str = "") -> Tuple[bool, str]:
        """Return (allowed, reason).  If not enabled, always allowed."""
        if not self.enabled:
            return (True, "")

        # Time window first
        ok, reason = self._check_time_window()
        if not ok:
            return (False, f"Outside engagement time window: {reason}")

        # Pull every IP and bare-hostname from the command
        ips = set(re.findall(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b', cmd))
        # Domains: filter out IPs and noise
        domain_candidates = set(re.findall(
            r'\b([a-zA-Z][a-zA-Z0-9\-_]*(?:\.[a-zA-Z0-9\-_]+)+)\b', cmd))
        domains = set()
        for d in domain_candidates:
            if re.match(r'^\d+\.\d+\.\d+\.\d+$', d):
                continue
            if d.lower() in DOMAIN_NOISE:
                continue
            domains.add(d.lower())

        # Add the explicit target hint if any
        if target_hint:
            ip_match = re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', target_hint)
            if ip_match:
                ips.add(target_hint)
            else:
                domains.add(target_hint.lower())

        # If no targets present, treat as local/utility command — allow
        if not ips and not domains:
            return (True, "")

        # Check blocks first
        for ip in ips:
            if self._ip_in_cidrs(ip, self.blocked_cidrs):
                return (False, f"IP {ip} in blocked_cidrs")
        for d in domains:
            if self._domain_matches(d, self.blocked_domains):
                return (False, f"Domain {d} in blocked_domains")

        # Check allows (only if any allow rules exist)
        # Note: IP_NOISE filter is intentionally NOT applied here —
        # scope enforcement must check every target IP, even if it's
        # something like 8.8.8.8 that we'd normally ignore as noise
        # in finding extraction.
        has_ip_allow = bool(self.allowed_cidrs)
        has_dom_allow = bool(self.allowed_domains)

        if has_ip_allow:
            for ip in ips:
                if not self._ip_in_cidrs(ip, self.allowed_cidrs):
                    return (False, f"IP {ip} not in allowed_cidrs")

        if has_dom_allow:
            for d in domains:
                if not self._domain_matches(d, self.allowed_domains):
                    return (False, f"Domain {d} not in allowed_domains")

        return (True, "")

    def summary(self) -> str:
        lines = []
        state = "\033[32mENABLED\033[0m" if self.enabled else "\033[90mdisabled\033[0m"
        lines.append(f"Scope: {state}")
        if self.allowed_cidrs:
            lines.append(f"  allowed CIDRs:   {', '.join(self.allowed_cidrs)}")
        if self.blocked_cidrs:
            lines.append(f"  blocked CIDRs:   {', '.join(self.blocked_cidrs)}")
        if self.allowed_domains:
            lines.append(f"  allowed domains: {', '.join(self.allowed_domains)}")
        if self.blocked_domains:
            lines.append(f"  blocked domains: {', '.join(self.blocked_domains)}")
        if self.time_start or self.time_end:
            lines.append(f"  time window:     {self.time_start or '(open)'} → {self.time_end or '(open)'}")
        return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════
# ATTACK GRAPH (v7.1)
#
# Lightweight graph-based memory layered on top of findings.  Hosts,
# services, credentials, hashes, vulns are nodes.  Edges encode
# relationships: "service runs_on host", "cred works_on service",
# "vuln affects service", "host can_pivot_to host".  Used for surfacing
# pivot suggestions the LLM might miss in the flat finding list.
# ═════════════════════════════════════════════════════════════════════

class AttackGraph:
    """Wrapper around networkx.DiGraph with offsec-specific semantics.
    Falls through to no-op stubs if networkx isn't installed so the
    rest of Zeus keeps working."""

    NODE_HOST    = "host"
    NODE_SVC     = "service"
    NODE_CRED    = "credential"
    NODE_HASH    = "hash"
    NODE_VULN    = "vuln"
    NODE_USER    = "user"
    NODE_DOMAIN  = "domain"

    EDGE_RUNS_ON = "runs_on"        # service -> host
    EDGE_WORKS   = "works_on"       # cred -> service
    EDGE_FOR     = "for_user"       # cred -> user
    EDGE_AFFECTS = "affects"        # vuln -> service
    EDGE_PIVOT   = "can_pivot_to"   # host -> host
    EDGE_BELONGS = "in_domain"      # host -> domain

    def __init__(self):
        if HAS_NETWORKX:
            self.g = nx.DiGraph()
        else:
            self.g = None

    def _has(self) -> bool:
        return self.g is not None

    def add_host(self, ip: str, **attrs):
        if not self._has() or not ip:
            return
        self.g.add_node(f"host:{ip}", kind=self.NODE_HOST, label=ip, **attrs)

    def add_service(self, host_ip: str, port: int, name: str = "", version: str = ""):
        if not self._has() or not host_ip:
            return
        sid = f"svc:{host_ip}:{port}"
        self.g.add_node(sid, kind=self.NODE_SVC,
                        label=f"{port}/{name}" if name else str(port),
                        version=version, port=port)
        self.g.add_edge(sid, f"host:{host_ip}", kind=self.EDGE_RUNS_ON)

    def add_credential(self, value: str, user: str = "",
                       host: str = "", verified: bool = False):
        if not self._has() or not value:
            return
        cid = f"cred:{value[:24]}"
        self.g.add_node(cid, kind=self.NODE_CRED, label=value[:32],
                        verified=verified)
        if user:
            uid = f"user:{user}"
            self.g.add_node(uid, kind=self.NODE_USER, label=user)
            self.g.add_edge(cid, uid, kind=self.EDGE_FOR)
        if host:
            self.g.add_edge(cid, f"host:{host}", kind=self.EDGE_WORKS)

    def add_hash(self, value: str, htype: str, user: str = ""):
        if not self._has() or not value:
            return
        hid = f"hash:{value[:16]}"
        self.g.add_node(hid, kind=self.NODE_HASH,
                        label=f"{htype}:{value[:12]}…", htype=htype)
        if user:
            uid = f"user:{user}"
            self.g.add_node(uid, kind=self.NODE_USER, label=user)
            self.g.add_edge(hid, uid, kind=self.EDGE_FOR)

    def add_vuln(self, cve: str, host: str = "", service_port: Optional[int] = None):
        if not self._has() or not cve:
            return
        vid = f"vuln:{cve}"
        self.g.add_node(vid, kind=self.NODE_VULN, label=cve)
        if host and service_port:
            self.g.add_edge(vid, f"svc:{host}:{service_port}", kind=self.EDGE_AFFECTS)
        elif host:
            self.g.add_edge(vid, f"host:{host}", kind=self.EDGE_AFFECTS)

    def mark_cred_verified_on(self, cred_value: str, host: str, port: int):
        if not self._has():
            return
        cid = f"cred:{cred_value[:24]}"
        sid = f"svc:{host}:{port}"
        if cid in self.g and sid in self.g:
            self.g.add_edge(cid, sid, kind=self.EDGE_WORKS, verified=True)

    def auth_services(self) -> List[Tuple[str, int, str]]:
        """Return all auth-able services as (host, port, name)."""
        if not self._has():
            return []
        results = []
        AUTH_PORTS = {21, 22, 23, 80, 110, 143, 389, 443, 445, 1433, 1521,
                      3306, 3389, 5432, 5900, 5985, 5986, 6379, 8080, 8443,
                      9200, 27017}
        for nid, attrs in self.g.nodes(data=True):
            if attrs.get("kind") != self.NODE_SVC:
                continue
            port = attrs.get("port", 0)
            if port in AUTH_PORTS:
                # parse host from nid svc:HOST:PORT
                parts = nid.split(":")
                if len(parts) >= 3:
                    results.append((parts[1], port, attrs.get("label", "")))
        return results

    def cred_fanout_targets(self, cred_value: str) -> List[Tuple[str, int, str]]:
        """For a given credential, return services it hasn't been
        verified-tested against yet."""
        if not self._has():
            return []
        cid = f"cred:{cred_value[:24]}"
        if cid not in self.g:
            return []
        # Edges out of cid that are 'works_on' AND verified=True
        verified_against = set()
        for _, tgt, attrs in self.g.out_edges(cid, data=True):
            if attrs.get("kind") == self.EDGE_WORKS and attrs.get("verified"):
                verified_against.add(tgt)
        # All auth services minus already-verified
        targets = []
        for host, port, name in self.auth_services():
            sid = f"svc:{host}:{port}"
            if sid not in verified_against:
                targets.append((host, port, name))
        return targets

    def pivot_suggestions(self) -> List[str]:
        """Return OSINT-flavoured pivot hints based on graph state.

        The Athena/Ares incarnations of this function suggested
        offensive pivots (untested creds, unexploited CVEs, uncracked
        hashes).  Zeus is OSINT-only and never gathers those node
        kinds in normal operation, so we just surface domain/host
        pivots for OSINT specialists to act on."""
        if not self._has():
            return []
        suggestions: List[str] = []
        # Domains that haven't been wayback-checked
        domains = [a.get("label") for n, a in self.g.nodes(data=True)
                   if a.get("kind") == self.NODE_DOMAIN]
        if domains:
            head = domains[:3]
            suggestions.append(
                f"{len(domains)} domain(s) in graph — consider wayback / "
                f"subfinder pivot ({', '.join(head)})"
            )
        return suggestions

    def summary(self) -> str:
        if not self._has():
            return "Attack graph: networkx not installed (disabled)"
        counts = {}
        for _, attrs in self.g.nodes(data=True):
            k = attrs.get("kind", "unknown")
            counts[k] = counts.get(k, 0) + 1
        parts = [f"{v} {k}{'s' if v != 1 else ''}" for k, v in sorted(counts.items())]
        return f"Attack graph: {len(self.g.nodes)} nodes, {len(self.g.edges)} edges  ({', '.join(parts)})"

    def to_compact_text(self, max_chars: int = 1200) -> str:
        """Compact text representation for prompt injection on demand."""
        if not self._has():
            return "(graph disabled)"
        lines = [self.summary()]
        # Group hosts and their services
        hosts = [(n, a) for n, a in self.g.nodes(data=True)
                 if a.get("kind") == self.NODE_HOST]
        for hid, hattrs in hosts[:8]:
            ip = hattrs.get("label", "?")
            lines.append(f"  HOST {ip}:")
            # Services on this host
            svcs = []
            for src, dst, eattrs in self.g.in_edges(hid, data=True):
                if eattrs.get("kind") == self.EDGE_RUNS_ON:
                    sa = self.g.nodes[src]
                    svcs.append(sa.get("label", "?"))
            if svcs:
                lines.append(f"    services: {', '.join(svcs[:8])}")
        # Pivot suggestions
        sugg = self.pivot_suggestions()
        if sugg:
            lines.append("  PIVOT HINTS:")
            for s in sugg[:5]:
                lines.append(f"    - {s}")
        text = "\n".join(lines)
        return text[:max_chars] + ("..." if len(text) > max_chars else "")


# ═════════════════════════════════════════════════════════════════════
# CONTEXT MANAGER (v7.1) — token-saving smart context
#
# By default each turn ships a MINIMAL system prompt:
#   - active node only (not full PTT)
#   - verified findings (no unverified flood)
#   - last DEFAULT_HISTORY_SLICE turns (not full MAX_HISTORY_MESSAGES)
#   - role-filtered KB (already in v7.0)
#   - tool registry compact form
#
# When the LLM actually needs more, it emits [NEED]target[/NEED] and
# the agent loop re-fetches with that target attached and replays the
# turn.  Targets:
#   [NEED]ptt[/NEED]              full OSINT Task Tree
#   [NEED]history[/NEED]          all 32 turns of history
#   [NEED]findings[/NEED]         verified + unverified findings
#   [NEED]graph[/NEED]            attack-graph compact text + pivots
#   [NEED]kb 5[/NEED]             specific KB section by number
#
# Auto-expansion triggers (no [NEED] required):
#   confidence in {yellow, red}  → expanded slice + ptt + graph
#   stuck_counter > 0            → expanded slice
#   new node entered             → ptt summary
# ═════════════════════════════════════════════════════════════════════

class ContextManager:
    """Decides what slice of state to send each turn.  Stateful so it
    can adapt based on confidence / stuck / new-node signals."""

    def __init__(self):
        self.last_node_id: Optional[str] = None
        self.recent_conf: str = "green"
        self.recent_stuck: int = 0
        self.tokens_saved_estimate: int = 0  # crude rolling estimate

    def signal_node_change(self, nid: Optional[str]):
        if nid != self.last_node_id:
            self.last_node_id = nid

    def signal_confidence(self, conf: str):
        self.recent_conf = conf

    def signal_stuck(self, n: int):
        self.recent_stuck = n

    def history_slice_size(self) -> int:
        """How many history turns to include this turn."""
        if self.recent_conf in ("yellow", "red"):
            return EXPANDED_HISTORY_SLICE
        if self.recent_stuck > 0:
            return EXPANDED_HISTORY_SLICE
        return DEFAULT_HISTORY_SLICE

    def should_attach_full_ptt(self) -> bool:
        return self.recent_conf in ("yellow", "red") or self.recent_stuck > 0

    def should_attach_graph(self) -> bool:
        return self.recent_conf in ("yellow", "red") or self.recent_stuck > 0

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Crude estimate: ~4 chars per token."""
        return max(1, len(text) // 4)

    def record_savings(self, full_size: int, sent_size: int):
        if full_size > sent_size:
            self.tokens_saved_estimate += (full_size - sent_size) // 4


# ═════════════════════════════════════════════════════════════════════
# WORKFLOWS — PTT seeders
#
# Each workflow now constructs an initial PTT for a given engagement
# type, instead of just being a fixed prompt.  Zeus's loop then
# walks the tree, dispatching the right specialist per phase.
# ═════════════════════════════════════════════════════════════════════

WORKFLOWS = {

    "1": {
        "name": "Self-OSINT Footprint",
        "description": "Audit what's findable about yourself — most defensible lane",
        "seed": [
            ("Categorise intake identifiers",                 "intake"),
            ("Username sweep across platforms",                "socialite"),
            ("Email registered-services enumeration",          "postman"),
            ("Domain footprint (if you own a domain)",         "registrar"),
            ("GitHub leaked-secrets scan on own repos",        "dorker"),
            ("Wayback history for own domain",                 "archivist"),
            ("Final consolidated report",                      "reporter"),
        ],
    },

    "2": {
        "name": "Username Pivot",
        "description": "Single handle → cross-platform footprint",
        "seed": [
            ("Sherlock + Maigret across ~400 platforms",       "socialite"),
            ("WhatsMyName API verification",                    "socialite"),
            ("GitHub user details + SSH/GPG keys",              "socialite"),
            ("Reddit + Mastodon + Bluesky lookup",              "socialite"),
            ("Pivot to email if commits / profile reveal one",  "postman"),
            ("Final report",                                    "reporter"),
        ],
    },

    "3": {
        "name": "Email Exposure",
        "description": "Email → which sites + provider fingerprint (no HIBP, paid)",
        "seed": [
            ("holehe registered-services enumeration",          "postman"),
            ("Gravatar md5 lookup",                             "postman"),
            ("Email DNS records (MX, SPF, DMARC)",              "postman"),
            ("GitHub email-search for committed emails",        "postman"),
            ("If custom domain, pivot to domain footprint",     "registrar"),
            ("Final report",                                    "reporter"),
        ],
    },

    "4": {
        "name": "Phone Triage",
        "description": "Number → carrier, country, line type, VoIP yes/no",
        "seed": [
            ("phoneinfoga scan",                                "caller"),
            ("Final report",                                    "reporter"),
        ],
    },

    "5": {
        "name": "Domain Due Diligence",
        "description": "Full domain footprint — WHOIS, DNS, subdomains, certs, archive",
        "seed": [
            ("WHOIS registrar + dates",                         "registrar"),
            ("DNS records (A/AAAA/MX/TXT/NS/CAA)",              "registrar"),
            ("Subdomain enum (subfinder + amass passive)",      "registrar"),
            ("Certificate transparency (crt.sh)",               "registrar"),
            ("ASN + reverse-IP",                                "registrar"),
            ("Tech fingerprint (whatweb passive + headers)",    "registrar"),
            ("Wayback historical endpoints",                    "archivist"),
            ("urlscan.io history",                              "archivist"),
            ("Final report",                                    "reporter"),
        ],
    },

    "6": {
        "name": "Threat-Actor Handle Track",
        "description": "Track an attacker's handles / infrastructure (no real-name pivot)",
        "seed": [
            ("Handle sweep (Sherlock/Maigret)",                 "socialite"),
            ("Public infrastructure check (OTX/ThreatFox)",     "registrar"),
            ("URL/domain reputation (urlhaus, OTX)",            "registrar"),
            ("Wayback / cached presence",                       "archivist"),
            ("Final report",                                    "reporter"),
        ],
    },

    "7": {
        "name": "Bug-Bounty Recon",
        "description": "Authorized program scope — subdomain + leak hunt",
        "seed": [
            ("WHOIS + DNS baseline",                            "registrar"),
            ("Subdomain enum (passive)",                        "registrar"),
            ("Certificate transparency",                        "registrar"),
            ("Tech stack fingerprint",                          "registrar"),
            ("Wayback for old endpoints",                       "archivist"),
            ("urlscan.io + theHarvester",                       "archivist"),
            ("GitHub dork for leaked secrets",                  "dorker"),
            ("Google dork checklist (manual URLs)",             "dorker"),
            ("Final report",                                    "reporter"),
        ],
    },

    "8": {
        "name": "Crypto Address Trace",
        "description": "Public blockchain — balance, txs, clustering hints",
        "seed": [
            ("Address balance + tx count",                      "ledger"),
            ("Recent transactions",                             "ledger"),
            ("Cluster heuristic (common-input ownership)",      "ledger"),
            ("Public attribution check (search address as text)", "dorker"),
            ("Final report",                                    "reporter"),
        ],
    },

    "9": {
        "name": "Image Metadata",
        "description": "EXIF extraction + GPS + camera fingerprint",
        "seed": [
            ("Full exiftool dump",                              "cartographer"),
            ("GPS + datetime + camera fields only",             "cartographer"),
            ("Strip-and-compare (clean version)",               "cartographer"),
            ("Final report",                                    "reporter"),
        ],
    },

    "10": {
        "name": "Wayback History Sweep",
        "description": "Full archive history for a domain or username",
        "seed": [
            ("waybackurls comprehensive pull",                  "archivist"),
            ("gau cross-source pull",                           "archivist"),
            ("Direct archive.org wayback availability",         "archivist"),
            ("urlscan.io history",                              "archivist"),
            ("Final report",                                    "reporter"),
        ],
    },

    "11": {
        "name": "Company Due Diligence",
        "description": "Entity research — corporate filings + domain + officers",
        "seed": [
            ("Domain footprint baseline",                       "registrar"),
            ("Subdomain + cert transparency",                   "registrar"),
            ("Wayback corporate history",                       "archivist"),
            ("urlscan.io corporate web presence",               "archivist"),
            ("Public-records dorking (filings, SEC, court)",    "dorker"),
            ("Final report",                                    "reporter"),
        ],
    },

    "12": {
        "name": "Document Leakage Hunt",
        "description": "Self-OSINT — find your own leaked secrets in public sources",
        "seed": [
            ("GitHub dork for env/config/key leakage",          "dorker"),
            ("Google dork URL set (manual open)",               "dorker"),
            ("Wayback archived leak hunt",                      "archivist"),
            ("crt.sh for forgotten subdomains exposing assets", "registrar"),
            ("Final report",                                    "reporter"),
        ],
    },
}


# ═════════════════════════════════════════════════════════════════════
# CORE RULES embedded in every system prompt
# ═════════════════════════════════════════════════════════════════════

CORE_RULES = (
    "OUTPUT FORMAT (STRICT — emit ONE of either form):\n"
    "  [THOUGHT]<reasoning>[/THOUGHT]\n"
    "  EITHER (preferred for known tools):\n"
    "    [TOOL]<tool_name>[/TOOL][ARGS]<json object of args>[/ARGS]\n"
    "  OR (for ad-hoc commands not in the tool registry):\n"
    "    [CMD]<one shell command, non-interactive>[/CMD]\n"
    "  [CONF]<green|yellow|red>[/CONF]\n"
    "  Optional: [VERIFY]<command to verify a finding>[/VERIFY]\n"
    "  Optional: [HANDOFF]<other agent role>[/HANDOFF]\n"
    "  Optional: [NEED]<ptt|history|findings|graph|kb N>[/NEED]\n"
    "    Use [NEED] when you require more state than the minimal context\n"
    "    provided.  The system will re-call you with that data attached.\n"
    "\n"
    "TOOL FORMAT EXAMPLES:\n"
    '  [TOOL]sherlock_run[/TOOL][ARGS]{"username":"thepriest"}[/ARGS]\n'
    '  [TOOL]holehe_run[/TOOL][ARGS]{"email":"someone@example.com"}[/ARGS]\n'
    '  [TOOL]crt_sh_query[/TOOL][ARGS]{"domain":"example.com"}[/ARGS]\n'
    '  [TOOL]subfinder_passive[/TOOL][ARGS]{"domain":"example.com"}[/ARGS]\n'
    '  [TOOL]github_user_api[/TOOL][ARGS]{"username":"thepriest"}[/ARGS]\n'
    '  [TOOL]exiftool_run[/TOOL][ARGS]{"image_path":"/tmp/photo.jpg"}[/ARGS]\n'
    '  [TOOL]btc_address_balance[/TOOL][ARGS]{"address":"bc1q..."}[/ARGS]\n'
    "\n"
    "WHEN TO USE [TOOL] vs [CMD]:\n"
    " - [TOOL] for any tool listed in the registry.\n"
    " - [CMD] for: custom curl pulls of public APIs, jq filtering of\n"
    "   tool output, in-flight sed/awk parsing, anything not in the registry.\n"
    "\n"
    "ZEUS DISCIPLINE — what makes this LEGAL OSINT:\n"
    " - PUBLIC DATA ONLY.  Every command must access information that\n"
    "   is published, indexed, archived, or in certificate transparency.\n"
    " - NO authentication bypass, NO brute force, NO credential guessing,\n"
    "   NO scraping behind login walls, NO ignoring robots.txt.\n"
    " - NO real-time location, NO home address resolution, NO stalkerware\n"
    "   aggregators (Spokeo, BeenVerified, TruePeopleSearch, etc.).\n"
    " - NO querying credential dumps for actual passwords (DeHashed,\n"
    "   WeLeakInfo, Snusbase, combolists).  HIBP yes/no would be fine\n"
    "   but it's paid now — Zeus skips it entirely.\n"
    " - NO modifying the local system in any way (no sudo, no apt, no\n"
    "   service control, no firewall mutation).\n"
    " - When in doubt — REFUSE and explain in [THOUGHT].\n"
    "\n"
    "AUTONOMOUS-MODE DISCIPLINE:\n"
    " - You run without y/n/q gates.  Don't waste turns asking permission.\n"
    " - When a command fails / times out, log it and move on.  No retries.\n"
    " - When you've exhausted obvious pivots for the current node, mark\n"
    "   it done and let the strategist pick the next node.\n"
    " - Emit WORKFLOW_COMPLETE in [CMD] only when the whole investigation\n"
    "   is done (all branches in done/dead-end OR wall-clock cap reached).\n"
    " - CONF green = high confidence the tool will yield useful data.\n"
    " - CONF yellow = uncertain / probably empty / pivot suggestion.\n"
    " - CONF red = should not run (refused or violates OSINT_REFUSE).\n"
    " - Cite the OSINT category in [THOUGHT] (SOCIAL_PRESENCE,\n"
    "   DOMAIN_FOOTPRINT, METADATA, ARCHIVE_HISTORY, CRYPTO_LEDGER, etc.).\n"
    " - Reason from real subprocess output only — never invent findings."
)


def build_system_prompt(agent_role: str,
                        target_info: Dict[str, Any],
                        ptt: PTT,
                        active_node: Optional[PTTNode],
                        lhost: str,
                        workflow_key: Optional[str] = None,
                        free_form: str = "",
                        context_mgr: Optional["ContextManager"] = None,
                        graph: Optional["AttackGraph"] = None,
                        scope: Optional["ScopeConfig"] = None,
                        force_full: bool = False,
                        need_attachments: Optional[List[str]] = None) -> str:
    """Compose system prompt for the chosen specialist agent.

    v7.1 — minimal context by default, expanded on demand.
    Includes: agent persona + extra rules + KB sections + active node +
    findings summary + Kali tool registry summary + structured tool
    registry + core rules.  When force_full=True or [NEED] tags trigger,
    extra context is attached.
    """
    spec = AGENT_SPECS.get(agent_role, AGENT_SPECS["strategist"])
    need_attachments = need_attachments or []

    # Decide expansion level
    expand_ptt   = force_full or "ptt" in need_attachments
    expand_finds = force_full or "findings" in need_attachments
    expand_graph = force_full or "graph" in need_attachments
    if context_mgr:
        if context_mgr.should_attach_full_ptt():
            expand_ptt = True
        if context_mgr.should_attach_graph():
            expand_graph = True

    # Target block
    target_parts = []
    if target_info.get("ip"):
        target_parts.append(f"Target: {target_info['ip']}")
    if target_info.get("domain"):
        target_parts.append(f"Domain: {target_info['domain']}")
    if target_info.get("notes"):
        target_parts.append(f"Mission: {target_info['notes']}")
    target_block = " | ".join(target_parts) if target_parts else "No target set"

    # Active node context (always present, even in minimal mode)
    node_block = ""
    if active_node:
        node_block = (
            f"CURRENT NODE: [{active_node.nid}] {active_node.title} "
            f"(phase={active_node.phase}, status={active_node.status}, "
            f"attempts={active_node.attempts}, conf={active_node.confidence})"
        )
        if active_node.last_cmd:
            node_block += f"\n  last_cmd: {active_node.last_cmd}"

    # Findings summary — minimal (verified counts) by default
    verified = ptt.get_verified()
    unverified = ptt.get_unverified()

    findings_block = ""
    if expand_finds:
        # Full dump — verified + unverified
        if verified or unverified:
            findings_block = "FINDINGS (FULL):\n"
            if verified:
                v_dict: Dict[str, List[str]] = {}
                for f in verified:
                    v_dict.setdefault(f.ftype, []).append(f.value)
                findings_block += "  VERIFIED:\n"
                for k, vs in v_dict.items():
                    findings_block += f"    {k}: {', '.join(vs[-10:])}\n"
            if unverified:
                u_dict: Dict[str, List[str]] = {}
                for f in unverified:
                    u_dict.setdefault(f.ftype, []).append(f.value)
                findings_block += "  UNVERIFIED (treat as candidates only):\n"
                for k, vs in u_dict.items():
                    findings_block += f"    {k}: {', '.join(vs[-10:])}\n"
    else:
        # Compact — verified only, last 4 per type
        if verified:
            v_dict_c: Dict[str, List[str]] = {}
            for f in verified:
                v_dict_c.setdefault(f.ftype, []).append(f.value)
            findings_block = "VERIFIED FINDINGS:\n"
            for k, vs in v_dict_c.items():
                findings_block += f"  {k}: {', '.join(vs[-4:])}\n"
        if unverified:
            findings_block += f"  ({len(unverified)} unverified — request [NEED]findings[/NEED] if relevant)\n"

    # PTT — minimal (just current branch) or full
    if expand_ptt:
        ptt_block = ptt.to_natural_language(max_chars=2000)
    elif active_node:
        # Just show current node + immediate siblings + parent
        nodes_to_show = {active_node.nid}
        if active_node.parent_id:
            nodes_to_show.add(active_node.parent_id)
            parent = ptt.nodes.get(active_node.parent_id)
            if parent:
                for sib in parent.children:
                    nodes_to_show.add(sib)
        ptt_block_lines = ["PTT (current branch only — request [NEED]ptt[/NEED] for full tree):"]
        for nid in sorted(nodes_to_show):
            n = ptt.nodes.get(nid)
            if n:
                glyph = ptt.STATUS_GLYPH.get(n.status, "?")
                ptt_block_lines.append(f"  {glyph} [{n.nid}] {n.title} ({n.status})")
        ptt_block = "\n".join(ptt_block_lines)
    else:
        ptt_block = "PTT: (empty — set a target first)"

    # Skip directives derived from findings — OSINT-flavoured, not offensive.
    # Suggests next OSINT pivots based on what's already been surfaced.
    skip = []
    fdict = ptt.findings_by_type_dict()
    if fdict.get("email"):
        skip.append("emails surfaced — pivot to holehe / gravatar / MX")
    if fdict.get("handle") or fdict.get("account"):
        skip.append("handles surfaced — pivot to sherlock / maigret across platforms")
    if fdict.get("domain"):
        skip.append("domains surfaced — pivot to whois / subfinder / crt.sh")
    if fdict.get("url"):
        skip.append("URLs surfaced — pivot to wayback / gau / urlscan")
    if fdict.get("phone"):
        skip.append("phones surfaced — pivot to phoneinfoga")
    if fdict.get("crypto_addr"):
        skip.append("crypto addresses surfaced — pivot to public chain queries")
    if fdict.get("image") or fdict.get("exif"):
        skip.append("images surfaced — pivot to exiftool")
    skip_block = ""
    if skip:
        skip_block = "OSINT PIVOT HINTS: " + " | ".join(skip)

    # Attack graph block
    graph_block = ""
    if expand_graph and graph is not None:
        graph_block = "PIVOT GRAPH STATE:\n" + graph.to_compact_text(max_chars=1200)
    elif graph is not None:
        graph_block = f"GRAPH: {graph.summary()}  (request [NEED]graph[/NEED] for paths)"

    # Knowledge base — agent-aware
    kb_text = get_kb_sections(workflow_key=workflow_key,
                              prompt_text=free_form,
                              agent_role=agent_role)

    # Apply [NEED]kb N[/NEED] requests
    for att in need_attachments:
        if att.startswith("kb "):
            try:
                num = int(att.split()[1])
                if num in KB:
                    kb_text += "\n\n" + KB[num]
            except (ValueError, IndexError):
                pass

    # Kali tools available (compact)
    tools_block = kali_tool_summary_for_prompt()

    # NEW: structured tool registry for [TOOL]/[ARGS] format
    structured_block = tool_registry_for_prompt()

    # Scope reminder
    scope_block = ""
    if scope and scope.enabled:
        scope_block = "⚠ ENGAGEMENT SCOPE ENFORCED — out-of-scope commands will be refused."

    parts = [
        f"You are Zeus, an elite LEGAL OSINT AI assistant on Kali NetHunter.",
        f"You aggregate publicly-available intelligence across the open web.",
        f"You access ONLY public data: published, indexed, archived, or in",
        f"certificate transparency.  Never bypass auth.  Never brute-force.",
        f"Never query credential dumps for actual passwords.  Never resolve",
        f"home addresses or real-time location.  Never modify the local system.",
        f"Operator: The Priest.  This host: {lhost}",
        "",
        f"=== ACTIVE AGENT: {spec['icon']} {spec['name']} ===",
        spec["persona"],
        spec["extra_rules"],
        "",
        target_block,
    ]
    if scope_block:
        parts.append(scope_block)
    if node_block:
        parts.append(node_block)
    if findings_block:
        parts.append(findings_block.strip())
    if skip_block:
        parts.append(skip_block)
    parts.append(ptt_block)
    if graph_block:
        parts.append(graph_block)
    parts.append(structured_block)
    parts.append(tools_block)
    parts.append("KNOWLEDGE BASE:\n" + kb_text)
    parts.append(CORE_RULES)
    return "\n\n".join(parts)


# ═════════════════════════════════════════════════════════════════════
# RESPONSE PARSING
# ═════════════════════════════════════════════════════════════════════

def parse_specialist_response(text: str) -> Dict[str, Any]:
    """Extract THOUGHT / CMD / TOOL / ARGS / CONF / VERIFY / HANDOFF /
    NEED from model output.  v7.1: TOOL/ARGS/NEED added."""
    out = {
        "thought":  "",
        "cmd":      None,
        "tool":     None,        # v7.1
        "args":     None,        # v7.1
        "conf":     "green",
        "verify":   None,
        "handoff":  None,
        "need":     [],          # v7.1 — list of attachment requests
    }
    if not text:
        return out

    t = re.search(r'\[THOUGHT\](.*?)\[/?THOUGHT\]', text, re.DOTALL | re.IGNORECASE)
    if t:
        out["thought"] = t.group(1).strip()

    c = re.search(r'\[CMD\](.*?)\[/?CMD\]', text, re.DOTALL | re.IGNORECASE)
    if c:
        out["cmd"] = c.group(1).strip()

    # v7.1 — structured tool dispatch
    tool_m = re.search(r'\[TOOL\]\s*([\w_]+)\s*\[/?TOOL\]', text, re.IGNORECASE)
    if tool_m:
        out["tool"] = tool_m.group(1).strip()
    args_m = re.search(r'\[ARGS\](.*?)\[/?ARGS\]', text, re.DOTALL | re.IGNORECASE)
    if args_m:
        out["args"] = args_m.group(1).strip()

    cf = re.search(r'\[CONF\]\s*(green|yellow|red)\s*\[/?CONF\]',
                   text, re.IGNORECASE)
    if cf:
        out["conf"] = cf.group(1).lower()

    v = re.search(r'\[VERIFY\](.*?)\[/?VERIFY\]', text, re.DOTALL | re.IGNORECASE)
    if v:
        out["verify"] = v.group(1).strip()

    h = re.search(r'\[HANDOFF\]\s*(\w+)\s*\[/?HANDOFF\]', text, re.IGNORECASE)
    if h:
        out["handoff"] = h.group(1).strip().lower()

    # Strategist may emit [AGENT]xxx[/AGENT] — treat as handoff
    if not out["handoff"]:
        a = re.search(r'\[AGENT\]\s*(\w+)\s*\[/?AGENT\]', text, re.IGNORECASE)
        if a:
            out["handoff"] = a.group(1).strip().lower()

    # v7.1 — multiple [NEED] tags allowed in one response
    needs = re.findall(r'\[NEED\]\s*([^\[\]]+?)\s*\[/?NEED\]', text, re.IGNORECASE)
    if needs:
        # Normalise: lowercase, trim, dedup
        seen = set()
        for n in needs:
            n_clean = n.strip().lower()
            if n_clean and n_clean not in seen:
                seen.add(n_clean)
                out["need"].append(n_clean)

    return out


# ═════════════════════════════════════════════════════════════════════
# ZEUS SESSION
# ═════════════════════════════════════════════════════════════════════

class ZeusSession:

    def __init__(self):
        self.target_info: Dict[str, Any] = {}
        self.lhost = "127.0.0.1"
        self.logfile = None
        self.session_start = datetime.datetime.now()
        self.history: List[Dict[str, str]] = []
        self.command_history: List[str] = []
        self.stuck_counter = 0
        self.tools_available: Dict[str, bool] = {}
        self.current_workflow_key: Optional[str] = None
        self.current_agent: str = "strategist"

        # PTT replaces the flat findings dict.
        self.ptt = PTT(goal="Mission undefined")

        # v7.1 — scope, pivot graph, context manager
        self.scope = ScopeConfig.load()
        self.graph = AttackGraph()
        self.context_mgr = ContextManager()

        # v7.1 — credential fanout queue (creds awaiting service tests)
        self.ioc_fanout_queue: List[Tuple[str, str]] = []  # (ioc_value, ftype)

        # v7.1 — track ATT&CK techniques exercised this session
        self.attack_techniques_used: Dict[str, Dict[str, Any]] = {}

        # Provider state
        self.provider_index = 0
        self.groq_client: Optional[Groq] = None

        os.makedirs(INSTALL_DIR, exist_ok=True)
        os.makedirs(LOG_DIR, exist_ok=True)

        self._init_provider()
        self._start_log()
        self._run_boot_check()
        self.lhost = get_lhost()
        ensure_yara_rules()

    # ── Provider init ─────────────────────────────────────────────

    def _init_provider(self):
        groq_key = os.environ.get("GROQ_API_KEY")
        if not groq_key:
            print(
                "\n\033[31m   FATAL: GROQ_API_KEY not set.\033[0m\n"
                "   Add to ~/.bashrc:  export GROQ_API_KEY='your_key'\n"
                "   Then: source ~/.bashrc\n"
            )
            sys.exit(1)
        try:
            self.groq_client = Groq(api_key=groq_key)
        except Exception as e:
            print(f"\033[31m   FATAL: Groq init: {e}\033[0m")
            sys.exit(1)
        first = PROVIDER_CHAIN[0]
        print(f"\033[32m   ✅ Groq client OK\033[0m")
        print(f"\033[32m   Active model: {first[1]}\033[0m")

    # ── Logging ───────────────────────────────────────────────────

    def _start_log(self):
        ts = self.session_start.strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(LOG_DIR, f"session_{ts}.txt")
        try:
            self.logfile = open(log_path, "w")
            self.logfile.write(
                f"ZEUS v{VERSION} LOG\n"
                f"Started: {self.session_start.isoformat()}\n"
                f"{'='*64}\n\n"
            )
            self.logfile.flush()
            print(f"\033[90m   Log: {log_path}\033[0m")
        except Exception as e:
            print(f"\033[33m   Log open failed: {e}\033[0m")

    def _log(self, text: str):
        if self.logfile:
            try:
                clean = re.sub(r'\033\[[0-9;]*m', '', text)
                self.logfile.write(clean + "\n")
                self.logfile.flush()
            except Exception:
                pass

    def _run_boot_check(self):
        # v7.2 — auto-expire the boot lock after BOOT_LOCK_TTL_SECONDS
        try:
            if os.path.exists(BOOT_LOCK):
                age = time.time() - os.path.getmtime(BOOT_LOCK)
                if age < BOOT_LOCK_TTL_SECONDS:
                    return
        except Exception:
            pass

        print()
        say_zeus("Boot check…")

        # Pull upgradable list (best-effort; non-fatal if apt unavailable)
        try:
            result = subprocess.run(
                "apt list --upgradable 2>/dev/null",
                shell=True, capture_output=True, text=True, timeout=15
            )
            upgrades = result.stdout.lower()
        except Exception:
            upgrades = ""

        # v7.2 — only flag a UI-package upgrade as a "threat" if the
        # package is ACTUALLY INSTALLED on this system.  Substring
        # matching alone produces false positives like 'xfce' matching
        # 'xfce4-something' on a phone where xfce was never installed.
        confirmed_threats: List[str] = []
        for p in BANNED_UPGRADE_PACKAGES:
            if p not in upgrades:
                continue
            try:
                # dpkg-query returns rc 0 when at least one matching
                # package is installed (state starts with 'i').
                check = subprocess.run(
                    f"dpkg-query -W -f='${{Status}}\\n' '{p}*' 2>/dev/null "
                    f"| grep -q '^install ok installed'",
                    shell=True, timeout=5
                )
                if check.returncode == 0:
                    confirmed_threats.append(p)
            except Exception:
                # If dpkg-query failed for some reason, be conservative
                # and DON'T flag — better than false alarms.
                continue

        if confirmed_threats:
            say_warn(f"UI threat blocked: {', '.join(confirmed_threats)} "
                     f"have upgrades pending — apt upgrade is banned.")
        else:
            say_ok("System OK")

        try:
            with open(BOOT_LOCK, "w") as f:
                f.write(f"ok {datetime.datetime.now().isoformat()}")
        except Exception:
            pass

    # ── Provider call & fallback chain ────────────────────────────

    def _call_provider(self, messages: list, model: str,
                       max_tokens: int = MAX_TOKENS_DEFAULT) -> str:
        completion = self.groq_client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return completion.choices[0].message.content

    def _think_with_fallback(self, messages: list,
                             max_tokens: int = MAX_TOKENS_DEFAULT) -> Optional[str]:
        start_index = self.provider_index
        last_error = None

        for attempt in range(len(PROVIDER_CHAIN)):
            idx = (start_index + attempt) % len(PROVIDER_CHAIN)
            model_id, model_name = PROVIDER_CHAIN[idx]

            try:
                response = self._call_provider(messages, model_id, max_tokens)
                if idx != self.provider_index:
                    self.provider_index = idx
                    print(f"\n\033[33m   ↪ Switched to: {model_name}\033[0m")
                return response

            except Exception as e:
                last_error = e
                err = str(e).lower()
                is_limit = any(x in err for x in [
                    "rate", "limit", "429", "quota",
                    "too many", "queue", "capacity"
                ])
                is_404 = "404" in err or "not_found" in err or "does not exist" in err
                is_cf = "cloudflare" in err

                if is_404:
                    print(f"\033[33m   {model_name} unavailable — skipping\033[0m")
                    continue
                elif is_limit:
                    print(f"\n\033[33m   {model_name} rate-limited — falling to next\033[0m")
                    continue
                elif is_cf:
                    print(f"\n\033[33m   {model_name} blocked by CF — next\033[0m")
                    continue
                else:
                    short = err[:100]
                    print(f"\n\033[31m   {model_name} error: {short}\033[0m")
                    continue

        print(f"\n\033[31m   ⚠  All providers exhausted: {last_error}\033[0m")
        return None

    def _current_model_name(self) -> str:
        if 0 <= self.provider_index < len(PROVIDER_CHAIN):
            return PROVIDER_CHAIN[self.provider_index][1]
        return "Unknown"

    # ── Target setup ──────────────────────────────────────────────

    def set_target(self):
        """Backwards-compatibility shim — Zeus uses extensive_intake instead."""
        return self.extensive_intake()

    def extensive_intake(self):
        """Front-loaded identifier form.  All operator input collected
        once, then Zeus runs autonomously to completion.
        """
        # ── Phase 1: lane gate ────────────────────────────────────
        print()
        say_zeus("OSINT INTAKE — answer what you have, blank-line to skip.")
        print()
        print("\033[33m   ⚠  Zeus is read-only public-data OSINT only.\033[0m")
        print("\033[33m   ⚠  It refuses anything that bypasses authentication,\033[0m")
        print("\033[33m   ⚠  brute-forces, touches stolen credential dumps,\033[0m")
        print("\033[33m   ⚠  asks for real-time location or street-level home\033[0m")
        print("\033[33m   ⚠  addresses, scrapes non-public data, or modifies\033[0m")
        print("\033[33m   ⚠  the local system.  All findings stay in RAM and\033[0m")
        print("\033[33m   ⚠  vanish when you exit.\033[0m")
        print()

        lanes = [
            ("self-osint",     "audit your own digital footprint"),
            ("threat-actor",   "track adversary handles + infrastructure"),
            ("journalism",     "public-interest investigation, logged"),
            ("due-diligence",  "entity / company focused"),
            ("bug-bounty",     "authorized program, scope file required"),
            ("training",       "CTF / known-test-target"),
        ]
        print("\033[36m   ─── Investigation lane ───\033[0m")
        for i, (k, desc) in enumerate(lanes, 1):
            print(f"   {i}. \033[97m{k:<14}\033[0m  \033[90m{desc}\033[0m")
        try:
            ch = input("\n   Choose lane [1-6]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        try:
            lane = lanes[int(ch) - 1][0]
        except (ValueError, IndexError):
            say_err("Invalid lane.  Aborting intake.")
            return False
        print(f"\033[32m   ✓ lane: {lane}\033[0m")

        # ── Phase 2: subject type ─────────────────────────────────
        print()
        subject_types = [
            ("person",          "individual human (self-osint or public figure)"),
            ("company",         "legal entity, business, organisation"),
            ("domain",          "web property only"),
            ("crypto-address",  "BTC / ETH / SOL / etc. wallet address"),
            ("threat-actor",    "adversary handle / infrastructure cluster"),
        ]
        print("\033[36m   ─── Subject type ───\033[0m")
        for i, (k, desc) in enumerate(subject_types, 1):
            print(f"   {i}. \033[97m{k:<14}\033[0m  \033[90m{desc}\033[0m")
        try:
            ch = input("\n   Choose subject type [1-5]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        try:
            subject_type = subject_types[int(ch) - 1][0]
        except (ValueError, IndexError):
            say_err("Invalid subject type.  Aborting intake.")
            return False
        print(f"\033[32m   ✓ subject_type: {subject_type}\033[0m")

        # Lane × subject sanity check
        if lane == "self-osint" and subject_type not in ("person",):
            say_err("self-osint lane only allows subject_type=person.")
            return False
        if lane == "due-diligence" and subject_type == "person":
            print("\033[33m   ⚠  Due-diligence on a person — confirm public-interest "
                  "justification:\033[0m")
            try:
                why = input("   Justification (logged): ").strip()
            except (EOFError, KeyboardInterrupt):
                return False
            if not why or len(why) < 8:
                say_err("Insufficient justification.  Aborting.")
                return False

        # ── Phase 3: identifier intake (per subject_type) ─────────
        print()
        print(f"\033[36m   ─── Identifiers (subject = {subject_type}) ───\033[0m")
        print("\033[90m   Multi-line fields: paste/type one per line, blank line to end.\033[0m")
        print()

        ident: Dict[str, Any] = {}

        def _multiline(prompt_label: str) -> List[str]:
            print(f"   \033[97m{prompt_label}\033[0m  \033[90m(blank to finish)\033[0m")
            out: List[str] = []
            while True:
                try:
                    line = input("     • ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    return out
                if not line:
                    return out
                out.append(line)

        def _single(prompt_label: str) -> str:
            try:
                return input(f"   \033[97m{prompt_label}\033[0m  ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return ""

        if subject_type == "person":
            ident["real_name"] = _single("Name (real name or alias):")
            ident["aliases"]   = _multiline("Other names / nicknames they use:")
            ident["emails"]    = _multiline("Email addresses:")
            ident["phones"]    = _multiline("Phone numbers:")
            ident["handles"]   = _multiline(
                "Usernames (one per line — bare handles like 'thepriest' or "
                "'platform:handle' format if you know the platform):"
            )
            ident["urls"]      = _multiline("Profile URLs / websites:")
            ident["region"]    = _single("Country or city (NEVER street):")
            ident["images"]    = _multiline("Image file paths (for EXIF):")
            ident["notes"]     = _single("Anything else worth noting:")

        elif subject_type == "company":
            ident["legal_name"]   = _single("Legal name:")
            ident["dbas"]         = _multiline("Trading names / DBAs:")
            ident["domains"]      = _multiline("Primary domain(s):")
            ident["country"]      = _single("Country of registration:")
            ident["ticker"]       = _single("Stock ticker (if public):")
            ident["industry"]     = _single("Industry:")
            ident["executives"]   = _multiline("Known executives:")
            ident["subsidiaries"] = _multiline("Subsidiaries:")
            ident["notes"]        = _single("Free-text notes:")

        elif subject_type == "domain":
            ident["primary"]      = _single("Primary domain:")
            ident["subdomains"]   = _multiline("Known subdomains:")
            ident["tech_hints"]   = _single("Tech stack hints:")
            ident["asn"]          = _single("ASN if known:")
            ident["notes"]        = _single("Free-text notes:")

        elif subject_type == "crypto-address":
            ident["addresses"]    = _multiline("Address(es):")
            ident["network"]      = _single("Network (BTC/ETH/SOL/etc.):")
            ident["notes"]        = _single("Free-text notes:")

        elif subject_type == "threat-actor":
            ident["handles"]      = _multiline(
                "Handles in 'platform:handle' format:"
            )
            ident["infra"]        = _multiline("Known infrastructure (domains/IPs):")
            ident["malware_fam"]  = _single("Attributed malware family:")
            ident["intel_source"] = _single("Threat-intel source feed:")
            ident["notes"]        = _single("Free-text notes:")

        # Sanity: at least one identifier must exist
        non_empty = [k for k, v in ident.items() if v]
        if not non_empty:
            say_err("No identifiers provided.  Zeus needs at least one seed.")
            return False

        self.target_info = {
            "lane":         lane,
            "subject_type": subject_type,
            "identifiers":  ident,
            # Compatibility shims for the existing scope/log machinery
            "ip":     None,
            "domain": (ident.get("primary") or
                       (ident.get("domains") or [None])[0] or
                       (ident.get("urls") or [None])[0]),
            "notes":  ident.get("notes") or "",
        }

        # Build the OTT root
        seed_label = (
            ident.get("real_name") or ident.get("legal_name") or
            ident.get("primary") or
            (ident.get("addresses") or [None])[0] or
            (ident.get("handles") or [None])[0] or
            "(unnamed subject)"
        )
        goal = f"OSINT investigation [{lane}]: {seed_label}"
        self.ptt = PTT(goal=goal)

        print()
        print(f"\033[32m   ✓ intake complete — {len(non_empty)} identifier "
              f"category/ies provided\033[0m")
        self._log(f"[INTAKE] lane={lane} subject={subject_type} "
                  f"seeds={non_empty}")
        return True

    # ── OSINT refuse-list gate (universal — runs on every command) ──
    def _is_osint_refused(self, cmd: str) -> Tuple[bool, str]:
        """Check command against OSINT_REFUSE_PATTERNS.  Returns
        (refused, reason)."""
        try:
            patterns = OSINT_REFUSE_PATTERNS  # type: ignore[name-defined]
        except NameError:
            return (False, "")
        for pat in patterns:
            try:
                m = re.search(pat, cmd, re.IGNORECASE)
            except re.error:
                continue
            if m:
                return (True, f"matches OSINT refuse pattern: {pat}")
        return (False, "")

    # ── Command safety gates (carried from v6.1, unchanged) ──────

    def _is_banned(self, cmd: str) -> bool:
        return any(b in cmd.lower() for b in BANNED_COMMANDS)

    def _is_interactive(self, cmd: str) -> Tuple[bool, str]:
        cmd_lower = cmd.lower().strip()
        non_interactive_markers = [
            " -q -r ", " -batch ", " --batch", " -e '", " -c '",
            "sshpass", "<<EOF", "<<<", " -y ", "expect ",
        ]
        if any(m in cmd for m in non_interactive_markers):
            return (False, "")
        for trigger, fix in INTERACTIVE_BLOCKED.items():
            if (cmd_lower.startswith(trigger) or
                f" {trigger}" in cmd_lower or
                f"&& {trigger}" in cmd_lower or
                f"; {trigger}" in cmd_lower):
                # msfconsole with -q -r is fine
                if trigger == "msfconsole" and (" -q -r " in cmd or " -q -x " in cmd):
                    return (False, "")
                return (True, fix)
        return (False, "")

    def _is_destructive(self, cmd: str) -> bool:
        for pattern in DESTRUCTIVE_COMMANDS:
            if re.search(pattern, cmd):
                return True
        return False

    def _needs_double_confirm(self, cmd: str) -> bool:
        for pattern in DOUBLE_CONFIRM:
            if re.search(pattern, cmd):
                return True
        return False

    def _normalize_choice(self, choice: str) -> str:
        c = choice.strip().lower()
        if c in ("y", "yes", "1y", "yy", "yeah", "yep", "ye"):
            return "y"
        if c in ("n", "no", "skip", "nope"):
            return "n"
        if c in ("q", "quit", "exit", "stop"):
            return "q"
        return c

    # ── Command execution (with full y/n gate) ────────────────────

    def _sync_graph_from_recent_findings(self, last_n: int):
        """Push the most recent N findings into the pivot graph."""
        if not self.graph._has() or last_n <= 0:
            return
        for f in self.ptt.findings[-last_n:]:
            try:
                if f.ftype == "ip":
                    self.graph.add_host(f.value)
                elif f.ftype == "port":
                    # Port findings often lack host context — try to
                    # associate with the most recently discovered host
                    hosts = [g.value for g in self.ptt.findings if g.ftype == "ip"]
                    host = hosts[-1] if hosts else (
                        self.target_info.get("ip") or "unknown")
                    try:
                        self.graph.add_service(host, int(f.value))
                    except ValueError:
                        pass
                elif f.ftype == "svc":
                    hosts = [g.value for g in self.ptt.findings if g.ftype == "ip"]
                    host = hosts[-1] if hosts else (
                        self.target_info.get("ip") or "unknown")
                    self.graph.add_service(host, 0, name=f.value, version=f.value)
                elif f.ftype == "account":
                    # Account anomalies attach to the host being audited
                    self.graph.add_credential(f.value, user=f.value,
                                              verified=f.verified)
                elif f.ftype == "hash":
                    # Defender side: a hash is an IOC pivot
                    self.graph.add_hash(f.value, htype="ioc")
                elif f.ftype == "cve":
                    host = self.target_info.get("ip") or ""
                    self.graph.add_vuln(f.value, host=host)
                elif f.ftype in ("yara_hit", "av_hit", "suricata_alert"):
                    # Treat as an IOC node tied to the host
                    self.graph.add_hash(f.value[:32], htype=f.ftype)
                elif f.ftype == "domain":
                    pass  # domain nodes optional
            except Exception:
                continue

    def _flush_cred_fanout(self):
        """No-op for Zeus.

        Inherited from Ares (defensive) where IOCs got fanned out into
        local-host log/pcap sweeps.  Zeus is an OSINT aggregator —
        new identifiers should be pivoted OUT to public sources by the
        appropriate specialist, not swept INWARD across /var/log.

        The fanout queue is still populated by add_finding() but Zeus
        ignores it.  Specialists pick up new identifiers via the OTT
        directly when the strategist routes to them."""
        # Drain the queue so memory doesn't grow unbounded, but don't
        # spawn any sweep nodes.
        self.ioc_fanout_queue.clear()

    # Zeus runs OSINT tools — none need root.  All sudo machinery is
    # neutralised via stubs so the run_command call sites still work.
    _sudo_password: Optional[str] = None
    _sudo_skip_session: bool = True  # always opted out — never prompts

    def _command_needs_sudo(self, cmd: str) -> bool:
        """Zeus refuses sudo entirely — OSINT doesn't need it."""
        return False

    def _needs_sudo_retry(self, output: str) -> bool:
        return False

    def _prime_sudo(self) -> bool:
        return False

    def _sudo_test(self) -> bool:
        return False

    def _wrap_sudo_with_password(self, cmd: str) -> str:
        return cmd

    def run_command(self, cmd: str, label: str = "EXEC") -> str:
        if self._is_destructive(cmd):
            print()
            print(error_alert(
                "DESTRUCTIVE COMMAND REFUSED", cmd,
                hint="Zeus will not run anything that wipes data, "
                     "kills the system, or creates fork bombs."))
            self._log(f"[DESTRUCTIVE REFUSED] {cmd}")
            return EXEC_DESTRUCTIVE

        # OSINT refusal gate — illegal-OSINT keyword/pattern check
        refused, why = self._is_osint_refused(cmd)
        if refused:
            print()
            print(error_alert(
                "OSINT REFUSE-LIST — REFUSED",
                f"{cmd}\n\nReason: {why}",
                hint="Zeus is read-only public-data OSINT only.  "
                     "Anything that bypasses auth, brute-forces, "
                     "touches stolen data, or stalks people is refused "
                     "by design — no exceptions, no overrides."))
            self._log(f"[OSINT REFUSED] {cmd} -- {why}")
            return EXEC_REJECTED

        # Scope / RoE check (Zeus uses lane gate not RoE; usually no-op)
        target_hint = (self.target_info.get("ip") or
                       self.target_info.get("domain") or "")
        scope_ok, scope_reason = self.scope.check(cmd, target_hint=target_hint)
        if not scope_ok:
            print()
            print(error_alert(
                "OUT OF SCOPE — REFUSED",
                f"{cmd}\n\nReason: {scope_reason}",
                hint=f"Lane / scope mismatch — refusing."))
            self._log(f"[OUT-OF-SCOPE] {cmd} -- {scope_reason}")
            return EXEC_REJECTED

        is_interactive, fix = self._is_interactive(cmd)
        if is_interactive:
            print()
            print(error_alert(
                "INTERACTIVE COMMAND BLOCKED", cmd,
                hint=f"Fix: {fix}"))
            self._log(f"[INTERACTIVE BLOCKED] {cmd}")
            return EXEC_INTERACTIVE_BLOCKED

        # OSINT-category pre-tag for the command itself
        attack_tag = attack_id_for_command(cmd)
        attack_label = ""
        if attack_tag:
            tid, tname, tactic = attack_tag
            attack_label = f"  \033[36m▸ {tid} {tname}\033[0m"
            if tid not in self.attack_techniques_used:
                self.attack_techniques_used[tid] = {
                    "name": tname, "tactic": tactic, "count": 0, "commands": []
                }
            self.attack_techniques_used[tid]["count"] += 1
            self.attack_techniques_used[tid]["commands"].append(cmd[:120])

        # Boxed command card
        is_verify = (label == "VERIFY")
        att_id = attack_tag[0] if attack_tag else ""
        att_name = attack_tag[1] if attack_tag else ""
        active_for_conf = self.ptt.find_in_progress()
        conf = active_for_conf.confidence if active_for_conf else "green"
        if active_for_conf and active_for_conf.attempts >= 2 and conf == "green":
            conf = "yellow"
        if active_for_conf and active_for_conf.attempts >= NODE_ATTEMPT_LIMIT - 1:
            conf = "red"
        print()
        print(command_card(cmd, conf=conf, attack_id=att_id,
                           attack_name=att_name, verify=is_verify))
        print()
        self._log(f"\n[CMD-{label}]{' '+att_id if att_id else ''}\n{cmd}")

        # AUTO_MODE — Zeus runs autonomously, no y/n/q gate.
        # Human gating only kicks in if AUTO_MODE has been disabled in
        # config (operator wanted manual confirmation).
        if AUTO_MODE:
            print(f"   \033[90m▸ auto-execute (AUTO_MODE=on)\033[0m")
        else:
            try:
                raw = input(
                    f"   {kbd('y')} run   {kbd('n')} skip   {kbd('q')} quit  › "
                )
            except (EOFError, KeyboardInterrupt):
                print()
                return EXEC_SESSION_EXIT

            choice = self._normalize_choice(raw)
            if choice == "q":
                return EXEC_SESSION_EXIT
            if choice != "y":
                print("\033[90m   Skipped.\033[0m")
                self._log("[SKIPPED]")
                return EXEC_REJECTED

        # Double-confirm list is empty for Zeus (no system mods).  If
        # somehow a pattern matched, refuse rather than confirm.
        if self._needs_double_confirm(cmd):
            print()
            print(error_alert(
                "WOULD MUTATE SYSTEM — REFUSED",
                cmd,
                hint="Zeus is read-only.  This was a defence-in-depth catch."))
            self._log("[SYSTEM MUTATION REFUSED]")
            return EXEC_REJECTED

        # No sudo handling — Zeus' sudo stubs make these no-ops
        actual_cmd = cmd
        sudo_pw_input: Optional[str] = None

        # Pick a timeout based on the command pattern
        cmd_timeout = DEFAULT_COMMAND_TIMEOUT
        for pat, t in COMMAND_TIMEOUTS:
            if re.search(pat, cmd, re.IGNORECASE):
                cmd_timeout = t
                break

        print()
        print(f"   \033[100m\033[97m\033[1m  ▶ EXECUTING  \033[0m  "
              f"\033[90m\033[3mtimeout={cmd_timeout}s · "
              f"Ctrl+C aborts this command only\033[0m\n")
        output_lines = []
        proc = None
        timed_out = False
        is_exploit = False  # Zeus has no exploit concept

        try:
            # If sudo password is needed, we have to feed it via stdin.
            # Otherwise we use stdin=DEVNULL so commands that read stdin
            # (e.g. ssh) fail fast instead of hanging forever.
            popen_kwargs = dict(
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                preexec_fn=os.setsid,
            )
            if sudo_pw_input is not None:
                popen_kwargs["stdin"] = subprocess.PIPE
            else:
                popen_kwargs["stdin"] = subprocess.DEVNULL

            proc = subprocess.Popen(actual_cmd, **popen_kwargs)
            if sudo_pw_input is not None and proc.stdin:
                try:
                    proc.stdin.write(sudo_pw_input)
                    proc.stdin.flush()
                    proc.stdin.close()
                except Exception:
                    pass

            # v7.2 — non-blocking read loop bounded by cmd_timeout.
            start_t = time.time()
            for line in iter(proc.stdout.readline, ""):
                # Strip the password-prompt line if it leaks through stderr
                if line.strip().startswith("[sudo] password for"):
                    continue
                print(line, end="")
                output_lines.append(line)
                if (time.time() - start_t) > cmd_timeout:
                    timed_out = True
                    break
            if timed_out:
                # Kill the process group cleanly
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                output_lines.append(f"\n[COMMAND TIMED OUT after {cmd_timeout}s — killed]\n")
                print(f"\n\033[31m   ⏱  Command timed out at {cmd_timeout}s "
                      f"and was killed.\033[0m")
            else:
                proc.wait()
        except KeyboardInterrupt:
            print("\n\033[33m   Command aborted by user — returning to Zeus\033[0m")
            if proc:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            output_lines.append("\n[COMMAND ABORTED BY USER]\n")
        except Exception as e:
            err = f"EXECUTION ERROR: {e}"
            print(f"\033[31m{err}\033[0m")
            return err

        raw_output = "".join(output_lines)
        rc = proc.returncode if proc else -1

        # v7.2 — if command failed with a permissions/raw-socket marker
        # AND wasn't already wrapped in sudo, offer a one-tap retry.
        if (rc != 0 and not self._command_needs_sudo(cmd)
                and self._needs_sudo_retry(raw_output)
                and not self._sudo_skip_session):
            print()
            print(error_alert(
                "PERMISSION DENIED — needs root",
                f"`{cmd[:160]}` failed without sudo.",
                hint="Press y to re-run prefixed with sudo (one-time, "
                     "uses cached password)."))
            try:
                ans = input(f"   {kbd('y')} retry as sudo   {kbd('n')} keep failure  › ")
            except (EOFError, KeyboardInterrupt):
                ans = "n"
            if self._normalize_choice(ans) == "y":
                # Recursively run the sudo-prefixed version through the
                # same gate.  We tag the label so the agent loop knows
                # this isn't a fresh proposal.
                say_sys("retrying with sudo prefix…", color="33")
                return self.run_command("sudo " + cmd, label=label + "-SUDO")

        # Auto-CVE lookup on recon-type commands
        if any(kw in cmd for kw in ["nmap", "whatweb", "smbclient",
                                      "nikto", "searchsploit", "nuclei",
                                      "nxc ", "crackmapexec"]):
            cve_extra = auto_cve_lookup(raw_output)
            if cve_extra:
                print(cve_extra)
                # Add to context, but DON'T parse this as findings
                # (those CVEs already came from real output)

        # ZEUS: removed Ares' auto-exploit / defensive-triage suggestion
        # path here.  CVE strings surfacing in OSINT output (e.g. in a
        # bug-bounty scope file or on a security blog) are leads, not
        # triggers for sigma rule lookups or apt-upgrade suggestions.
        # The reporter agent will surface them in the final report.

        self._log(f"[OUTPUT]\n{raw_output}")

        # Source-tagged finding extraction — ONLY on raw subprocess output
        active = self.ptt.find_in_progress()
        active_id = active.nid if active else self.ptt.root_id
        findings_before = len(self.ptt.findings)
        new_count = extract_findings_from_stdout(
            raw_output, source_cmd=cmd, ptt=self.ptt,
            active_node_id=active_id,
        )
        if new_count > 0:
            # v7.2 — boxed findings card with the actual extracted values
            new_findings = self.ptt.findings[findings_before:]
            items = []
            for f in new_findings:
                icon_map = {
                    "ip": "🌐", "port": "🔌", "user": "👤",
                    "hash": "🔐", "hash_ntlm": "🔐", "krb_hash": "🎫",
                    "ntlmv2": "🔐", "cred": "🔑", "cve": "💥",
                    "svc": "⚙", "domain": "🏷", "url": "🔗",
                    "exposed_path": "⚠", "smb_share": "📂",
                    "email": "📧", "ssh_key": "🗝", "aws_key": "☁",
                }
                icon = icon_map.get(f.ftype, "•")
                tag = f" \033[36m{f.attack_id}\033[0m" if f.attack_id else ""
                items.append(
                    f"{icon}  \033[97m{f.ftype:<12}\033[0m "
                    f"\033[36m{f.value[:42]}\033[0m{tag}"
                )
            print()
            print(findings_card(new_count, items))
            # Feed new findings into pivot graph
            self._sync_graph_from_recent_findings(new_count)
            # Defensive fanout: when an IOC (suspicious hash, alert,
            # YARA hit, suricata alert, persistence artifact, sus IP)
            # lands, queue it so the threat hunter sweeps every other
            # relevant source for the same indicator.
            IOC_FANOUT_TYPES = {
                "hash", "yara_hit", "av_hit", "suricata_alert",
                "persistence", "suspicious_proc", "ip", "domain", "url",
                "attack_id",
            }
            for f in self.ptt.findings[-new_count:]:
                if (f.ftype in IOC_FANOUT_TYPES and
                    (f.value, f.ftype) not in self.ioc_fanout_queue):
                    self.ioc_fanout_queue.append((f.value, f.ftype))
                    print(f"\033[33m   ↳ IOC queued for fanout: "
                          f"{f.ftype} = {f.value[:40]}\033[0m")

        # Compress for AI context
        compressed = compress_output_for_history(
            raw_output, is_exploit_result=is_exploit
        )
        if (len(raw_output) > 1000 and
            len(compressed) < len(raw_output) * 0.5):
            print(f"\033[90m   [output compressed: "
                  f"{len(raw_output)}→{len(compressed)} chars for AI]\033[0m")

        return compressed.strip() or "(no output)"

    # ── Verification command (PoC validation) ────────────────────

    def attempt_verification(self, verify_cmd: str,
                             finding_value: str,
                             finding_type: str) -> bool:
        """Run a verify-tagged command through the y/n gate.
        On success (zero exit AND useful output), promote the finding
        to verified=True in the PTT.

        Per operator instruction: ALWAYS goes through y/n gate.
        """
        print()
        print(_box(
            "PoC VERIFICATION",
            [f"  Claim: \033[97m{finding_type}={finding_value[:48]}\033[0m",
             f"  Verifier will attempt to confirm this is real."],
            color="31"))

        result = self.run_command(verify_cmd, label="VERIFY")
        if result in (EXEC_REJECTED, EXEC_DESTRUCTIVE,
                      EXEC_INTERACTIVE_BLOCKED, EXEC_SESSION_EXIT):
            return result == EXEC_SESSION_EXIT and False or False

        # Heuristic: verify command output should NOT contain auth-failure
        # markers and SHOULD be non-empty.
        result_lower = result.lower()
        fail_markers = [
            "permission denied", "authentication failed", "access denied",
            "login incorrect", "invalid", "401", "403", "unauthorized",
            "could not connect", "connection refused", "connection timed",
            "not found", "no such", "command not found",
        ]
        if any(m in result_lower for m in fail_markers):
            print()
            print(_box(
                "✗ VERIFICATION FAILED",
                [f"  {finding_type}={finding_value[:48]} stays unverified"],
                color="31"))
            return False

        if not result.strip() or result.strip() == "(no output)":
            print()
            print(_box(
                "? VERIFICATION INCONCLUSIVE",
                ["  Empty output. Try a different verifier."],
                color="33"))
            return False

        # Promote finding to verified
        for f in self.ptt.findings:
            if f.ftype == finding_type and f.value == finding_value:
                f.verified = True
                f.notes = f"Verified by: {verify_cmd[:120]}"
                print(f"\033[32m   ✓ VERIFIED — "
                      f"{finding_type}={finding_value} confirmed real\033[0m")
                # v7.1 — sync to pivot graph
                if finding_type == "cred" and self.graph._has():
                    # Try to extract host:port from verify_cmd
                    host_match = re.search(
                        r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b', verify_cmd)
                    port_match = re.search(r':(\d{2,5})\b', verify_cmd)
                    if host_match:
                        host = host_match.group(1)
                        port = int(port_match.group(1)) if port_match else 0
                        self.graph.mark_cred_verified_on(finding_value, host, port)
                return True

        return False

    # ── Specialist agent dispatch ────────────────────────────────

    def _select_agent(self, node: Optional[PTTNode],
                      free_form: str = "") -> str:
        """Deterministic dispatcher: OTT node phase → specialist role.

        For Zeus this is mostly a passthrough — the strategist handles
        routing by emitting [HANDOFF].  We only consult phase mappings
        when there's no active handoff in progress.
        """
        # Honour an explicit handoff that was set on the previous turn.
        forced = getattr(self, "_forced_next_agent", None)
        if forced and forced in AGENT_SPECS:
            self._forced_next_agent = None
            return forced

        # OTT phase mapping
        if node and node.phase in PHASE_TO_AGENT:
            return PHASE_TO_AGENT[node.phase]

        # Free-form keyword scan — Zeus OSINT specialists only
        lower = (free_form or "").lower()
        if any(k in lower for k in ["sherlock", "maigret", "whatsmyname",
                                     "username", "handle", "platform",
                                     "github user", "reddit user",
                                     "mastodon", "bluesky"]):
            return "socialite"
        if any(k in lower for k in ["holehe", "gravatar", "email triage",
                                     "mx record", "spf", "dmarc",
                                     "pgp keyserver"]):
            return "postman"
        if any(k in lower for k in ["phoneinfoga", "phone osint",
                                     "carrier lookup", "line type"]):
            return "caller"
        if any(k in lower for k in ["whois", "subfinder", "amass",
                                     "crt.sh", "subdomain", "domain footprint",
                                     "asn", "dns record"]):
            return "registrar"
        if any(k in lower for k in ["exiftool", "exif", "image metadata",
                                     "gps coord", "camera serial"]):
            return "cartographer"
        if any(k in lower for k in ["wayback", "archive.org", "gau",
                                     "waybackurls", "snapshot",
                                     "deleted content"]):
            return "archivist"
        if any(k in lower for k in ["google dork", "github dork",
                                     "secret hunt", "leak hunt"]):
            return "dorker"
        if any(k in lower for k in ["btc address", "eth address",
                                     "blockchain", "wallet", "crypto",
                                     "ens reverse"]):
            return "ledger"
        if any(k in lower for k in ["report", "summary", "writeup",
                                     "consolidate", "executive"]):
            return "reporter"
        # Default: strategist routes
        return "strategist"

    # ── Two-pass thinking turn ───────────────────────────────────

    def think_turn(self, prompt: str,
                   workflow_key: Optional[str] = None) -> Dict[str, Any]:
        """Single specialist turn.

        Picks specialist agent based on current PTT node, builds the
        appropriate system prompt, calls the LLM with fallback chain,
        parses the response.

        v7.1: handles [TOOL]/[ARGS] dispatch through ToolBuilder, and
        [NEED] tags trigger up to MAX_NEED_FETCHES re-calls with the
        requested context attached.

        Returns dict with: agent, thought, cmd, tool, args, conf,
        verify, handoff, need.
        """
        active = self.ptt.find_in_progress() or self.ptt.find_next_pending()
        if active and active.status == "todo":
            self.ptt.set_status(active.nid, "in_progress")
            active = self.ptt.nodes[active.nid]

        # v7.1 — let context manager track signals
        self.context_mgr.signal_node_change(active.nid if active else None)
        self.context_mgr.signal_stuck(self.stuck_counter)

        agent_role = self._select_agent(active, free_form=prompt)
        self.current_agent = agent_role

        # The NEED loop: build a minimal prompt; if the LLM emits [NEED],
        # rebuild with the requested attachments and call again, up to
        # MAX_NEED_FETCHES times.
        need_attachments: List[str] = []
        parsed: Dict[str, Any] = {}
        for fetch_round in range(MAX_NEED_FETCHES + 1):
            sys_prompt = build_system_prompt(
                agent_role=agent_role,
                target_info=self.target_info,
                ptt=self.ptt,
                active_node=active,
                lhost=self.lhost,
                workflow_key=workflow_key,
                free_form=prompt,
                context_mgr=self.context_mgr,
                graph=self.graph,
                scope=self.scope,
                need_attachments=need_attachments,
            )

            # v7.1 — slice history per context manager
            slice_size = self.context_mgr.history_slice_size()
            # If [NEED]history[/NEED] requested, send the lot
            if "history" in need_attachments:
                slice_size = MAX_HISTORY_MESSAGES
            windowed = self.history[-slice_size:]

            # Compress assistant turns to just their CMD/TOOL block
            compressed_history = []
            for msg in windowed:
                if msg["role"] == "assistant":
                    cm = re.search(r'\[CMD\](.*?)\[/?CMD\]',
                                   msg["content"], re.DOTALL)
                    tm = re.search(r'\[TOOL\](.*?)\[/?TOOL\]',
                                   msg["content"], re.DOTALL)
                    am = re.search(r'\[ARGS\](.*?)\[/?ARGS\]',
                                   msg["content"], re.DOTALL)
                    if tm and am:
                        compressed_history.append({
                            "role": "assistant",
                            "content": (f"[TOOL]{tm.group(1).strip()}[/TOOL]"
                                        f"[ARGS]{am.group(1).strip()}[/ARGS]")
                        })
                    elif cm:
                        compressed_history.append({
                            "role": "assistant",
                            "content": f"[CMD]{cm.group(1).strip()}[/CMD]"
                        })
                    else:
                        compressed_history.append(msg)
                else:
                    compressed_history.append(msg)

            messages = [{"role": "system", "content": sys_prompt}]
            messages.extend(compressed_history)
            messages.append({"role": "user", "content": prompt})

            # Estimate tokens for context savings counter
            sent_size = sum(len(m["content"]) for m in messages)
            full_size_est = sent_size + (
                # estimate of what FULL context would have added
                4000 if not need_attachments else 0
            )
            self.context_mgr.record_savings(full_size_est, sent_size)

            response = self._think_with_fallback(messages,
                                                  max_tokens=MAX_TOKENS_DEFAULT)
            if not response:
                return {"agent": agent_role, "thought": "", "cmd": None,
                        "tool": None, "args": None,
                        "conf": "red", "verify": None, "handoff": None,
                        "need": []}

            parsed = parse_specialist_response(response)
            parsed["agent"] = agent_role

            # If LLM requested more context AND we still have rounds left
            if parsed["need"] and fetch_round < MAX_NEED_FETCHES:
                # Attach the requested context for next round, don't log
                # the [NEED] turn into history (it's a meta-call)
                fresh = [n for n in parsed["need"] if n not in need_attachments]
                if fresh:
                    need_attachments.extend(fresh)
                    print(f"\033[90m   ▸ context-fetch — LLM requested: "
                          f"\033[36m{', '.join(fresh)}\033[0m")
                    continue
            break

        # Only log the FINAL exchange to history (not the NEED-only turns)
        self.history.append({"role": "user", "content": prompt})
        self.history.append({"role": "assistant", "content": response})
        # Trim to MAX_HISTORY_MESSAGES — kept in RAM, only sliced when sending
        if len(self.history) > MAX_HISTORY_MESSAGES * 2:
            self.history = self.history[-(MAX_HISTORY_MESSAGES * 2):]
        self._log(f"[AI:{agent_role}]\n{response}")

        # ZEUS GUARD: strategist MUST delegate, never execute.  If the
        # strategist emitted [CMD] or [TOOL] alongside (or instead of)
        # a handoff, drop them — UNLESS the [CMD] is WORKFLOW_COMPLETE,
        # which is the strategist's only way to end the investigation.
        # Without this guard the strategist runs OSINT commands itself
        # and the specialists never get a turn.
        self._pending_dispatch_error = None
        if agent_role == "strategist":
            cmd_is_workflow_done = (
                parsed["cmd"]
                and "WORKFLOW_COMPLETE" in parsed["cmd"].upper()
                and not parsed["tool"]
            )
            if cmd_is_workflow_done:
                # Pass through — investigation termination is allowed.
                # Normalise to bare WORKFLOW_COMPLETE so downstream
                # comparisons match.
                parsed["cmd"] = "WORKFLOW_COMPLETE"
                parsed["tool"] = None
                parsed["args"] = None
            elif parsed["cmd"] or parsed["tool"]:
                # Strategist tried to run an actual command — strip it.
                parsed["cmd"] = None
                parsed["tool"] = None
                parsed["args"] = None
                # If there was no handoff either, queue a corrective
                # message that forces the strategist to delegate next turn.
                if not parsed["handoff"]:
                    self._pending_dispatch_error = (
                        "STRATEGIST RULE: you must NEVER emit [CMD] or "
                        "[TOOL] (except [CMD]WORKFLOW_COMPLETE[/CMD] to "
                        "end the investigation).  Your only output is "
                        "[HANDOFF]<role>[/HANDOFF] where <role> is one "
                        "of: intake, socialite, postman, caller, "
                        "registrar, cartographer, archivist, dorker, "
                        "ledger, reporter."
                    )

        # v7.2 — TOOL dispatch: convert [TOOL]/[ARGS] → shell string.
        # Hard errors are stashed on self._pending_dispatch_error so the
        # agent loop can splice them into the next prompt — that way
        # the LLM actually learns about its bad kwargs instead of
        # looping the same args.
        dispatch_remap_note = ""
        if parsed["tool"]:
            shell, msg = dispatch_tool(parsed["tool"], parsed["args"] or "{}")
            if shell:
                parsed["cmd"] = shell
                if msg and msg.startswith("NOTE:"):
                    dispatch_remap_note = msg
            else:
                # Hard ERROR — feed back to LLM next turn
                self._pending_dispatch_error = (
                    f"Your previous [TOOL]{parsed['tool']}[/TOOL] dispatch "
                    f"failed:\n  {msg}\n"
                    f"Either correct the args, switch tools, or fall back "
                    f"to a [CMD] block."
                )
                if not parsed["cmd"]:
                    parsed["cmd"] = None  # no fallback — agent loop will retry

        # If a handoff is requested, queue it for next turn's _select_agent
        if parsed["handoff"] and parsed["handoff"] in AGENT_SPECS:
            self._forced_next_agent = parsed["handoff"]

        # v7.2 — failure-aware confidence.  If we've failed N times
        # already on this node, force a yellow/red regardless of what
        # the LLM said.
        if active and active.attempts >= NODE_ATTEMPT_LIMIT - 1:
            parsed["conf"] = "red"
        elif active and active.attempts >= 2 and parsed["conf"] == "green":
            parsed["conf"] = "yellow"

        # ─── v7.2 BOXED RENDERING ──────────────────────────────────
        target_label = (self.target_info.get("ip") or
                        self.target_info.get("domain") or "no-target")
        self._turn_no = getattr(self, "_turn_no", 0) + 1
        v_count = len(self.ptt.get_verified())
        u_count = len(self.ptt.get_unverified())
        node_label = active.nid if active else "—"
        print()
        print(turn_box(
            turn_no=self._turn_no,
            target=target_label,
            agent_role=agent_role,
            model=self._current_model_name(),
            verified=v_count, unverified=u_count,
            techniques=len(self.attack_techniques_used),
            node_id=node_label,
        ))
        if parsed["thought"]:
            print(thought_card(parsed["thought"], agent_role=agent_role))
        if parsed["tool"] and parsed["cmd"]:
            tool_attack = attack_id_for_command(parsed["cmd"])
            t_id = tool_attack[0] if tool_attack else ""
            t_name = tool_attack[1] if tool_attack else ""
            print(dispatch_card(
                tool=parsed["tool"], shell_str=parsed["cmd"],
                attack_id=t_id, attack_name=t_name,
                remap_note=dispatch_remap_note,
            ))
        elif self._pending_dispatch_error:
            print(error_alert(
                "TOOL DISPATCH FAILED",
                self._pending_dispatch_error,
                hint="The error will be fed back to the AI on the next turn.",
            ))

        if active:
            self.ptt.set_confidence(active.nid, parsed["conf"])

        # v7.1 — feed signals back to context manager
        self.context_mgr.signal_confidence(parsed["conf"])

        return parsed

    # ── PTT seeding from workflow ────────────────────────────────

    def _seed_ptt_from_workflow(self, key: str, target: str):
        wf = WORKFLOWS.get(key)
        if not wf:
            return
        goal = f"{wf['name']}: {target}"
        self.ptt = PTT(goal=goal)
        for title, phase in wf["seed"]:
            self.ptt.add_node(self.ptt.root_id, title, phase, status="todo")

    # ── Stuck recovery ───────────────────────────────────────────

    def _handle_stuck(self):
        """When stuck — ask AI for 3 alternative approaches."""
        print("\n\033[33m   ⚠  Zeus is stuck.  Asking AI for 3 alternatives...\033[0m")

        active = self.ptt.find_in_progress() or self.ptt.find_next_pending()
        node_desc = (f"Current node: [{active.nid}] {active.title} "
                     f"(phase={active.phase})") if active else "No active node"

        verified_summary = []
        for f in self.ptt.get_verified()[-10:]:
            verified_summary.append(f"{f.ftype}={f.value}")

        prompt = (
            f"You are stuck.  {node_desc}.\n"
            f"Verified findings: {' | '.join(verified_summary) or 'minimal'}.\n"
            "Output ONLY this format:\n"
            "[OPTIONS]\n"
            "1. <approach 1 — fundamentally different OSINT angle, one line>\n"
            "2. <approach 2 — different OSINT angle, one line>\n"
            "3. <approach 3 — different OSINT angle, one line>\n"
            "[/OPTIONS]\n"
            "Each option must take a totally different OSINT approach "
            "(e.g. handle pivot to a new platform vs email registration "
            "probe vs domain/subdomain enumeration vs wayback archive "
            "recovery vs blockchain address lookup vs EXIF/image "
            "metadata).  Stay within the declared lane and refuse list."
        )

        response = self._think_with_fallback([
            {"role": "system",
             "content": "You are Zeus, listing pivot options when stuck."},
            {"role": "user", "content": prompt},
        ])
        if not response:
            print("\033[31m   AI unavailable.  Type your own next objective.\033[0m")
            return

        m = re.search(r'\[OPTIONS\](.*?)\[/?OPTIONS\]', response, re.DOTALL)
        opts_text = m.group(1).strip() if m else response

        print(f"\n\033[35m   ZEUS — 3 ALTERNATIVES:\033[0m\n")
        print(f"\033[97m{opts_text}\033[0m\n")

        try:
            choice = input(
                "\033[90m   Pick [1/2/3] or type own objective: \033[0m"
            ).strip()
        except (EOFError, KeyboardInterrupt):
            return

        if choice in ("1", "2", "3"):
            for line in opts_text.split('\n'):
                if line.strip().startswith(choice + "."):
                    new_obj = line.split('.', 1)[1].strip()
                    print(f"\033[32m   Pursuing: {new_obj}\033[0m")
                    # Mark current as dead-end so we don't loop back
                    if active:
                        self.ptt.set_status(active.nid, "dead_end")
                    self._agent_loop(new_obj)
                    return
        elif choice:
            self._agent_loop(choice)

    # ── Main agent loop ──────────────────────────────────────────

    def _agent_loop(self, initial_prompt: str,
                    workflow_key: Optional[str] = None):
        prompt = initial_prompt
        self.current_workflow_key = workflow_key
        self.stuck_counter = 0
        # Track success per node so workflow can't auto-complete a
        # streak of failures (v7.2 fix).
        self._node_success_count: Dict[str, int] = {}

        # Autonomous-mode caps
        loop_start_ts = time.time()
        turns_taken   = 0

        while True:
            # ── Autonomous caps ───────────────────────────────────
            elapsed = time.time() - loop_start_ts
            if AUTO_MODE and turns_taken >= MAX_AUTO_TURNS:
                say_warn(f"Hit MAX_AUTO_TURNS ({MAX_AUTO_TURNS}) — "
                         f"stopping investigation, generating report.")
                break
            if AUTO_MODE and elapsed >= MAX_WALL_CLOCK_SECONDS:
                say_warn(f"Hit MAX_WALL_CLOCK ({MAX_WALL_CLOCK_SECONDS}s) — "
                         f"stopping investigation, generating report.")
                break
            turns_taken += 1

            # v7.2 — turn header is now drawn by turn_box() inside
            # think_turn(); no inline header needed here.
            active = self.ptt.find_in_progress() or self.ptt.find_next_pending()

            # v7.2 — if a previous turn produced a hard dispatch error,
            # splice it into the prompt so the LLM sees its own mistake
            # and can correct.  Without this the loop just kept emitting
            # the same kwargs and getting silently dropped.
            pending_err = getattr(self, "_pending_dispatch_error_to_prompt", None)
            if pending_err:
                prompt = (
                    f"DISPATCH ERROR FROM YOUR PREVIOUS TURN:\n"
                    f"{pending_err}\n\n"
                    f"Re-issue with corrected args, switch tools, or "
                    f"use [CMD]. Original task:\n{prompt}"
                )
                self._pending_dispatch_error_to_prompt = None

            parsed = self.think_turn(prompt, workflow_key=workflow_key)
            cmd     = parsed["cmd"]
            conf    = parsed["conf"]
            verify  = parsed["verify"]
            handoff = parsed["handoff"]

            # v7.2 — propagate any fresh dispatch error from think_turn
            # into the next iteration of this loop.
            if getattr(self, "_pending_dispatch_error", None):
                self._pending_dispatch_error_to_prompt = self._pending_dispatch_error
                self._pending_dispatch_error = None

            if cmd is None:
                # ZEUS: a handoff with no command is normal for the
                # strategist (and for any specialist that wants to
                # delegate).  Switch agent and continue without
                # counting it as a failed turn.
                if handoff and handoff in AGENT_SPECS:
                    self._forced_next_agent = handoff
                    self._no_cmd_retries = 0
                    spec = AGENT_SPECS[handoff]
                    print(f"\033[36m   ↪ routing to "
                          f"\033[{spec['color']}m{spec['icon']} "
                          f"{spec['name']}\033[0m")
                    # Build a fresh prompt for the next agent
                    prompt = (
                        f"You are now active.  Read the OTT and the "
                        f"intake identifiers, pick the right tool from "
                        f"your specialty, and emit a single [TOOL] or "
                        f"[CMD] block.  Stay within your lane "
                        f"({self.target_info.get('lane', 'unknown')})."
                    )
                    continue

                # v7.1 — instead of bailing, retry up to 2x with a
                # corrective hint.  This recovers from tool-dispatch
                # failures and from the LLM accidentally omitting [CMD].
                no_cmd_retries = getattr(self, "_no_cmd_retries", 0)
                if no_cmd_retries < 2:
                    self._no_cmd_retries = no_cmd_retries + 1
                    say_warn("Agent did not output a [CMD] block — asking again.")
                    prompt = (
                        "Your previous response had no executable command. "
                        "Output a SINGLE [CMD]…[/CMD] line (or [TOOL]…[/TOOL]"
                        "[ARGS]…[/ARGS]) plus [THOUGHT][CONF].  If your "
                        "preferred tool isn't in the structured registry or "
                        "its dispatch failed, fall back to [CMD] with the "
                        "raw shell command.  If you cannot make further "
                        "progress on this branch, emit "
                        "[CMD]WORKFLOW_COMPLETE[/CMD] alone."
                    )
                    continue
                else:
                    self._no_cmd_retries = 0
                    say_err("Still no command after 2 retries — bailing.")
                    break
            else:
                self._no_cmd_retries = 0  # reset on success

            # Workflow done check — v7.2 GATED on actual progress
            if WORKFLOW_DONE in cmd.upper() or "WORKFLOW_COMPLETE" in cmd.upper():
                # v7.2 — refuse to auto-complete a node that has zero
                # successful commands AND zero findings.  The LLM can
                # try to bail out of failures with WORKFLOW_COMPLETE;
                # this gate stops that.
                node_findings = 0
                node_successes = 0
                if active:
                    node_findings = len(active.findings)
                    node_successes = self._node_success_count.get(active.nid, 0)
                if active and node_findings == 0 and node_successes == 0:
                    say_warn(f"Refusing WORKFLOW_COMPLETE on node "
                             f"[{active.nid}] — 0 findings, 0 successful "
                             f"commands. Try a different approach.")
                    prompt = (
                        f"You proposed WORKFLOW_COMPLETE but node "
                        f"[{active.nid}] {active.title} has produced no "
                        f"successful commands and no findings yet. "
                        f"You may not skip a node that hasn't yielded "
                        f"any data. Take a fundamentally different "
                        f"approach (different tool, different angle), "
                        f"or [HANDOFF]<other_agent>[/HANDOFF] to escalate."
                    )
                    continue

                if active:
                    self.ptt.set_status(active.nid, "done")
                # Check if we have more pending nodes
                nxt = self.ptt.find_next_pending()
                if nxt:
                    print()
                    print(_box(
                        "✓ NODE COMPLETE",
                        [f"  Moving to: \033[97m[{nxt.nid}] {nxt.title}\033[0m"],
                        color="32"))
                    self.ptt.set_status(nxt.nid, "in_progress")
                    prompt = (f"Previous node complete.  "
                              f"Now work on: {nxt.title} (phase: {nxt.phase}). "
                              f"Output [THOUGHT][CMD][CONF].")
                    continue
                else:
                    print()
                    print(_box(
                        "✓ WORKFLOW COMPLETE",
                        [f"  All nodes done. \033[32m{len(self.ptt.get_verified())}"
                         f"\033[0m verified findings, "
                         f"\033[33m{len(self.ptt.get_unverified())}\033[0m unverified."],
                        color="32"))
                    self._log("[WORKFLOW DONE]")
                    break

            # Handoff request
            if handoff and handoff in AGENT_SPECS:
                print()
                print(_box(
                    "↪ HANDOFF",
                    [f"  → {AGENT_SPECS[handoff]['icon']} "
                     f"{AGENT_SPECS[handoff]['name']}"],
                    color="33"))
                # Add a sibling node for the handoff phase if reasonable
                if active and active.parent_id:
                    self.ptt.add_node(active.parent_id,
                                      f"Handoff to {handoff}",
                                      handoff, status="todo")

            # Banned check
            if self._is_banned(cmd):
                print()
                print(error_alert(
                    "BANNED COMMAND BLOCKED",
                    f"`{cmd}` would change UI / system packages.",
                    hint="Use `which`/`dpkg -l` to check tools instead. "
                         "apt upgrade variants are permanently disabled."))
                prompt = ("That apt upgrade variant is blocked.  Use which "
                          "or dpkg -l to check tools.  Provide alternative "
                          "with [THOUGHT][CMD][CONF].")
                continue

            # v7.2 — Track command for repeat detection.  More aggressive
            # than v7.1: ANY exact repeat in the last 5 commands triggers
            # a forced agent rotation + RED conf override.  This stops
            # the loop where dropped kwargs produced identical shells.
            cmd_norm = re.sub(r'\s+', ' ', cmd.strip().lower())
            if cmd_norm in self.command_history[-5:]:
                print()
                print(error_alert(
                    "LOOP DETECTED",
                    f"You just ran this exact command. Repeating means the "
                    f"previous result didn't change anything you can act on.",
                    hint="Forcing pivot to a different approach now."))
                self.stuck_counter += 1
                if active:
                    self.ptt.increment_attempts(active.nid)
                    self.ptt.set_confidence(active.nid, "red")
                if self.stuck_counter >= STUCK_THRESHOLD:
                    self.stuck_counter = 0
                    self._handle_stuck()
                    break
                # v7.2 — give the LLM stronger guidance: name the command,
                # require a *different category* of approach, and bump
                # the agent if possible.
                rotation_hint = ""
                if self.current_agent == "strategist":
                    rotation_hint = " Switch from scanning to direct service interaction (whatweb, curl, nxc, smbclient)."
                elif self.current_agent == "web":
                    rotation_hint = " Switch from brute/fuzz to manual probing (curl with payloads) or pivot to network agent."
                prompt = (
                    f"LOOP-BREAKER: you already ran `{cmd}`. The result "
                    f"didn't help. Take a FUNDAMENTALLY DIFFERENT approach: "
                    f"different tool, different angle, different "
                    f"specialist.{rotation_hint} Output [THOUGHT][CMD][CONF]. "
                    f"You may [HANDOFF]<other_agent>[/HANDOFF] to escalate."
                )
                continue

            self.command_history.append(cmd_norm)
            if len(self.command_history) > 25:
                self.command_history = self.command_history[-25:]

            # Confidence handling — the pill already shows in think_turn()
            if conf == "red":
                print()
                print(_box("RED CONFIDENCE — execution skipped",
                           ["  Asking AI for recon to gather missing "
                            "context first."], color="31"))
                prompt = ("Confidence was RED.  Propose a recon command to "
                          "gather the missing context, not the attack.  "
                          "[THOUGHT][CMD][CONF].")
                continue

            # Execute the command (always y/n gated)
            if active:
                self.ptt.increment_attempts(active.nid)
                self.ptt.set_last_cmd(active.nid, cmd)
            output = self.run_command(cmd)

            if output == EXEC_SESSION_EXIT:
                print()
                say_zeus("Session ended by The Priest.")
                self._generate_report()
                if self.logfile:
                    self.logfile.close()
                sys.exit(0)

            if output == EXEC_INTERACTIVE_BLOCKED:
                _, fix = self._is_interactive(cmd)
                prompt = (f"That command would hijack the terminal.  {fix}  "
                          f"Provide non-interactive alternative.  "
                          f"[THOUGHT][CMD][CONF].")
                continue

            if output == EXEC_DESTRUCTIVE:
                prompt = ("That command was destructive and refused.  "
                          "Propose a non-destructive alternative.  "
                          "[THOUGHT][CMD][CONF].")
                continue

            if output == EXEC_REJECTED:
                self.stuck_counter += 1
                if active:
                    if active.attempts >= NODE_ATTEMPT_LIMIT:
                        self.ptt.set_status(active.nid, "dead_end")
                        print()
                        print(_box(
                            "✗ DEAD END",
                            [f"  Node [{active.nid}] {active.title}",
                             f"  Marked dead-end after {active.attempts} attempts."],
                            color="31"))
                if self.stuck_counter >= STUCK_THRESHOLD:
                    self.stuck_counter = 0
                    self._handle_stuck()
                    break

                try:
                    print()
                    say_zeus("Alternative approach?", indent=3)
                    raw = input(f"   {kbd('y')} yes   {kbd('n')} no  › ")
                except (EOFError, KeyboardInterrupt):
                    break
                if self._normalize_choice(raw) == "y":
                    prompt = ("The Priest rejected that.  Different approach "
                              "to same goal.  [THOUGHT][CMD][CONF].")
                    continue
                else:
                    break

            # v7.2 — record a successful exec for this node (used by
            # the WORKFLOW_COMPLETE gate above).  We count any non-error
            # return from run_command as a success at the framework
            # level — even if the tool found nothing, the LLM at least
            # got real output to reason from.
            self.stuck_counter = 0
            if active:
                self._node_success_count[active.nid] = (
                    self._node_success_count.get(active.nid, 0) + 1)

            # v7.1 — flush any queued credential fanout work into PTT
            self._flush_cred_fanout()

            # Optional verification
            if verify:
                print()
                print(_box(
                    "PoC VERIFICATION",
                    ["  Agent proposed a verification command — "
                     "running through y/n gate."],
                    color="33"))
                # Try to figure out which finding it's verifying — pick the
                # most recent unverified finding from this node
                if active:
                    candidates = [self.ptt.findings[fid - 1] for fid in active.findings
                                  if fid - 1 < len(self.ptt.findings)]
                else:
                    candidates = []
                target_finding = None
                for f in candidates:
                    if not f.verified:
                        target_finding = f
                        break
                if target_finding is None and self.ptt.get_unverified():
                    target_finding = self.ptt.get_unverified()[-1]

                if target_finding:
                    self.attempt_verification(verify,
                                              target_finding.value,
                                              target_finding.ftype)
                else:
                    # Just run the verify command standalone
                    self.run_command(verify, label="VERIFY")

            # Build pivot prompt with fresh context
            pivot_lines = []
            f_dict = self.ptt.findings_by_type_dict(only_verified=True)
            if f_dict:
                pivot_lines.append("VERIFIED FINDINGS:")
                for k, vs in f_dict.items():
                    pivot_lines.append(f"  {k.upper()}: {', '.join(vs[-4:])}")
            unv = self.ptt.get_unverified()
            if unv:
                u_dict: Dict[str, List[str]] = {}
                for f in unv[-15:]:
                    u_dict.setdefault(f.ftype, []).append(f.value)
                pivot_lines.append("UNVERIFIED CANDIDATES:")
                for k, vs in u_dict.items():
                    pivot_lines.append(f"  {k.upper()}: {', '.join(vs)}")
            pivot = "\n".join(pivot_lines)

            prompt = (
                f"TERMINAL OUTPUT:\n{output}\n\n"
                f"{pivot}\n\n"
                "Analyse with elite reasoning in [THOUGHT].  Pivot on "
                "verified findings.  WORKFLOW_COMPLETE if current node "
                "is done; else next [CMD].  Always include [CONF]."
            )

    # ── Workflow runner ───────────────────────────────────────────

    def _resolve_target(self) -> str:
        target = (self.target_info.get("ip") or
                  self.target_info.get("domain") or "")
        if not target:
            try:
                target = input("\033[90m   Enter target: \033[0m").strip()
            except (EOFError, KeyboardInterrupt):
                target = ""
        return target

    def run_workflow(self, key: str):
        wf = WORKFLOWS.get(key)
        if not wf:
            return
        target = self._resolve_target()
        if not target:
            print("\033[31m   No target.\033[0m")
            return

        print()
        say_zeus(f"Workflow: {wf['name']}")
        print()
        self._log(f"[WORKFLOW] {wf['name']}")
        self._seed_ptt_from_workflow(key, target)
        print(self.ptt.to_terminal())
        print()

        prompt = (f"Workflow: {wf['name']}\nTarget: {target}\n\n"
                  f"Walk the PTT one node at a time.  For each node, output "
                  f"[THOUGHT][CMD][CONF].  Mark WORKFLOW_COMPLETE when the "
                  f"current node is done; the system will move you to the next.")
        self._agent_loop(prompt, workflow_key=key)

    def show_workflow_menu(self):
        print(f"\n{header_box('  WORKFLOW MENU  ', color='35')}\n")
        for k, wf in WORKFLOWS.items():
            print(f"   \033[97m[{k:>2}]\033[0m  {wf['name']}")
            print(f"          \033[90m{wf['description']}\033[0m")
        print(f"\n   \033[97m[ 0]\033[0m  Cancel\n")
        try:
            choice = input("\033[90m   Select: \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if choice in WORKFLOWS:
            self.run_workflow(choice)
        elif choice != "0":
            print("\033[33m   Invalid.\033[0m")

    # ── Findings / Tree display ──────────────────────────────────

    def show_findings(self):
        if not self.ptt.findings:
            print("\n\033[90m   No findings yet.\033[0m\n")
            return

        verified = self.ptt.get_verified()
        unverified = self.ptt.get_unverified()

        print(f"\n{header_box('  FINDINGS  ', color='32')}")
        if verified:
            print(f"\n\033[32m   VERIFIED ({len(verified)}):\033[0m")
            for f in verified:
                print(finding_card(f))
        if unverified:
            print(f"\n\033[33m   UNVERIFIED ({len(unverified)}):\033[0m")
            for f in unverified:
                print(finding_card(f))
        print()

    def show_tree(self):
        print(f"\n{header_box('  OSINT TASK TREE  ', color='35')}\n")
        print(self.ptt.to_terminal())
        print()
        print(f"  \033[90mLegend:\033[0m  "
              f"○ todo  \033[33m◐\033[0m in_progress  "
              f"\033[32m●\033[0m done  \033[31m✗\033[0m dead-end  "
              f"\033[90m─\033[0m skipped")
        print()

    # ── Report generation (with cleanup pass) ───────────────────

    def _llm_cleanup_pass(self) -> str:
        """Ask the AI to write a clean OSINT report from the findings.
        Called at report-generation time.  Returns a markdown body.
        Falls through to a plain dump if the LLM is unavailable.
        """
        verified = self.ptt.get_verified()
        if not verified and not self.ptt.findings:
            return "No findings to report."

        # Prepare context for the LLM
        v_summary = []
        for f in verified:
            v_summary.append(
                f"- {f.ftype}: {f.value} "
                f"(node {f.node_id}, source: `{f.source_cmd[:80]}`)"
            )

        u_summary = []
        for f in self.ptt.get_unverified():
            u_summary.append(f"- {f.ftype}: {f.value} (unverified, node {f.node_id})")

        ti = self.target_info or {}
        lane = ti.get("lane") or "(no lane)"
        subj_type = ti.get("subject_type") or "(no subject type)"
        ident = ti.get("identifiers") or {}
        seed_label = (
            ident.get("real_name") or ident.get("legal_name") or
            ident.get("primary") or
            (ident.get("addresses") or [None])[0] or
            (ident.get("handles") or [None])[0] or
            "(unnamed subject)"
        )

        # Inventory of intake identifiers (so the LLM knows what
        # categories were declared up front — separates "what we knew"
        # from "what we surfaced").
        intake_lines: List[str] = []
        for k, v in ident.items():
            if not v:
                continue
            if isinstance(v, list):
                for item in v:
                    intake_lines.append(f"- {k}: {item}")
            else:
                intake_lines.append(f"- {k}: {v}")

        sys_prompt = (
            "You are Zeus' Reporter agent.  You write OSINT investigation "
            "summaries from public-source data only.  Be concise and "
            "factual.  Use Markdown.  Never invent findings — use only "
            "what is provided in the prompt.  This is OSINT, not "
            "incident response: do NOT use ATT&CK terminology, do NOT "
            "write 'verdict: healthy/suspicious/compromised', do NOT "
            "recommend EDR or hardening.  This report describes what "
            "is publicly findable about a subject."
        )

        user_prompt = (
            f"Subject:       {seed_label}\n"
            f"Lane:          {lane}\n"
            f"Subject type:  {subj_type}\n\n"
            f"INTAKE IDENTIFIERS (operator-declared):\n"
            f"{chr(10).join(intake_lines) or '(none)'}\n\n"
            f"VERIFIED FINDINGS (multi-source corroborated):\n"
            f"{chr(10).join(v_summary) or '(none)'}\n\n"
            f"UNVERIFIED FINDINGS (single-source, treat as leads):\n"
            f"{chr(10).join(u_summary) or '(none)'}\n\n"
            f"Write a concise OSINT report with these sections:\n"
            f"## Subject Overview\n"
            f"   Two or three sentences: who/what was investigated, "
            f"under which lane, and why it matters in plain English.\n"
            f"## Surfaced Identifiers\n"
            f"   New identifiers Zeus discovered beyond what the "
            f"operator provided (handles, emails, domains, profiles).  "
            f"Group by category.  Skip if there are none.\n"
            f"## Cross-Platform Presence\n"
            f"   Where the subject appears across the public web "
            f"(social platforms, GitHub, mastodon, etc.).  Skip if no "
            f"such findings exist.\n"
            f"## Infrastructure / Footprint\n"
            f"   Domains, subdomains, DNS records, archive snapshots — "
            f"applicable mainly for company/domain subjects.  Skip if "
            f"no such findings exist.\n"
            f"## Coverage Gaps\n"
            f"   Branches Zeus could not exhaust (rate-limited, missing "
            f"tools, lane-restricted).  Operator should investigate "
            f"these manually.\n"
            f"## Confidence\n"
            f"   Brief breakdown — N findings high-confidence "
            f"(corroborated), M medium (single source), K leads only.\n\n"
            f"Do not invent ATT&CK techniques, MITRE IDs, threat "
            f"actors, or compromise verdicts.  This is a footprint "
            f"report, not an incident report."
        )

        response = self._think_with_fallback([
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ], max_tokens=2048)

        if not response:
            return self._fallback_report_body()
        return response

    def _fallback_report_body(self) -> str:
        lines = ["## Findings\n"]
        verified = self.ptt.get_verified()
        if verified:
            lines.append("### Verified")
            for f in verified:
                lines.append(f"- **{f.ftype}**: `{f.value}` "
                             f"(source: `{f.source_cmd[:100]}`)")
        unv = self.ptt.get_unverified()
        if unv:
            lines.append("\n### Unverified Candidates")
            for f in unv:
                lines.append(f"- **{f.ftype}**: `{f.value}`")
        return "\n".join(lines)

    def _generate_report(self):
        """Print the final OSINT report directly to the terminal.
        Zeus is RAM-only — nothing is saved to disk.  Operator copies
        whatever they want from the terminal before exit."""
        ts = datetime.datetime.now()
        duration = ts - self.session_start

        # Subject summary from intake
        ti = self.target_info or {}
        lane = ti.get("lane") or "(no lane)"
        subj = ti.get("subject_type") or "(no subject)"
        ident = ti.get("identifiers") or {}
        seed_label = (
            ident.get("real_name") or ident.get("legal_name") or
            ident.get("primary") or
            (ident.get("addresses") or [None])[0] or
            (ident.get("handles") or [None])[0] or
            "(unnamed subject)"
        )

        # Get LLM-generated body
        body = self._llm_cleanup_pass()
        # OSINT-category section (replaces ATT&CK)
        category_section = self._build_mitre_section()

        # ── Render to terminal ─────────────────────────────────────
        W = 75
        bar = "═" * W
        thin = "─" * W

        print()
        print(f"\033[33m╔{bar}╗\033[0m")
        print(f"\033[33m║\033[0m  \033[1m\033[97m  ZEUS  v{VERSION}  ·  OSINT INVESTIGATION REPORT\033[0m"
              .ljust(W + 16) + f"\033[33m║\033[0m")
        print(f"\033[33m╚{bar}╝\033[0m")
        print()
        print(f"  \033[97mSubject:\033[0m       {seed_label}")
        print(f"  \033[97mLane:\033[0m          {lane}")
        print(f"  \033[97mSubject type:\033[0m  {subj}")
        print(f"  \033[97mCommander:\033[0m     The Priest")
        print(f"  \033[97mStarted:\033[0m       {self.session_start.isoformat(timespec='seconds')}")
        print(f"  \033[97mDuration:\033[0m      {str(duration).split('.')[0]}")
        print(f"  \033[97mThis host:\033[0m     {self.lhost}")
        print(f"  \033[97mFindings:\033[0m      "
              f"{len(self.ptt.get_verified())} verified · "
              f"{len(self.ptt.get_unverified())} unverified")
        if self.context_mgr.tokens_saved_estimate > 0:
            print(f"  \033[97mTokens saved:\033[0m  "
                  f"~{self.context_mgr.tokens_saved_estimate:,}")
        print()

        # Identifier inventory
        non_empty_ids = {k: v for k, v in ident.items() if v}
        if non_empty_ids:
            print(f"\033[36m  ─── INTAKE IDENTIFIERS ───\033[0m")
            for k, v in non_empty_ids.items():
                if isinstance(v, list):
                    if not v:
                        continue
                    print(f"    \033[97m{k}:\033[0m")
                    for item in v[:10]:
                        print(f"       • {item}")
                else:
                    print(f"    \033[97m{k}:\033[0m  {v}")
            print()

        # LLM-generated body
        print(f"\033[36m  ─── ANALYSIS ───\033[0m")
        print()
        for line in body.splitlines():
            print(f"  {line}")
        print()

        # OSINT category coverage
        print(f"\033[36m  ─── OSINT CATEGORY COVERAGE ───\033[0m")
        for line in category_section.splitlines():
            print(f"  {line}")
        print()

        # OTT final state
        print(f"\033[36m  ─── OSINT TASK TREE (final) ───\033[0m")
        for line in self.ptt.to_natural_language(max_chars=4000).splitlines():
            print(f"  {line}")
        print()

        # Raw findings with provenance
        if self.ptt.findings:
            print(f"\033[36m  ─── RAW FINDINGS ───\033[0m")
            for fnd in self.ptt.findings:
                mark = "\033[32m✓\033[0m" if fnd.verified else "\033[90m?\033[0m"
                tag = (f" \033[36m[{fnd.attack_id} {fnd.attack_name}]\033[0m"
                       if fnd.attack_id else "")
                print(f"    {mark} \033[97m{fnd.ftype:<14}\033[0m "
                      f"\033[33m{fnd.value[:60]}\033[0m{tag}")
                print(f"        \033[90msource: {fnd.source_cmd[:120]}\033[0m")
            print()

        # Disclaimer
        print(f"\033[33m  {thin}\033[0m")
        print(f"\033[33m  All findings above are PUBLIC OSINT only.  Lane: {lane}.\033[0m")
        print(f"\033[33m  Retained in RAM only — gone the moment Zeus exits.\033[0m")
        print(f"\033[33m  No data was written to disk.  Copy what you want NOW.\033[0m")
        print(f"\033[33m  {thin}\033[0m")
        print()
        print(f"\033[90m  (Generated by Zeus v{VERSION} at {ts.isoformat(timespec='seconds')})\033[0m")
        print()

    def _build_mitre_section(self) -> str:
        """v7.1 — MITRE ATT&CK Navigator-friendly section: techniques
        exercised, findings grouped by technique."""
        lines = ["## OSINT Category Coverage\n"]

        # Categories exercised (from commands run)
        if self.attack_techniques_used:
            lines.append("### Categories Exercised\n")
            lines.append("| ID | Category | Bucket | Times |")
            lines.append("|----|----------|--------|-------|")
            # Sort by tactic, then by count desc
            sorted_techs = sorted(
                self.attack_techniques_used.items(),
                key=lambda x: (x[1]["tactic"], -x[1]["count"]),
            )
            for tid, info in sorted_techs:
                lines.append(f"| {tid} | {info['name']} | "
                             f"{info['tactic']} | {info['count']} |")
            lines.append("")
        else:
            lines.append("_No OSINT categories recorded._\n")

        # Findings grouped by technique
        by_tech: Dict[str, List[Finding]] = {}
        for fnd in self.ptt.findings:
            if fnd.attack_id:
                by_tech.setdefault(fnd.attack_id, []).append(fnd)

        if by_tech:
            lines.append("### Findings by Category\n")
            for tid in sorted(by_tech.keys()):
                fs = by_tech[tid]
                first = fs[0]
                lines.append(f"#### {tid} — {first.attack_name} "
                             f"_({first.attack_tactic})_")
                for fnd in fs:
                    mark = "✓" if fnd.verified else "?"
                    lines.append(f"- [{mark}] {fnd.ftype}: `{fnd.value}`")
                lines.append("")

        return "\n".join(lines)

    def save_session(self):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(LOG_DIR, f"save_{ts}.txt")
        try:
            with open(path, "w") as f:
                f.write(f"ZEUS SAVE {ts}\n{'='*60}\n\n")
                for msg in self.history:
                    f.write(f"[{msg['role'].upper()}]\n{msg['content']}\n\n")
            print(f"\033[32m   Saved: {path}\033[0m")
        except Exception as e:
            print(f"\033[31m   Save failed: {e}\033[0m")

    # ── Help, status, tool status ─────────────────────────────────

    def show_model_status(self):
        print(f"\n{header_box('  PROVIDER CHAIN  ', color='35')}\n")
        for i, (model_id, name) in enumerate(PROVIDER_CHAIN):
            mark = "\033[32m▶ ACTIVE\033[0m" if i == self.provider_index else "      "
            print(f"   {mark}  [{i+1}]  \033[97m{name:<22}\033[0m  "
                  f"\033[90m{model_id}\033[0m")
        print()

    def show_tools_status(self):
        print(f"\n{header_box('  KALI ARSENAL — AVAILABILITY  ', color='35')}\n")
        all_tools = all_kali_tools_flat()
        # Cache lookups
        for t in all_tools:
            if t not in self.tools_available:
                self.tools_available[t] = cmd_exists(t)

        # Group by category, show install state
        for cat, tools in KALI_TOOLS.items():
            present = [t for t in tools if self.tools_available.get(t)]
            missing = [t for t in tools if not self.tools_available.get(t)]
            print(f"\n   \033[97m{cat.upper()}\033[0m  "
                  f"\033[32m{len(present)}\033[0m / "
                  f"\033[97m{len(tools)}\033[0m available")
            if present:
                print(f"     \033[32m✓\033[0m {', '.join(present[:8])}"
                      + (f" \033[90m+{len(present)-8} more\033[0m" if len(present) > 8 else ""))
            if missing:
                print(f"     \033[31m✗\033[0m {', '.join(missing[:8])}"
                      + (f" \033[90m+{len(missing)-8} more\033[0m" if len(missing) > 8 else ""))
        print()

        all_missing = [t for t, p in self.tools_available.items() if not p]
        if all_missing:
            try:
                ans = input(f"\033[33m   Install {len(all_missing)} missing tools? [y/n]: \033[0m")
            except (EOFError, KeyboardInterrupt):
                return
            if self._normalize_choice(ans) == "y":
                for t in all_missing:
                    install_if_missing(t)
                    self.tools_available[t] = cmd_exists(t)

    def show_scope(self):
        """v7.1 — display scope / RoE config; allow toggle."""
        print(f"\n{header_box('  ENGAGEMENT SCOPE / RoE  ', color='33')}\n")
        print(f"   {self.scope.summary()}")
        print(f"\n   \033[90mFile: {SCOPE_FILE}\033[0m")
        print(f"   \033[90mEdit that file to set CIDRs, domains, time windows.\033[0m\n")
        try:
            choice = input("   Toggle scope enabled? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice == "y":
            self.scope.enabled = not self.scope.enabled
            try:
                with open(SCOPE_FILE, "r") as f:
                    data = json.load(f)
            except Exception:
                data = dict(DEFAULT_SCOPE)
            data["enabled"] = self.scope.enabled
            try:
                with open(SCOPE_FILE, "w") as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                print(f"   \033[33m   Save failed: {e}\033[0m")
            state = ("\033[32menabled\033[0m" if self.scope.enabled
                     else "\033[90mdisabled\033[0m")
            print(f"   Scope is now {state}\n")

    def show_graph(self):
        """v7.1 — display OSINT pivot graph state."""
        print(f"\n{header_box('  PIVOT GRAPH  ', color='36')}\n")
        if not HAS_NETWORKX:
            print("   \033[33m   networkx not installed.  "
                  "pip install networkx --break-system-packages\033[0m\n")
            return
        print(f"   {self.graph.summary()}\n")
        compact = self.graph.to_compact_text(max_chars=4000)
        for line in compact.split("\n")[1:]:  # skip the summary line
            print(f"   {line}")
        print()
        sugg = self.graph.pivot_suggestions()
        if sugg:
            print(f"   \033[33m\033[1mPIVOT HINTS:\033[0m")
            for s in sugg:
                print(f"     \033[33m›\033[0m {s}")
        print()

    def show_mitre(self):
        """v7.1 — display OSINT categories exercised this session.

        Variable names still say 'attack' for backwards compat with the
        Ares skeleton (MITRE_TECHNIQUES, attack_id, etc.) but the
        contents are repurposed as OSINT category mappings — see the
        comment on MITRE_TECHNIQUES."""
        print(f"\n{header_box('  OSINT CATEGORY COVERAGE  ', color='31')}\n")
        if not self.attack_techniques_used:
            print("   \033[90m   No OSINT categories recorded yet.\033[0m\n")
            return
        # Group by tactic
        by_tactic: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
        for tid, info in self.attack_techniques_used.items():
            by_tactic.setdefault(info["tactic"], []).append((tid, info))
        for tactic in sorted(by_tactic.keys()):
            print(f"   \033[31m\033[1m{tactic}\033[0m")
            for tid, info in sorted(by_tactic[tactic],
                                     key=lambda x: -x[1]["count"]):
                print(f"     \033[97m{tid}\033[0m  {info['name']:<42} "
                      f"\033[90m×{info['count']}\033[0m")
            print()
        total = sum(i["count"] for i in self.attack_techniques_used.values())
        print(f"   \033[90m   {len(self.attack_techniques_used)} unique technique(s), "
              f"{total} total invocation(s)\033[0m\n")

    def show_dashboard(self):
        """v7.1 — concise session status panel."""
        v_count = len(self.ptt.get_verified())
        u_count = len(self.ptt.get_unverified())
        nodes_done = sum(1 for n in self.ptt.nodes.values() if n.status == "done")
        nodes_total = len(self.ptt.nodes)
        target = (self.target_info.get("ip") or
                  self.target_info.get("domain") or "—")
        elapsed = datetime.datetime.now() - self.session_start
        elapsed_str = str(elapsed).split(".")[0]
        scope_state = ("\033[32mON\033[0m" if self.scope.enabled
                       else "\033[90moff\033[0m")
        print(f"\n{header_box('  SESSION DASHBOARD  ', color='35')}\n")
        print(f"   \033[97mTarget       :\033[0m {target}")
        print(f"   \033[97mElapsed      :\033[0m {elapsed_str}")
        print(f"   \033[97mAgent        :\033[0m {AGENT_SPECS[self.current_agent]['icon']} "
              f"{AGENT_SPECS[self.current_agent]['name']}")
        print(f"   \033[97mModel        :\033[0m {self._current_model_name()}")
        print(f"   \033[97mPTT progress :\033[0m {nodes_done}/{nodes_total} nodes done")
        print(f"   \033[97mFindings     :\033[0m \033[32m{v_count}\033[0m verified, "
              f"\033[33m{u_count}\033[0m unverified")
        print(f"   \033[97mATT&CK techs :\033[0m {len(self.attack_techniques_used)} unique")
        print(f"   \033[97mGraph        :\033[0m {self.graph.summary()}")
        print(f"   \033[97mScope (RoE)  :\033[0m {scope_state}")
        if self.context_mgr.tokens_saved_estimate > 0:
            print(f"   \033[97mTokens saved :\033[0m "
                  f"~{self.context_mgr.tokens_saved_estimate:,} (smart context)")
        print()

    def show_help(self):
        print(
            f"\n   \033[33m\033[1mZEUS v{VERSION}\033[0m"
            f"   \033[90mby The Priest\033[0m\n"
            f"   Model      : \033[97m{self._current_model_name()}\033[0m\n"
            f"   This host  : \033[97m{self.lhost}\033[0m\n"
            f"   Agents     : \033[97m{len(AGENT_SPECS)}\033[0m  "
            f"(strategist, intake, socialite, postman, caller, "
            f"registrar, cartographer, archivist, dorker, ledger, "
            f"reporter)\n"
            f"   Workflows  : \033[97m{len(WORKFLOWS)}\033[0m\n"
            f"   Tools      : \033[97m{len(TOOL_DISPATCH)}\033[0m structured OSINT builders\n"
            f"   Mode       : \033[32mAUTO_MODE on\033[0m  "
            f"(no y/n/q · cap {MAX_AUTO_TURNS} turns / "
            f"{MAX_WALL_CLOCK_SECONDS//60} min)\n"
            f"   Persistence: \033[33mNONE\033[0m  "
            f"(RAM only · /tmp/zeus_<pid>/ wiped on exit)\n"
            f"   Refusals   : \033[31mhard-coded\033[0m  "
            f"({len(OSINT_REFUSE_PATTERNS)} illegal-OSINT patterns)\n\n"
            "   Zeus runs autonomously after intake.  No interactive\n"
            "   command loop — front-load everything you have, hit\n"
            "   enter, watch.  Final report prints to terminal so you\n"
            "   can copy what you want before exit (info wipes on quit).\n"
        )

    def show_agents(self):
        print(f"\n{header_box('  OSINT SPECIALISTS  ', color='33')}\n")
        for role, spec in AGENT_SPECS.items():
            print(f"   \033[{spec['color']}m{spec['icon']}  "
                  f"{spec['name']:<32}\033[0m  \033[90m({role})\033[0m")
        print()

    # ── REPL ──────────────────────────────────────────────────────

    def repl(self):
        # Cinematic boot
        print(BANNER)
        for ln in boot_sequence_lines():
            print(ln)
            time.sleep(0.04)
        print()
        print(speakers_legend())
        print()

        # Front-loaded intake.  If aborted, exit immediately.
        ok = self.extensive_intake()
        if not ok:
            say_zeus("Intake cancelled.  Nothing to investigate — exiting.")
            self._cleanup_ramdir()
            return

        # Show what Zeus is about to do
        ti = self.target_info
        ident = ti.get("identifiers") or {}
        ident_count = sum(
            (len(v) if isinstance(v, list) else (1 if v else 0))
            for v in ident.values()
        )
        print()
        say_zeus(f"Investigation begins — lane={ti['lane']}, "
                 f"subject={ti['subject_type']}, "
                 f"{ident_count} identifier(s) provided.")
        say_dim(f"Caps: max {MAX_AUTO_TURNS} turns / "
                f"{MAX_WALL_CLOCK_SECONDS//60} min wall-clock.  "
                f"AUTO_MODE on — no y/n/q gates.")
        print()

        # Build the initial objective from intake
        seed_label = (
            ident.get("real_name") or ident.get("legal_name") or
            ident.get("primary") or
            (ident.get("addresses") or [None])[0] or
            (ident.get("handles") or [None])[0] or
            "(unnamed subject)"
        )
        # Compose a kickoff prompt that bundles every identifier the
        # operator provided so the strategist has full context.
        kickoff_lines = [
            f"OSINT INVESTIGATION OBJECTIVE",
            f"Lane: {ti['lane']}",
            f"Subject type: {ti['subject_type']}",
            f"Subject: {seed_label}",
            f"",
            f"DECLARED IDENTIFIERS:",
        ]
        for k, v in ident.items():
            if not v:
                continue
            if isinstance(v, list):
                kickoff_lines.append(f"  {k}:")
                for item in v:
                    kickoff_lines.append(f"    - {item}")
            else:
                kickoff_lines.append(f"  {k}: {v}")
        kickoff_lines.append("")
        kickoff_lines.append(
            "Begin enumeration.  Pivot every new identifier you "
            "discover.  Stop at depth 2 unless the lane is "
            "threat-actor.  Refuse anything that violates the OSINT "
            "refuse list.  Emit WORKFLOW_COMPLETE when nothing more "
            "useful to add."
        )
        kickoff = "\n".join(kickoff_lines)

        # Run the autonomous agent loop
        try:
            self._agent_loop(kickoff, workflow_key=None)
        except KeyboardInterrupt:
            print()
            say_warn("Investigation interrupted by Ctrl+C — generating "
                     "partial report.")

        # Final report — terminal only, no disk
        print()
        say_zeus("Investigation complete.  Generating report...")
        self._generate_report()

        # Wipe RAM + ephemeral working dir
        self._cleanup_ramdir()
        say_zeus("RAM wiped.  Session over.")

    def _cleanup_ramdir(self):
        """rm -rf /tmp/zeus_<pid>/ if it exists.  Best-effort — every
        artifact lived under there so the operator's promise of 'info
        gone when I close it' is kept."""
        try:
            if self.logfile:
                try:
                    self.logfile.close()
                except Exception:
                    pass
                self.logfile = None
        except Exception:
            pass
        try:
            if os.path.isdir(INSTALL_DIR):
                import shutil
                shutil.rmtree(INSTALL_DIR, ignore_errors=True)
        except Exception:
            pass
        try:
            if os.path.isfile(BOOT_LOCK):
                os.remove(BOOT_LOCK)
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════
# BANNER
# ═════════════════════════════════════════════════════════════════════

# Build the banner programmatically so colour escapes are unambiguous
# and we never lose them through editor copies.
def _build_banner() -> str:
    M  = "\033[33m"   # gold/yellow frame (Zeus / lightning)
    W  = "\033[97m"   # bright white logo
    G  = "\033[90m"   # grey detail
    C  = "\033[36m"   # cyan accent
    Y  = "\033[93m"   # bright yellow lightning
    B  = "\033[1m"    # bold
    R  = "\033[0m"    # reset
    KB = "\033[100m\033[97m"  # keycap inverse
    L  = lambda s: f"{M}│{R} {s}"

    lines = [
        "",
        f"{M}╭─────────────────────────────────────────────────────────────────╮{R}",
        L(f"{' '*65}") + f"{M}│{R}",
        L(f"        {W}███████╗███████╗██╗   ██╗███████╗{M}                          ") + f"{M}│{R}",
        L(f"        {W}╚══███╔╝██╔════╝██║   ██║██╔════╝{M}                          ") + f"{M}│{R}",
        L(f"        {W}  ███╔╝ █████╗  ██║   ██║███████╗{M}     {Y}⚡{M}                  ") + f"{M}│{R}",
        L(f"        {W} ███╔╝  ██╔══╝  ██║   ██║╚════██║{M}                          ") + f"{M}│{R}",
        L(f"        {W}███████╗███████╗╚██████╔╝███████║{M}                          ") + f"{M}│{R}",
        L(f"        {W}╚══════╝╚══════╝ ╚═════╝ ╚══════╝{M}                          ") + f"{M}│{R}",
        L(f"{' '*65}") + f"{M}│{R}",
        L(f"   {B}{W}AI OSINT AGGREGATOR{R}{M}  ·  {B}{C}v1.1{R}{M}                          ") + f"{M}│{R}",
        L(f"   {G}Bare-metal Kali NetHunter  ·  Operator: The Priest{M}          ") + f"{M}│{R}",
        L(f"   {G}third pillar · Athena finds · Ares defends · Zeus aggregates{M}") + f"{M}│{R}",
        L(f"{' '*65}") + f"{M}│{R}",
        L(f" {G}╭─{C} legal OSINT capabilities {G}──────────────────────────────╮{M}    ") + f"{M}│{R}",
        L(f" {G}│{R}  {W}⊕ Username Pivot   {G}sherlock · maigret · whatsmyname{R}    {G}│{M}    ") + f"{M}│{R}",
        L(f" {G}│{R}  {W}⊕ Email Triage     {G}holehe · gravatar · MX/SPF/DMARC{R}    {G}│{M}    ") + f"{M}│{R}",
        L(f" {G}│{R}  {W}⊕ Phone OSINT      {G}phoneinfoga (carrier+VoIP detect){R}   {G}│{M}    ") + f"{M}│{R}",
        L(f" {G}│{R}  {W}⊕ Domain Footprint {G}whois · subfinder · crt.sh · ASN{R}    {G}│{M}    ") + f"{M}│{R}",
        L(f" {G}│{R}  {W}⊕ Archive History  {G}wayback · gau · urlscan{R}              {G}│{M}    ") + f"{M}│{R}",
        L(f" {G}│{R}  {W}⊕ EXIF Metadata    {G}image GPS / camera / software{R}        {G}│{M}    ") + f"{M}│{R}",
        L(f" {G}│{R}  {W}⊕ Blockchain       {G}public chain queries (BTC/ETH/+){R}     {G}│{M}    ") + f"{M}│{R}",
        L(f" {G}│{R}  {W}⊕ AUTONOMOUS       {G}front-loaded intake · no y/n/q gates{R} {G}│{M}    ") + f"{M}│{R}",
        L(f" {G}╰───────────────────────────────────────────────────────────╯{M}    ") + f"{M}│{R}",
        L(f"{' '*65}") + f"{M}│{R}",
        L(f"   {Y}⚡{R}  {G}{B}RAM-only{R}{G} — info wiped on session end. {Y}⚡{R}{G}            {M}") + f"{M}│{R}",
        L(f"{' '*65}") + f"{M}│{R}",
        f"{M}╰─────────────────────────────────────────────────────────────────╯{R}",
        "",
    ]
    return "\n".join(lines)


BANNER = _build_banner()


# ═════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        session = ZeusSession()
        session.repl()
    except KeyboardInterrupt:
        print("\n\033[90mInterrupted.\033[0m")
        sys.exit(130)
