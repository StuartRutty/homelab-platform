# Homelab Platform: a two-host container platform with real backups, a written threat model, and an agent gateway

I'm a delivery and technical program manager. This repo documents the
self-hosted platform I run at home — not as a hobby tour, but as an
operations case study: how it's built, how it's backed up, how its exposure
was audited, and how it was extended for AI agent clients without weakening
any of that.

Sanitized public version: patterns, decisions, and verified numbers.
Hostnames, addresses, credentials, and personal-data details are omitted by
design.

## The platform at a glance

| | |
|---|---|
| Hosts | 4, two of them servers — an ops host (low-power mini PC, 16 GB) and a media host (Ryzen 7, 32 GB, mid-range GPU), plus a Linux desktop and a living-room PC |
| Running containers | 50+ across the two server hosts |
| Ingress | **One** reverse proxy for everything; **zero inbound ports** on the router |
| Public edge | An outbound-only tunnel; the same URLs work on and off the LAN, with real certificates in both directions |
| Backups | Nightly snapshots from both server hosts into one encrypted, append-only repo; retention 7 daily / 4 weekly / 6 monthly |
| Restore assurance | A restore drill that runs **itself monthly**: integrity check plus an actual restore of live data, verified, then cleaned up |
| Unattended assurance | A weekly container vulnerability scan across both hosts, a weekly fleet patch/CVE sweep, a weekly automated audit of public exposure against the proxy config, and a monthly scheduler-hygiene sweep |
| Documentation | 23 markdown docs, git-versioned; the deployment pattern is a template, not folklore |

## Start with the interesting parts

- **[Incident: VPN egress outage](docs/incident-vpn-outage.md)** — a
  post-mortem: a provider's regional server pool died, a health-check
  sidecar turned it into a restart flap, and the fix taught a lasting lesson
  about editing YAML with the wrong tool.
- **[Backup & disaster recovery](docs/backup-and-dr.md)** — the strongest
  story here. Everyone has backups; this one proves them monthly, on a
  schedule, and the coverage audit has a quarantine-then-delete discipline
  behind it.
- **[Security review](docs/security-review.md)** — a written threat model, an
  exposure audit that probed every public hostname through the real edge and
  classified what it found, and the remediation trail: plaintext secrets
  scrubbed from stale copies, an abandoned public endpoint retired with its
  keys rotated, and an intrusion-prevention layer that had to be redesigned
  around how a tunnel hides client IPs.
- **[Architecture](docs/architecture.md)** — the shape: tunnel → single
  reverse proxy → containers, split-horizon DNS, and the constraint that
  shaped all of it (no cross-host container networking, so nothing publishes
  dozens of LAN ports).
- **[Deployment pattern](docs/deployment-pattern.md)** — the nine-step
  template every new service follows, including the DNS failure that bit
  twice (a wildcard record that answers A queries while returning NXDOMAIN
  for everything else, which per RFC 8020 poisons the whole name — on mobile
  clients only, naturally) and the sync check that now gates every deploy.
- **[The agent gateway](docs/agent-gateway.md)** — how an external AI
  assistant got useful access to this lab with exactly one credential,
  per-service credentials it never sees, allowlisted routes — and two routes
  deliberately narrowed or refused because the upstream semantics were
  unsafe to expose.

## Design principles that recur

1. **One ingress pattern.** Every service sits behind the same proxy, on a
   private container network, publishing no ports of its own. New services
   are a folder, a proxy block, a DNS line, and a backup entry — the pattern
   doc is the whole onboarding.
2. **The edge is outbound-only.** Nothing listens on the router. Public
   reachability is a per-service decision made in the tunnel config, never
   an accident of a published port.
3. **Backups are a claim you verify.** Snapshot counts are trivia; the
   monthly automated restore — with content-level integrity checks — is the
   evidence.
4. **Audit through the real surface.** The exposure review probed hostnames
   through the public edge itself, because what a config file says and what
   the internet sees are different documents.
5. **Agents get least privilege by construction.** One credential,
   server-side injection of the real ones, per-route permissions — and the
   willingness to refuse a route entirely when the upstream app can't be
   exposed safely.
6. **Write it down or it didn't happen.** Threat model, deployment pattern,
   incident post-mortems, per-host inventory — 23 documents, versioned, and
   updated as part of the work rather than after it.

---

*Stuart Rutty — delivery & technical program management, Brooklyn NY.
[LinkedIn](https://www.linkedin.com/in/stuartrutty/)*
