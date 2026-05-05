# Zeus — AI Legal OSINT Aggregator

**v1.0** · Bare-metal Kali NetHunter · Operator: The Priest · ⚡ RAM-only

Zeus is the third pillar of the Greek pantheon stack. Where **Athena**
finds the path in (offense) and **Ares** verifies you've closed it
(defense), **Zeus** aggregates everything legally findable about a
subject from public sources — read-only OSINT, no auth bypass, no
stolen credentials, no system mutation.

You feed Zeus an extensive intake of identifiers (name, emails,
phones, handles, URLs, image paths, PGP/SSH fingerprints, anything
else you have). Zeus runs autonomously — no `y/n/q` prompts during
the run — and prints a final report to your terminal. **Nothing
persists.** When you exit, `/tmp/zeus_<pid>/` gets `rm -rf`'d and
all findings are gone.

---

## What Zeus does

### 11 OSINT specialists

| Icon | Name | What it does |
|------|------|--------------|
| ♛ | **Strategist** | Routes the OSINT Task Tree, picks which agent runs next |
| 🪪 | **Intake** | Lane gate, identifier validation, refuses illegal lanes |
| 👤 | **Socialite** | Username/handle pivots — sherlock, maigret, whatsmyname, socialscan |
| 📮 | **Postman** | Email triage — holehe, gravatar, MX/SPF/DMARC, GitHub commit search |
| 📞 | **Caller** | Phone OSINT — phoneinfoga (carrier/country/line-type only) |
| 🌐 | **Registrar** | Domain — WHOIS, DNS, subfinder/amass passive, crt.sh, ASN |
| 🗺 | **Cartographer** | EXIF metadata, GPS coord surfacing (no auto-resolution) |
| 📚 | **Archivist** | Wayback Machine, archive.org, deleted-content recovery |
| 🔎 | **Dorker** | Curated Google + GitHub dorks for legitimate self-leak detection |
| 💰 | **Ledger** | Public blockchain (BTC/ETH/SOL/+) — balance, txs, ENS reverse |
| 📋 | **Reporter** | Consolidates findings, groups by OSINT category, prints the report |

### 12 workflows

Self-OSINT Footprint · Username Pivot · Email Exposure · Phone Triage
· Domain Due Diligence · Threat-Actor Handle Tracking · Bug-Bounty
Recon · Crypto Address Trace · Image Metadata · Wayback History Sweep
· Company Due Diligence · Document Leakage Hunt

### 46 structured OSINT tool builders

Every tool wraps a free, public-data source. Examples: `sherlock_run`,
`maigret_run`, `socialscan_run`, `whatsmyname_query`, `holehe_run`,
`gravatar_lookup`, `github_user_api`, `github_keys_check`,
`github_email_search`, `github_dork`, `reddit_user_info`,
`mastodon_lookup`, `bluesky_resolve`, `phoneinfoga_scan`,
`whois_lookup`, `dig_lookup`, `subfinder_passive`, `amass_passive`,
`crt_sh_query`, `reverse_ip_hackertarget`, `asn_lookup`,
`whatweb_passive`, `waybackurls_run`, `gau_run`, `wayback_check`,
`exiftool_run`, `exiftool_gps_only`, `btc_address_balance`,
`btc_address_txs`, `eth_address_balance`, `google_dork_curated`.

---

## Hard refusals

Zeus is **read-only public-data OSINT only.** It refuses:

- **Brute force / credential guessing** — hydra, medusa, hashcat, john, kerbrute, crackmapexec
- **Stolen credential dumps** — DeHashed credential queries, combolists, weleakinfo, snusbase
- **Real-time location** — cell-tower triangulation, IMSI catchers, SS7
- **Home / street address resolution** — explicit and pattern-matched
- **Stalkerware aggregators** — Spokeo, BeenVerified, TruePeopleSearch, Intelius, Pipl, Whitepages
- **Voter rolls** — jurisdiction-restricted, mostly stalking
- **Doxbin and dox sites**
- **Domestic-abuse-adjacent intent** — ex-partner tracking language patterns
- **Authenticated scrapers** — instaloader, instagram-scraper, twint
- **System mutation** — sudo, systemctl, iptables, ufw, fail2ban, kill, etc. (Zeus is OSINT not pentest)

47 refuse patterns hard-coded. There's no override flag. **Zeus refuses by design, not by configuration.**

---

## Lane gate

At session start, you pick one of six investigation lanes:

| Lane | Use case |
|------|----------|
| `self-osint` | Audit your own digital footprint (most defensible) |
| `threat-actor` | Track adversary handles + infrastructure (security research) |
| `journalism` | Public-interest investigation, justification logged in report |
| `due-diligence` | Entity / company focused, not personal |
| `bug-bounty` | Authorized program, scope file required |
| `training` | CTF / known-test-target, no live people |

