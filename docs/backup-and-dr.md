# Backup & disaster recovery

The design goal, stated once and then enforced: **a backup is a claim, and
claims get verified.** Snapshot counts are the least interesting number
here; the restore drill is the point.

## Design

- **One encrypted repository** (restic) covers both server hosts. The
  repository only opens with its password; the password is escrowed in the
  platform's own password manager — if it weren't, one OS reinstall would
  turn every snapshot into a brick.
- The media host pushes its snapshots over the LAN to an **append-only**
  restic server on the ops host: basic-authenticated, and append-only means
  a compromised or malfunctioning client can add snapshots but cannot delete
  or rewrite existing ones.
- **Retention** — 7 daily, 4 weekly, 6 monthly — is applied by the ops
  host's nightly job only. The media host's job runs later in the night so
  the two schedules never collide on the shared repo.
- **Cadence:** both hosts snapshot nightly, staggered so the two jobs never
  collide on the shared repo; the retention policy bounds recovery history
  to roughly six months at any given time.

## Coverage: audited, not assumed

A coverage audit compared every named volume used by a running container
against the backup list. Everything live was covered; the uncovered set was
entirely stale — leftover volumes from retired services and an earlier
hosting arrangement.

The cleanup set the house standard for destructive work:

1. **Quarantine first** — all 97 stale volumes exported to tarballs (5.1 GB)
   and spot-checked for integrity.
2. **Then delete** — 96 volumes removed, one held back by the final in-use
   re-check (a leftover container still referenced it; its tarball exists).
3. Nothing was deleted that existed only in one place.

New services join the backup list as an explicit step of the deployment
pattern — the script doesn't auto-discover volumes, deliberately, so junk
never gets backed up by accident.

## Verification: three layers

1. **Repository integrity** — periodic `restic check` across the whole repo
   (309 snapshots verified clean at the first full check; the repo has grown
   since).
2. **Manual restore drill (passed, Sep 2026)** — a live service volume was
   restored from its latest snapshot to scratch space: 134 files including
   the database and key material, verified present and intact, then the
   scratch copy was deleted. Backups became a fact, not a hypothesis.
3. **Automated monthly drill (since Nov 2026)** — the drill now runs itself
   on the first of the month, silent on success:
   - `restic check --read-data-subset=5%` (reads real pack data, not just
     metadata)
   - a real restore of a live database, verified with the database's own
     integrity check plus a content sanity count
   - a real restore of the proxy configuration — the file the whole ingress
     depends on
   - scratch space deleted afterward

   The gap this closed: the nightly job backed up, forgot, and pruned — but
   never ran `check` at all. A corrupt pack would have been invisible until
   the day a restore was needed, which is the worst day to learn about it.

## Deliberate exclusions

The multi-terabyte media library is **not** in the repository. It's large,
regenerable, and would crowd out the data that isn't. The line is drawn
explicitly — media metadata and service configuration *are* backed up; the
media files themselves are not — rather than by accident or by storage
pressure.
