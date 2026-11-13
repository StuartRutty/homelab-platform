# Incident post-mortem: VPN egress outage, 2026-10-09

**Impact:** Two download clients that share a VPN container's network
namespace were offline ~11:52–12:45 EDT. Nothing else was affected — only
those clients are VPN-routed.

## Root-cause chain

1. **The VPN provider's entire regional server pool went down.** Every
   endpoint in the pinned metro region refused connections — verified
   independently from two hosts, so it wasn't a local fault. Other regions
   were healthy.
2. **The VPN client was pinned to that region**, so the tunnel was dead and
   its health check failed.
3. **A self-healing sidecar made it worse.** The sidecar watches the VPN
   container and its dependents and restarts unhealthy containers. With the
   tunnel unable to connect, all three entered a kill/restart flap on ~30 s
   cycles — the VPN container could never stay up long enough to connect
   even if an endpoint had answered, and the dependents bounced with it.
4. **After the region was changed, an outdated client image kept failing
   slowly**: it selected dead endpoints from the new region one per retry
   (a soft-restart loop, ~30 s each), and its port-forwarding service probed
   **hardcoded API addresses from a previous tunnel subnet** that no longer
   existed.

## What fixed it

- Moved the region pin to a neighboring healthy pool.
- Updated the VPN client image. (Operational gotcha worth keeping: the
  image pull **must run from an interactive session** — from a
  non-interactive SSH session, the container runtime's credential helper
  fails with a logon-session error, and the usual config workarounds do not
  bypass it.)
- The updated image derives the port-forward API address from the *actual*
  tunnel route instead of stale constants — port forwarding succeeded
  immediately on the new build.
- After recreating the VPN container, its dependents had to be started
  explicitly; left alone they can land in an error state rather than
  auto-starting.
- A temporary hardcoded endpoint pin was used during diagnosis and
  **removed** once the updated client's own selection worked. The fallback
  procedure is documented if it ever sticks again: read the provider's
  server list from inside the container, probe candidates, pin one that
  verifiably answers.

## Secondary incident, caused by the fix itself

Editing the compose file with a legacy shell's text cmdlet corrupted it:
the file contained BOM-less non-ASCII comment text, the shell read it as a
legacy codepage and re-encoded it — injecting a C1 control character in the
process. The parser then rejected the file with `control characters are not
allowed`, an error that points nowhere near the cause, and byte-level
checks for control bytes **missed it entirely** because the corruption lives
at the decoded-codepoint level.

Fixed by rewriting the affected lines as pure ASCII with a byte-precise
editor. **Rule adopted: YAML on these hosts is edited with byte-precise
tooling, never with shell text round-trips.**

## Detection gap

Monitoring covered all three services — and produced no alert. The health
check runs every six hours, and the outage happened to fall inside the
healthy window on both sides of it. The follow-up is alerting on the
self-healer's **restart-count spikes**: the flap was visible in restart
counts from the first minute, an hour before a human noticed the outage.
