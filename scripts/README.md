# scripts/ — the automation behind the claims

The docs in this repo describe an assurance routine: backups that prove
themselves, weekly vulnerability and exposure checks, scheduler hygiene.
These are the actual scripts that run it — scrubbed for publication
(real domain → `example.local`, addresses and household paths →
placeholders; logic, comments, and structure untouched).

The comments are kept verbatim on purpose: they carry the fix history.
The boot-window preflight exists because a reboot used to page. The
`--ignore-unfixed` flag exists because unfixable CVEs buried the signal.
The explicit system-node path exists because under cron, `node` on PATH
is the agent runtime's bundled binary, not the host's. That history is
the most legible evidence in the repo.

| File | Schedule | What it proves |
|---|---|---|
| [restic-verify.py](restic-verify.py) | Monthly, 1st, 05:00 | The restore drill from [backup-and-dr.md](../docs/backup-and-dr.md): `restic check` over a real slice of pack data, then an actual restore of a live database (opened, integrity-checked, row-counted) and of the proxy config — into a scratch dir, cleaned up after. Silent on success; a backup that has never been restored is a hypothesis. |
| [trivy-vuln-scan.py](trivy-vuln-scan.py) | Weekly, Mon 08:15 | Scans every running container image on both hosts with Trivy for fixable CRITICAL/HIGH CVEs. An update bot tells you an image *changed*; this tells you your *current* image is exploitable. Silent when clean. |
| [security-sweep.py](security-sweep.py) | Weekly, Wed 09:15 | The collector half of the fleet patch/CVE sweep: per-image scan caching (a weekly run only rescans what changed), new-finding detection against a seen-set, and Node runtime version tracking against upstream release lines, including EOL detection. A decision layer reads its report; the collector itself is deterministic, no LLM. |
| [exposure-audit.py](exposure-audit.py) | Weekly, Mon 08:30 | The exposure audit from [security-review.md](../docs/security-review.md), automated: every vhost from the proxy config is probed *through the public edge* (public-resolver lookup, `--resolve` to the edge IP — LAN split-horizon DNS would otherwise bypass the CDN entirely), classified access / public-open / unreachable, and diffed against seeded expectations, including a per-host API-path pass that catches an unintended Access bypass. Silent when the world matches expectations. |
| [cron-hygiene-prompt.md](cron-hygiene-prompt.md) | Monthly, 1st, 10:00 | Scheduler hygiene is not a script — it's a read-only agent instruction: audit the job store for dead weight (long-paused jobs, missing scripts, superseded watchers, orphaned state files) and report removals for a human to approve. Included because the discipline — an automation that audits the automations, and is forbidden from changing anything itself — is the point. |

## Deliberately not here

The health-check probe script (the platform's main monitoring artifact)
is omitted, and the omission is documented rather than silent: the script
*is* the inventory. Its probe set enumerates every container, every
deliberately-stopped service, and every LAN-only endpoint — exactly the
service map this repo withholds by design. A faithful scrub would replace
a third of it with placeholders and still disclose the map. What the docs
claim about health checking stands on the pattern (preflight → probes
with an expected-down exemption list → silent unless something real)
rather than on that file.
