# The agent gateway — one credential, least privilege, and two routes that had to be refused

When an external AI assistant needed real access to this lab's services, the
integration problem was credential-shaped: the assistant's platform can
present **exactly one stored credential**, but every app in the lab has its
own token — and the stopgap (edge rules bypassing the identity layer for two
app APIs, open to the whole internet) was exactly the kind of temporary that
becomes permanent. The security review flagged it; this gateway is what
replaced it.

## Design

A **credential-multiplexing ingress on the existing reverse proxy**:

- The client holds one bearer credential. The proxy checks it, then
  **injects the correct per-service credential server-side** before proxying
  to the app. App credentials live only in the proxy's secrets file; the
  client never sees, stores, or transmits them.
- **Permissions are per-route and match what each service actually
  supports** — full CRUD where the workflow needs it, read-only where the
  upstream has no write surface worth exposing. There is no blanket rule.
- New capabilities are added as routes on the same listener behind the same
  credential — never as new ports, hosts, or credentials for the client.

**Rejected alternative:** a serverless cloud worker as the gateway. It
works, but every secret update would require a cloud credential minted for
the purpose, and the app tokens would live in someone else's cloud — the
opposite of the posture this was built to fix.

## The code-bearing piece

Most of the gateway is proxy configuration. One small service behind it does
the jobs config can't:

- **An async job queue** — the client submits bounded work items; a local
  worker drains the queue on an interval. The spool is **data only: nothing
  is ever executed from it**, and instructions embedded in an item are
  treated as text to process, not commands to follow. Submissions are
  idempotent (a retried submit returns the same job; the same key with a
  different payload is a conflict), and results are retained for 7 days.
- **A lab-status aggregator** — one JSON snapshot of every host, all running
  containers, and native health/version for the proxied services, cached
  for 60 seconds so clients can't poll the lab into the ground.
- **A read-only documentation view** — manifest plus file contents for the
  lab's text docs. No write route exists.
- **An async task bridge to the local agent** — longer tasks hand off to the
  homelab's own agent with explicit status semantics. The rule that matters:
  **approvals never resolve through the external client.** A task that needs
  the owner's confirmation reports that it is waiting, is stopped after a
  few minutes, and fails as *needs-approval* — the human acts interactively,
  in person, or not at all.

## Two routes that had to be narrowed — and one refused

Granting the routes was the easy part. The interesting work was the two
media routes:

- **A request route, allowlisted to five paths.** A media-request service
  got a route restricted to search, title lookup, request creation, and
  reading back a request — everything else simply doesn't exist through the
  gateway. Requests are attributed to a **dedicated low-privilege user**
  whose identity the gateway injects; that account's password was generated
  randomly and recorded nowhere, so header injection is the only way in.
  Escalation was tested, not assumed: attempts to act as another user, to
  bypass quota limits, and to reach settings or user administration are all
  refused, verified live.
- **A maintenance API refused entirely.** A library-maintenance app's
  native API includes collection actions whose default mode is *delete* —
  and a nightly worker executes armed actions autonomously. The proxy
  cannot filter request bodies, so there was no safe way to expose that API
  at all. Instead, the gateway exposes only the underlying **media server's
  collections API**: collections can be created, edited, and populated, and
  media files are structurally unreachable. The refusal was the design.

## Cutover discipline

- The client's five legacy per-service connectors were retired the day the
  gateway landed; all 12 routes were verified live before the switch.
- Keys the client had previously held were **revoked or rotated** — one
  app's key had to be rotated because the client held a live copy, and since
  that app has a single app-wide key, the rotation touched all three of its
  consumers in the same window.
- The two internet-facing bypass rules from the stopgap era were deleted,
  and anonymous access to those APIs was verified closed **from the public
  edge** — the same vantage point the exposure audit uses.
