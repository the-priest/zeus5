# Zeus — User Manual

**v1.0** · Bare-metal Kali NetHunter · Operator: The Priest · ⚡ RAM-only

This is the operator's manual. It assumes you've installed Zeus
(`./install.sh`) and have `GROQ_API_KEY` set. If not, read README.md
first.

---

## 1. The mental model

Zeus behaves nothing like Athena or Ares mid-session. Athena and
Ares ask you `y/n/q` before every command — interactive, you stay
in the loop. Zeus is the opposite: **front-load everything you have,
then let it run.**

The session has three phases:

1. **Intake** — Zeus asks for the lane, subject type, and every
   identifier you can give it. Multi-line where appropriate.
2. **Autonomous run** — agents fire, tools dispatch, findings extract,
   no prompts. Capped at 50 turns / 15 minutes wall-clock.
3. **Terminal report** — final report prints inline. You copy what
   you want. You quit. Everything in `/tmp/zeus_<pid>/` gets wiped.

The state that drives Zeus is the **OSINT Task Tree (OTT)**. Each
node has a phase (intake / social / email / phone / domain / image /
archive / dork / crypto / report) and the phase determines which
specialist agent runs.

Findings are extracted from raw subprocess output. The AI's text
never enters the case file. Every finding records the exact shell
command that produced it. No hallucinations.

---

## 2. The voices

| Voice | Symbol | Who |
|-------|--------|-----|
| `⚔ priest` | magenta | You — operator |
| `◈ ZEUS` | cyan | Framework (boot, status, errors) |
| Per-agent symbols | per-agent colour | Active OSINT specialist |
| `▌` | grey | Shell command about to run |
| `✓ / ⚠ / ✕` | green / yellow / red | Success / warn / error |

---

## 3. First run

```bash
$ zeus
```

You see the gold ZEUS banner, the boot sequence, then a warning:

> ⚠  Zeus is read-only public-data OSINT only.
> ⚠  It refuses anything that bypasses authentication, brute-forces,
> ⚠  touches stolen credential dumps, asks for real-time location or
> ⚠  street-level home addresses, scrapes non-public data, or
> ⚠  modifies the local system.
> ⚠  All findings stay in RAM and vanish when you exit.

Then the lane gate.

---

## 4. The lane gate (mandatory)

Pick **one** at session start. The lane changes Zeus' behaviour
fundamentally — what pivots it tries, what it refuses, what it
considers in scope.

| # | Lane | What it's for |
|---|------|---------------|
| 1 | `self-osint` | Audit your own digital footprint. **Most defensible.** |
| 2 | `threat-actor` | Track adversary handles + infrastructure. Pairs with Ares. |
| 3 | `journalism` | Public-interest investigation. Justification logged in report. |
| 4 | `due-diligence` | Entity / company focused. On a person → asks for justification. |
| 5 | `bug-bounty` | Authorized program. Refuses out-of-scope assets. |
| 6 | `training` | CTF / known test target. No live people. |

Then the subject type:

| # | Subject | Notes |
|---|---------|-------|
| 1 | `person` | Individual human |
| 2 | `company` | Legal entity, business, organisation |
| 3 | `domain` | Web property only |
| 4 | `crypto-address` | BTC / ETH / SOL / etc. wallet |
| 5 | `threat-actor` | Adversary handle/infrastructure cluster (no real-name) |

---

## 5. Identifier intake — extensive

### For a person:

- **Real name** (or alias if pseudonymous subject)
- **Aliases / nicknames** (multi-line)
- **Year of birth** (year only — disambiguates without doxxing)
- **Email addresses** (multi-line, all you have)
- **Phone numbers** (multi-line, E.164 format if possible)
- **Usernames** in `platform:handle` format, e.g. `github:thepriest`,
  `reddit:foo`, `mastodon:bar@social.example` (multi-line)
- **Profile URLs** (multi-line)
- **City / state / country** — *NOT* street address (Zeus refuses)
- **Employer / institution**
- **Industry / profession**
- **Languages**
- **Image file paths** (multi-line) — for EXIF extraction. GPS coords
  are surfaced as findings; Zeus does NOT auto-resolve them to street
  addresses, you paste the coords into a map yourself.
- **PGP key fingerprint** — pivots to public keyservers
- **SSH key fingerprint OR GitHub username** — cross-checks
  `github.com/USER.keys`
- **Free-text notes**

### For a company:

- Legal name + DBAs + primary domain(s) + country + ticker (if public)
+ industry + executives + subsidiaries + notes

### For a domain:

- Primary + known subdomains + tech hints + ASN if known + notes

### For a crypto address:

