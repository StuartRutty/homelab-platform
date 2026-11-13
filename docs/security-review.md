# Security review — threat model, exposure audit, remediation

This is the sanitized summary of a standing security review (started Sep
2026, still maintained). Method and resolved findings are public here;
hostnames, per-host findings, and open items are not — publishing an attack
surface is not a security story.

## Threat model

Agreed before any tooling was touched:

- **The LAN is shared** — other people's devices are on it, under one Wi-Fi
  password — treat LAN peers as untrusted. Anything sent in plaintext on
  the LAN is sniffable.
- **The internet surface is every tunnel hostname.** Attackers are scanners,
  credential stuffers, and opportunistic bots.
- **Assets, in priority order:** personal data (contacts, notes, inventory,
  credentials, reading history), the media library, admin interfaces
  (container management above all), VPN control, and the infrastructure
  itself.
- **Owner's priorities, in his words:** simplicity, security, no leaking
  personal information.

## Project 1: HTTPS on the LAN

Before this project, services were plain HTTP inside the apartment — on a
shared network, logins were sniffable. The fix made the *same* URL work on
and off the LAN, encrypted in both directions:

- A custom proxy image (stock proxy + DNS plugin) issues **Let's Encrypt
  certificates via ACME DNS-01**, using an API token scoped to DNS edits on
  this single zone.
- All **27 service hostnames** were issued real certificates the day it
  deployed; renewal follows the identical path.
- Consequences beyond encryption: WebAuthn/FIDO2 works on the LAN (a real
  secure context), and LAN traffic never hairpins out to the public edge.

## Project 2: the exposure audit

Every public hostname was probed **through the real public edge** — not
read from config — because the two routinely disagree. 29 hostnames were
probed; setting aside test and placeholder names, the classification:

| Classification | Count | How it was established |
|---|---|---|
| Behind an identity gate at the edge | 4 | Probed; gate challenge confirmed |
| App login wall verified | 15 | Login surfaces checked individually — including one admin UI verified as *initialized* (not sitting in first-run setup) by its API's response semantics |
| Open by design, no personal data | 5 | Content reviewed; nothing personal served |
| Not routable from the internet | 2 | Probed; no ingress exists |
| Unresolved | 1 | A single-page app whose auth couldn't be enumerated from outside; **treated as unverified**, not assumed safe |

The audit also produced a full DNS inventory review, which caught the
routine debris (a leftover record advertising the proxy itself — deleted)
and one remote-access hostname exposing a raw address by design, which was
reviewed and accepted with compensating controls (key-pinned clients,
per-client credentials).

## Findings and remediations (all closed)

- **A plaintext VPN credential in 13 stale file copies.** Old backups and
  seed files carried the literal value. All copies scrubbed to environment
  references and the scrub *verified* — zero literal hits afterward. The
  remediation itself caused a brief incident: a substring-matched replace
  truncated the live value, which was recovered from the second host's copy
  after containment was verified. Lesson kept: bulk secret edits get
  byte-level verification, and a recovery source gets identified *before*
  the edit starts.
- **The backup password was reachable by every container.** It sat in the
  shared environment file every service symlinks. Removed; the backup job
  now reads it from machine-scope environment, where no container can.
- **Six third-party API keys inline in one app's config** — moved to that
  service's own secrets file, hash-verified as moved verbatim, and the
  service smoke-tested after recreation.
- **An abandoned serverless endpoint holding write-capable keys.** An
  internet-reachable worker, unmaintained for months, was found holding API
  keys with write access to multiple services — unmaintained code, publicly
  reachable, holding the keys to the kingdom: the exact inverse of least
  privilege. Retired; every key it held was rotated; and the design lesson
  (a gateway must carry only what it needs) shaped the agent gateway that
  replaced this class of integration.
- **Two "bypass for everyone" edge rules.** Created as a stopgap so an
  external integration could reach two app APIs, they let the whole
  internet reach those APIs past the edge's identity layer. Closed and
  replaced by the single-credential gateway (see
  [agent-gateway.md](agent-gateway.md)); verified from the public edge that
  anonymous API requests now fail.
- **Expired API tokens** for a household app were purged, and the
  integration was re-issued as a non-admin, purpose-named user reachable
  only through the gateway.

## Detection: intrusion prevention behind a tunnel

CrowdSec parses the proxy's access logs and bans hostile IPs at the proxy.
Deploying it behind a tunnel surfaced a design problem worth the write-up:

- **All tunnel traffic shares one source IP** — the tunnel client's. A naive
  bouncer that bans it takes the entire public surface down. Internal and
  loopback ranges are therefore whitelisted from ban evaluation by design.
- **The container runtime NATs everything anyway** — the proxy never sees
  real LAN client IPs on published ports. Real client identity exists only
  in headers set by the edge. The proxy was configured with trusted-proxy
  ranges and client-IP headers so that detection and enforcement both key
  on the same computed client IP — symmetric, and verified end-to-end:
  ban → 403, unban → 200, tunnel unaffected throughout.
- **A privacy fix rode along:** access logs no longer retain full URLs
  (query strings are redacted at write time — they can carry tokens).
  Historical logs were purged, and backup snapshots containing the old logs
  were rewritten and pruned so the redaction is real, not cosmetic.

## Standing practice

The review is a living checklist, not a one-off: a per-host remediation
backlog is worked in dedicated sessions, fixes are verified through the
real edge after they land, and the exposure classification is re-run when
the surface changes. A backup-integrity item from this same review grew
into the monthly automated restore drill documented in
[backup-and-dr.md](backup-and-dr.md).