The lane changes which agents run aggressively and which pivots
Zeus refuses. Self-OSINT is the strongest lane (you investigating
yourself); threat-actor avoids real-name attribution; due-diligence
on a person prompts for a public-interest justification before
proceeding.

---

## Install

Tested on Kali Linux NetHunter (sdm845, Phosh). Should work on any
Debian / Ubuntu / Arch system with Python ≥ 3.10.

```bash
git clone https://github.com/the-priest/zeus.git
cd zeus
chmod +x install.sh
./install.sh
```

The installer:

1. Detects your login shell, picks the right rc file.
2. Verifies Python 3.10+.
3. Installs `groq` and `networkx` (with `--break-system-packages` on PEP 668 systems).
4. Symlinks `/usr/local/bin/zeus` → `zeus.py` (or falls back to a shell alias if no sudo).
5. Picks up `GROQ_API_KEY` from your environment if Athena/Ares already set it; otherwise prompts.
6. Optionally prompts for a `GITHUB_TOKEN` (free PAT, raises GitHub API rate limit 60→5000/hr).
7. Detects existing `~/.athena` or `~/.ares` and tells you the three are designed to run together.

If the installer can't run, the manual route is:

```bash
pip install groq networkx --break-system-packages
export GROQ_API_KEY='your_key_here'
python3 zeus.py
```

### Optional OSINT CLI tools (graceful degradation)

Zeus runs without these but coverage is reduced:

```bash
pipx install sherlock-project maigret holehe socialscan
go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest
go install -v github.com/owasp-amass/amass/v4/...@master
sudo apt install libimage-exiftool-perl jq -y
```

When a tool is missing, Zeus tells you which one and gives the
install command in the dispatch error — same pattern as Athena/Ares.

---

## Usage

```bash
$ zeus
```

Boot sequence → intake form (lane → subject type → identifiers) →
autonomous run → terminal report.

### Intake (full identifier list for a person)

- Real name + aliases
- Year of birth
- Email addresses (multi-line)
- Phone numbers (multi-line)
- Usernames in `platform:handle` format (e.g. `github:thepriest`)
- Profile URLs
- City / state / country (NOT street address — Zeus refuses doxxing)
- Employer / institution
- Industry / profession
- Languages
- Image file paths (for EXIF extraction)
- PGP key fingerprint (pivots to public keyservers)
- SSH key fingerprint OR GitHub username (cross-checks `/USER.keys`)
- Free-text notes

For **company**, **domain**, **crypto-address**, or **threat-actor**
subjects, the form adapts.

### Autonomous run

Once intake is done, Zeus runs without prompting. Caps:

- **50 turns max** per session
- **15 minutes wall-clock max**
- Per-tool timeouts (sherlock 5min, holehe 3min, etc.)

You can `Ctrl+C` mid-run to stop early — Zeus prints a partial report
with whatever it found.

### Final report

Printed to terminal in colored sections:

- Header (subject, lane, duration, token savings)
- Intake identifiers (everything you provided)
- Analysis (LLM-generated summary)
- OSINT category coverage
- OSINT Task Tree (final state)
- Raw findings with provenance (every finding cites its source command)
- Disclaimer

**Copy what you want before quitting.** Once you exit, `/tmp/zeus_<pid>/`
is wiped and everything is gone.

---

## Files (during a session)

```
zeus.py                       # the whole agent, single file
/tmp/zeus_<pid>/              # ephemeral working dir, wiped on exit
/tmp/zeus_<pid>/logs/         # per-session log (also wiped on exit)
/tmp/zeus_<pid>.lock          # boot lock, removed on cleanup
```

**No files survive the session.** No `~/.zeus/`, no persistent reports,
no saved findings. By design.

---

## Pairing with Athena and Ares

All three tools share the same `GROQ_API_KEY`, the same Kali rig, the
same UI conventions (boxed turn output, status bar, OSINT/ATT&CK
tagging). They pair this way:

| Tool | Persistence | Mode | Mission |
|------|-------------|------|---------|
| **Athena** | `~/.athena/` | Manual y/n/q gates | Find the path in (offense) |
| **Ares** | `~/.ares/` | Manual y/n/q gates | Verify you've closed it (defense) |
| **Zeus** | NONE (RAM-only) | Autonomous, no gates | Aggregate legal OSINT |

Athena and Ares are persistent investigation tools you come back to.
Zeus is a one-shot aggregator — fire it, get a report, exit, gone.

---

## License

MIT — see [LICENSE](LICENSE) for the full text plus a
use-only-on-systems-and-subjects-you-have-authority-to-investigate
disclaimer. Personal project by The Priest.

**Use Zeus only on yourself, on people who've consented, on declared
threat-actor handles, on public companies, on bug-bounty targets in
declared scope, or on CTF training boxes. The tool refuses the
illegal cases, but the operator is responsible for staying inside
their own jurisdiction's law.**