- Address(es) + network (BTC/ETH/SOL/etc.) + notes

### For a threat actor:

- Handles per platform + known infra (domains/IPs) + attributed
malware family + threat-intel feed source + notes

**Tip:** the more you provide upfront, the more pivots Zeus has to
work with. A handle alone gives Zeus a username sweep. A handle +
email + domain gives it three branches and lets it correlate.

---

## 6. The autonomous run

Once intake completes, Zeus says:

```
◈ ZEUS  Investigation begins — lane=self-osint,
        subject=person, 7 identifier(s) provided.
        Caps: max 50 turns / 15 min wall-clock.
        AUTO_MODE on — no y/n/q gates.
```

Then it runs. Each turn renders as a stack of boxes:

- **TURN N** header (target / agent / model / findings count)
- **THOUGHT** — agent's reasoning
- **DISPATCH** — tool builder + arguments
- **COMMAND** — shell string + confidence pill + OSINT category tag
- **EXECUTING** with timeout indicator
- **RESULT** with raw output (truncated)
- **FINDINGS +N** when new findings extracted

There's no `y/n/q` prompt anywhere. If a command fails or times out,
Zeus logs the failure and moves on (auto-skip on failure — the
recommended mode). If you really want to abort early, hit `Ctrl+C` —
Zeus catches it, prints a partial report, then exits.

### What the agents do

The strategist routes between specialists based on the OTT phase. A
typical self-osint run on a person might look like:

```
TURN 1 → strategist routes to socialite
TURN 2 → socialite runs sherlock_run on declared handle
TURN 3 → socialite finds GitHub handle in sherlock output, queues pivot
TURN 4 → socialite runs github_user_api → finds public email
TURN 5 → strategist routes to postman
TURN 6 → postman runs holehe_run on email → finds 12 registered services
TURN 7 → strategist routes to registrar
TURN 8 → registrar runs whois_lookup on declared domain
TURN 9 → registrar runs subfinder_passive → finds 4 subdomains
TURN 10 → ...etc...
TURN N → strategist emits WORKFLOW_COMPLETE
```

---

## 7. The OSINT refuse list

Hard-coded. **Never overrideable.** 47 patterns covering:

- **Brute force / cred-guess**: `hydra`, `medusa`, `hashcat`, `john`,
  `kerbrute`, `crackmapexec`, `nxc smb/ssh/winrm/rdp`, `patator`, `ncrack`
- **Auth bypass**: any `login.{0,8}(bypass|crack|skip)`,
  `captcha.{0,8}(solve|bypass)`
- **Stolen credential dumps**: `dehashed.*[?&](password|hash)=`,
  `combolists`, `weleakinfo`, `snusbase`, `leakcheck`, `breach.compilation`
- **Real-time tracking**: `realtime.*track/locate/location/position`,
  `cell.tower`, `imsi.catcher`, `ss7`, `sms.intercept`
- **Address resolution**: `(home|street).{0,8}address.{0,8}(lookup|find|search)`
- **Stalkerware aggregators**: `spokeo`, `truepeoplesearch`,
  `beenverified`, `intelius`, `pipl`, `zaba`, `peoplefinder`,
  `whitepages.com`
- **Voter rolls**: `voter.{0,4}(roll|registration)`
- **Doxbin**: `doxbin`, `doxxx?`, `dox.{0,4}drop`
- **CSAM / minors**: `csam`, `child.{0,6}(porn|sex)`,
  `minor.{0,6}(track|locate|find.address)`
- **Domestic abuse adjacent**: `ex.{0,4}(girlfriend|boyfriend|wife|husband|partner)` +
  any tracking verbs nearby in either direction
- **Authenticated scrapers**: `instaloader`, `instagram-scraper`,
  `twint`
- **System mutation**: any `sudo`, `systemctl`, `iptables`, `ufw`,
  `fail2ban`, `kill`, `usermod`, `passwd`, `useradd`, `userdel`,
  `chattr`, `auditctl` — Zeus is OSINT, not pentest

If a refused command somehow gets through to `run_command`, it dies
with an `OSINT REFUSE-LIST — REFUSED` panel. The session continues.

---

## 8. The report

Printed to terminal at session end. Sections:

