# Deployment pattern — how every new service ships

Every service on the platform follows the same nine steps. The pattern is a
template with the failure stories left in, because the stories are why the
steps exist. (Paths and the domain are placeholders; the production versions
live in the private docs.)

## 1. Folder + shared environment

One folder per service under a category tree. A symlinked shared `.env`
provides common variables (timezone, domain, shared database settings);
service-specific secrets go in a local `.env.secrets` that is never
symlinked and never shared.

## 2. Compose file

```yaml
services:
  <service>:
    image: <image>
    container_name: <service>
    restart: unless-stopped
    env_file:
      - .env
      - .env.secrets      # only if the service has secrets
    volumes:
      - <service>_data:/data
    networks:
      - homelab           # the shared private network, external: true
```

Rules: **no `ports:` block** (the proxy reaches the container by name; a
service that publishes its own port has left the pattern), always join the
shared network, **named volumes** for database-backed services (bind mounts
corrupt under Docker Desktop on Windows).

## 3. Proxy block

One dual-scheme block per service — HTTP and HTTPS in the *same* block,
never a bare hostname (the proxy silently defaults to HTTPS-only) and never
HTTPS-only (the HTTP side is the tunnel origin and the LAN fallback):

```
http://<service>.home.example.com, https://<service>.home.example.com {
	reverse_proxy <container-name>:<port>
	log { output file /var/log/access.log; format json }
}
```

Reload is zero-downtime; no restart. Do **not** blanket-redirect HTTP to
HTTPS — the tunnel ingress targets the HTTP origin, so a redirect takes
down every published hostname at once.

## 4. DNS: add a host-record — required, not optional

Add one explicit record per service to the DNS server's overrides, pointing
the name at the proxy's LAN address. This step exists because of a failure
that bit **twice**:

The zone also has a wildcard address record, which answers A queries fine —
but returns **NXDOMAIN for every other record type** (AAAA, HTTPS, NS, TXT).
Per RFC 8020, a resolver that receives NXDOMAIN for *any* type caches the
whole **name** as nonexistent. Desktops mostly ask for A and look healthy;
mobile clients ask for AAAA/HTTPS, get NXDOMAIN, and report "address not
found" for a service that is up. The explicit host-record returns
NOERROR/NODATA for those types — empty but valid — which is the correct
semantic and doesn't poison the name.

The tell, when it happened: *"works right after toggling Wi-Fi, then fails
again after some browsing"* — clearing the client cache works until the
NXDOMAIN is re-cached.

Verification reads the response **status**, not the answer (a status-printing
query shows nothing for both the correct NODATA and the broken NXDOMAIN, so
answer-counting cannot tell them apart):

```
for each type in A AAAA NS TXT HTTPS:
    status must be NOERROR; the A answer must be the proxy's LAN address
```

And the sync gate that would have caught it both times — list proxy vhosts
with no matching DNS record; the output must be empty:

```
comm -23 <(vhosts from the proxy config) <(names from the DNS overrides)
```

Retirements count too: a stale record for a dead name is cosmetic, but a
missing record for a live name breaks mobile clients, so the gate runs on
removals as well as adds.

## 5. Tunnel hostname (only if it should be public)

Add a public hostname to the tunnel config pointing at the proxy's HTTP
origin — every public service targets the same origin; the proxy routes by
Host header. Some services additionally sit behind an identity gate at the
edge. **LAN-only services get no tunnel entry and stay unreachable from
outside.** Public reachability is a decision, never a default.

## 6. Bring it up

`docker compose up -d`. Editing env vars on a running service recreates it
automatically; no force flags needed.

## 7. Register the backup — mandatory

Add the service's volume names to the backup script's explicit list, then
run a backup manually to capture the new volume immediately. The script
**does not auto-discover volumes, by design** — auto-discovery eventually
backs up junk. The trade is deliberate: forgetting this step means the new
service is unprotected, so the step is in the template, in bold, in the
private runbook.

## 8. Wire into health monitoring

Standing rule: **every deployed service is covered by the health check, and
stays covered.** Services with a proxy vhost need no work — the health check
re-derives its probe list from the proxy config on every run and probes each
vhost through the proxy (which also covers the second host's proxied
services). Vhost-less services get an explicit probe line. A deploy isn't
done until one manual health run shows the new service green.

## 9. Document it

Update the per-host service inventory (the authoritative list: port, env
vars, special configuration) and, if topology changed, the architecture
doc. Documentation is step 9 of the deploy, not a later chore — the
inventory stays complete because of this rule.
