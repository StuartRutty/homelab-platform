# Architecture

## Hosts

| Role | Hardware | OS | Job |
|---|---|---|---|
| Ops host | Low-power mini PC (Intel N150), 16 GB RAM | Windows 11 | The self-hosted services stack — dozens of containers |
| Media host | Ryzen 7 (8C/16T), 32 GB RAM, RTX 4060 Ti | Windows 11 | Media services and storage — a multi-terabyte media drive |
| Desktop | Intel i5, 16 GB RAM | Ubuntu | Daily driver; administers the servers over SSH and remote desktop |
| Living-room PC | Dell OptiPlex (i7-8700), 8 GB RAM | Ubuntu | Standalone playback box; spare capacity |

Admin access is SSH, key-only, per-account — no shared admin user. A remote
desktop tool is the fallback GUI path.

## The constraint that shaped everything

The two server hosts run Docker Desktop on Windows, which has **no
cross-host overlay networking**. The design response: essentially all
services live behind **one reverse proxy on the ops host**, including the
media host's services (the proxy forwards to them over the LAN). The
alternative — publishing dozens of individual LAN ports across hosts — was
rejected: one ingress is auditable, fifty port forwards are not.

```
Internet → CDN/tunnel edge (TLS terminates here)
                    ↓  outbound-only tunnel, zero inbound ports
              Reverse proxy (single instance, ops host)
                    ↓  routes by hostname
              Service containers (private container network)
```

## Edge and TLS

- **No ports are forwarded on the router.** Public reachability comes from
  an outbound-only tunnel terminating at the proxy.
- Every service hostname is **dual-scheme in a single proxy block**: plain
  HTTP (kept because the tunnel origin targets it — a blanket redirect to
  HTTPS would break every published hostname at once) and HTTPS with real
  Let's Encrypt certificates.
- Certificates are issued via **ACME DNS-01** using a custom proxy image with
  a DNS plugin, authorized by an API token scoped to DNS edits on this one
  zone. Issuance was verified 27/27 when the setup launched; renewal uses
  the identical path.
- The result: **the same URL works on and off the LAN**, with a real
  certificate in both directions. LAN traffic stays in the apartment,
  encrypted — which also makes WebAuthn/FIDO2 logins work locally, since the
  LAN URL is a genuine secure context.

## DNS

- A Pi-hole + Unbound pair provides split-horizon DNS: the service domain
  resolves to the proxy's LAN address inside the network and to the tunnel
  edge outside it. Answers for the zone stay local-only — no upstream
  leakage of internal names.
- The recursive resolver has a **static address inside the Docker network**,
  and the filtering resolver is pinned next to it; an early incident (the
  filter claiming the resolver's address on recreation, leaving it unable to
  start) is why the pins exist.
- DNS is configured **per device, deliberately**. The network is shared
  with people who aren't part of this lab; forcing filtering network-wide
  was considered and rejected.
  Every configured device points at exactly one resolver — see the
  deployment pattern doc for the RFC 8020 failure that makes "no secondary
  resolver" a load-bearing rule rather than a preference.

## Private overlay network

A Tailscale tailnet (five devices) overlays the LAN for administration and
agent access:

- ACL policy is versioned in git like everything else.
- An advertised subnet route puts the LAN behind the tailnet, so remote
  admin reaches the same services at the same names and LAN addresses — no
  separate VPN-only naming scheme.
- One agent device is restricted by ACL to a small set of ports on the LAN
  and a single overlay address — a timeout anywhere else is the policy
  working, not an outage. (That boundary is described, not mapped, for
  obvious reasons.)

## Data layer

Shared state lives in dedicated containers on the ops host — PostgreSQL,
MySQL, and Redis — rather than per-service databases scattered across the
lab. Services with their own embedded stores use named volumes (bind mounts
corrupt too easily under Docker Desktop on Windows). The backup design that
covers all of it is in [backup-and-dr.md](backup-and-dr.md).

## Layering on the ops host

| Layer | What runs there |
|---|---|
| Edge | Tunnel client |
| Proxy | The single reverse proxy (custom image: DNS plugin + intrusion-prevention bouncer) |
| Security | CrowdSec (parses proxy access logs; see the security review for the client-IP problem it had to solve) |
| DNS | Pi-hole + Unbound |
| State | PostgreSQL · MySQL · Redis |
| Identity | A self-hosted password manager |
| Monitoring | Container admin UI, uptime monitoring, update notifications |
| Backup | Nightly restic (see backup doc) |
| Services | Everything else — media, home operations, personal data, AI front-ends — each in its own folder per the deployment pattern |