```
╔═══════════════════════════════════════════════════════════════╗
║  ZEUS  v1.0  ·  OSINT INVESTIGATION REPORT                  ║
╚═══════════════════════════════════════════════════════════════╝

  Subject:       <seed_label>
  Lane:          <lane>
  Subject type:  <person|company|domain|crypto-address|threat-actor>
  Operator:      The Priest
  Started:       <iso>
  Duration:      <hh:mm:ss>
  This host:     <lhost>
  Findings:      N verified · M unverified
  Tokens saved:  ~K  (smart context)

  ─── INTAKE IDENTIFIERS ───
    real_name:  ...
    emails:
       • ...
    handles:
       • ...

  ─── ANALYSIS ───
    <LLM-written summary, paragraphs>

  ─── OSINT CATEGORY COVERAGE ───
    SOCIAL_PRESENCE × 5
    DOMAIN_FOOTPRINT × 3
    ARCHIVE_HISTORY × 2
    ...

  ─── OSINT TASK TREE (final) ───
    [0] OSINT investigation [self-osint]: ...
    [1] Username sweep ✓
    [2] Email triage ✓
    [3] Domain footprint ✓
    ...

  ─── RAW FINDINGS ───
    ✓ handle    github:thepriest      [SOCIAL_PRESENCE]
        source: sherlock --print-found thepriest...
    ✓ domain    example.com           [DOMAIN_FOOTPRINT]
        source: subfinder -d ...
    ...

  ───────────────────────────────────────────────────────────────
  All findings above are PUBLIC OSINT only.  Lane: <lane>.
  Retained in RAM only — gone the moment Zeus exits.
  No data was written to disk.  Copy what you want NOW.
  ───────────────────────────────────────────────────────────────
```

**Copy what you want before exit.** Zeus closes the terminal session,
runs `rm -rf /tmp/zeus_<pid>/`, and that's it. No saved log, no saved
report, no `~/.zeus/`.

---

## 9. Sudo, persistence, scope — none of these apply to Zeus

These are present in Athena/Ares but explicitly disabled in Zeus:

- **Sudo** — Zeus refuses every command containing `sudo`. OSINT
  doesn't need root.
- **`~/.zeus/`** — doesn't exist. Working dir is `/tmp/zeus_<pid>/`,
  wiped on exit.
- **Scope.json (RoE)** — Zeus uses the lane gate instead. The lane
  enforces what the tool will and won't do.
- **Double-confirm** — Zeus has no system-mutation commands to gate.
  Anything that would mutate is in the hard-refuse list.

---

## 10. Pairing with Athena and Ares

Same `GROQ_API_KEY` works for all three. Run them side-by-side on
one device:

| Phase of work | Tool to use |
|---------------|-------------|
| Pentesting an authorized target | **Athena** |
| Hardening / IR on your own host | **Ares** |
| OSINT on yourself / a threat actor / a company | **Zeus** |

Athena's findings persist in `~/.athena/`. Ares' in `~/.ares/`.
Zeus' don't persist at all — by design. Zeus is fire-and-forget.

---

## 11. Troubleshooting

**`zeus: command not found`**

Source your rc: `source ~/.bashrc` (or `~/.zshrc`). The installer's
final line tells you which.

**`FATAL: GROQ_API_KEY not set`**

```bash
export GROQ_API_KEY='your_key_here'
```

Add it to your shell rc to make it persistent. Or re-run
`./install.sh` and it'll re-prompt.

**Tool not installed (e.g. sherlock not found)**

Zeus tells you the install command in the dispatch error. Most are
one-liners:

```bash
pipx install sherlock-project maigret holehe socialscan
```

Zeus skips that branch and continues with what it has.

**GitHub branch is rate-limited**

Add a free GitHub token:

```bash
export GITHUB_TOKEN='ghp_xxxxxxxxxxxx'
```

Generate at https://github.com/settings/tokens → Generate new token
(classic) → check `public_repo` and `read:user` scopes only.

**Run hits the 15-minute cap before finishing**

Increase `MAX_WALL_CLOCK_SECONDS` in `zeus.py` (top of file, in the
PATHS / LIMITS / MARKERS section). For deep investigations 30 minutes
is reasonable. Zeus stops gracefully and reports what it has.

**Run finishes too fast with sparse findings**

Provide more identifiers in intake. The bottleneck on free-only OSINT
is usually that you gave it one seed when you could have given it
five. Zeus pivots from what you give it; if you give it more, it
goes further.

---

## 12. The OSINT discipline (philosophy)

A few lines from KB section 1:

> Every finding is public, attributable, and reproducible. No private
> data, no auth bypass, no stolen dumps. If a tool wants a credential
> to "see more," that's the line — Zeus stops there.
>
> Three discipline rules:
> 1. **PIVOT** — Every identifier is a seed for the next.
> 2. **CORROBORATE** — One source is a lead, two is a finding.
> 3. **PRESERVE** — Public sources can disappear. Capture wayback
>    URLs and snapshot timestamps as proof-of-existence at time of search.
>
> When in doubt: refuse, log the reason, move on.
