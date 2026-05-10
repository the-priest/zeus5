# Zeus — Legal OSINT Search Engine

```
███████╗███████╗██╗   ██╗███████╗
╚══███╔╝██╔════╝██║   ██║██╔════╝
  ███╔╝ █████╗  ██║   ██║███████╗   ⚡
 ███╔╝  ██╔══╝  ██║   ██║╚════██║
███████╗███████╗╚██████╔╝███████║
╚══════╝╚══════╝ ╚═════╝ ╚══════╝
```

**Fast public-OSINT search.** Give it a name, handle, or email — it
finds every public profile that actually exists for that person and
shows you only the ones it can verify by HTTP fetch.

Single file. ~1300 lines. Runs in 60-120 seconds. Designed for
bare-metal Kali NetHunter (sdm845 / OnePlus 6 / Phosh) but works on
any Linux with Python 3.10+.

Built by **The Priest** as the third pillar of a Greek-pantheon stack:

| Tool | Role | Repo |
|------|------|------|
| Athena | offensive recon agent | [athena5](https://github.com/the-priest/athena5) |
| Ares | defensive audit agent | [ares5](https://github.com/the-priest/ares5) |
| **Zeus** | **legal OSINT aggregator** | **(this repo)** |

---

## What it does

```
intake → enumerate → verify → enrich → report
```

1. **Intake** — 4 fields: name, handle, email, country. Press
   Enter to skip any field.
2. **Enumerate** — runs `sherlock`, `maigret`, `holehe`, and
   Gravatar in parallel. If you only give a name, Zeus
   generates 3 likely handle variants (`lukakrajina`,
   `luka.krajina`, `lkrajina`).
3. **Verify** — every candidate URL is **fetched live**. Zeus
   reads the page and checks the handle is actually present in
   `<title>` or `og:title`. Soft-404 platforms (NationStates,
   Discord, hudsonrock API, etc.) auto-drop. This is what kills
   the false-positive flood that sherlock alone produces.
4. **Enrich** — every confirmed profile gets its bio, display
   name, location, profile photo, and linked external accounts
   extracted from meta tags. GitHub gets an extra API call for
   bio, company, location, blog, repos, followers, created_at.
5. **Report** — terminal output, grouped by verification status:
   - `── CONFIRMED PROFILES ──` (verified by HTTP fetch)
   - `── GITHUB DEEP-DIVE ──` (with freshness flag for new accounts)
   - `── GRAVATAR ──` (linked accounts pulled from the profile)
   - `── EMAIL REGISTERED ON ──` (from holehe)
   - `── UNVERIFIED LEADS ──` (handle in URL but couldn't confirm body)
   - `── AI SYNTHESIS ──` (optional Groq-written paragraph)

Everything runs **RAM-only**. Nothing is written to disk. The
ephemeral workdir `/tmp/zeus_<pid>/` is wiped on exit.

---

## What it deliberately doesn't do

- **No authentication bypass.** Public web only.
- **No credential-dump queries.** Won't query DeHashed for passwords.
- **No real-time location.** Won't try to pin someone's home.
- **No stalkerware aggregators.** Won't query Spokeo / BeenVerified.
- **No system modification.** Read-only.
- **No disk persistence.** Findings live in RAM and vanish when
  you exit. Copy what you need before quitting.

If you point it at a person who keeps a private digital footprint
(locked accounts, single platform, throwaway handles), Zeus will
honestly tell you it found nothing rather than fabricate results.

---

## Installation

### Quick install (Kali / Ubuntu / Debian)

```bash
git clone https://github.com/the-priest/zeus5.git
cd zeus5
./install.sh
```

### Manual

```bash
# Python deps
pip install -r requirements.txt --break-system-packages

# OSINT CLIs (Zeus shells out to these)
pipx install sherlock-project
pipx install maigret
pipx install holehe

# Groq API key (for the optional AI summary)
export GROQ_API_KEY=gsk_...
echo 'export GROQ_API_KEY=gsk_...' >> ~/.bashrc

# Symlink for the `zeus` command
mkdir -p ~/.local/bin
ln -sf "$PWD/zeus.py" ~/.local/bin/zeus
chmod +x ~/.local/bin/zeus
```

---

## Usage

```bash
zeus
```

Then answer the 4 intake prompts (any one is enough):

```
   Name (real or alias):     Jane Doe
   Username / handle:        janedoe
   Email address:            jane@example.com
   Country (optional):       Ireland
```

Zeus runs the pipeline and prints the report directly to the
terminal. No flags, no subcommands, no config file.

### Example output

```
╔══════════════════════════════════════════════════════════════════════╗
║  ZEUS v5.1  ·  OSINT SEARCH REPORT                                   ║
╚══════════════════════════════════════════════════════════════════════╝

    3 CONFIRMED PROFILES

  Subject:    Jane Doe
  Country:    Ireland
  Searched:   janedoe, j.doe, jdoe
  Time:       47s

  ── CONFIRMED PROFILES ── verified by HTTP fetch

    ✓  https://github.com/janedoe
       name:     Jane Doe
       location: Dublin
       bio:      Backend engineer.  Rust / Go.
       links:    https://twitter.com/janedoe, https://janedoe.dev

    ✓  https://www.reddit.com/user/janedoe

    ✓  https://bsky.app/profile/janedoe.bsky.social

  ── GITHUB DEEP-DIVE ──

    https://github.com/janedoe
       name:     Jane Doe
       bio:      Backend engineer
       location: Dublin
       blog:     https://janedoe.dev
       joined:   2019-03-12 (2615 days ago)
       repos:    47, followers: 312
```

---

## Configuration

All controlled by constants at the top of `zeus.py`:

| Constant | Default | What it does |
|----------|---------|--------------|
| `TOTAL_TIMEOUT_SEC` | `240` | hard wall-clock cap |
| `ENUM_TIMEOUT` | `90` | sherlock+maigret+holehe |
| `PER_FETCH_TIMEOUT` | `7` | one HTTP fetch |
| `VERIFY_PARALLEL` | `8` | concurrent verifier threads |
| `ENRICH_PARALLEL` | `4` | concurrent enrich threads |

Edit them in-place if you want longer searches or more aggressive
parallelism.

---

## Platform-specific verification

Zeus has hardcoded verification rules for high-signal platforms
where it knows what a real profile page looks like:

```
github · gitlab · bsky · reddit · youtube · medium · twitter / x
soundcloud · instagram · tiktok · linkedin · facebook · stackoverflow
lichess · letterboxd · spotify · keybase · tumblr · ...
```

For everything else it falls back to generic `<title>` / `og:`
meta tag matching.

Soft-404 platforms (sites that return HTTP 200 for any username,
making sherlock think every probe is a hit) are hardcoded as
**auto-noise**. Currently 30+ platforms including: Discord,
NationStates, hudsonrock API, Rarible, Star Citizen, Russian
forums (phpRU / svidbook / velomania / igromania / opennet),
codesnippets fandom, wikidot, interpals, mercadolivre, 1337x,
couchsurfing, tetr.io.

If you find another platform that's always-200, open an issue or
add it to `SOFT_404_PLATFORMS` in `zeus.py`.

---

## Why not just use sherlock directly?

Sherlock returns 50-150 "hits" for any common username, most of
which are false positives (server returned 200 but no real
profile exists at the URL). Out of the box, sherlock gives you a
wall of URLs with no way to tell which are real.

Zeus runs sherlock, then fetches each URL and verifies the
handle is on the page. Typical reduction: 80 sherlock hits →
5-15 confirmed profiles.

The same principle applies to maigret (which also tries
underscore/dash variants and frequently doubles up findings).

---

## Tools Zeus shells out to

| Tool | Used for | Required? |
|------|----------|-----------|
| `sherlock` | username → candidate URLs | recommended |
| `maigret` | username → candidate URLs (different DB) | recommended |
| `holehe` | email → registered services | recommended |
| `python3` | everything else | yes |

If a tool is missing, Zeus warns at startup and skips that step.
You still get the others.

---

## API keys

- **Groq** — `GROQ_API_KEY`, free tier. Used **only** for the
  optional AI summary paragraph at the end of the report.
  Zeus works fine without it; the AI paragraph is skipped.
- **GitHub** — `GITHUB_TOKEN`, optional. Without it, the
  GitHub user API call still works but is rate-limited to 60
  requests/hour from one IP. With a token: 5000/hour.

No paid APIs. No HIBP (paid since 2024). No Spokeo. No
BeenVerified. No DeHashed.

---

## Tested on

- Kali Linux Rolling (aarch64) on OnePlus 6, NetHunter,
  kernel `6.6.58-sdm845-nh`, Phosh UI
- Linux Mint Cinnamon (x86_64), Dell Latitude E5540
- ThinkPad X395 dedicated Kali SSD

---

## License

MIT — see `LICENSE`.

---

## Acknowledgements

Shells out to `sherlock-project/sherlock`, `soxoj/maigret`,
`megadose/holehe`. None of those projects endorse this tool.
The verification + dedup + reporting layer is mine.
