# health-check.sh — EXCERPT (framework sections only)
# Cron: every 6 hours. Emits one `CHECK <probe> OK|WARN|CRIT` line per probe
# plus a final `SUMMARY` line; always exits 0, because the cron delivery is
# the stdout, not an engine error. Sections 2-6 and 8-9 of the full script
# (restart counts, memory pressure, backup freshness, disk, TLS expiry,
# LAN-only endpoints, host metrics) are elided; the probe numbering below
# is the original file's. The full script is deliberately unpublished —
# its probe set is effectively the service inventory (see scripts/README.md).
#
#!/usr/bin/env bash
# health-check.sh — homelab health probe for the primary host's Docker stacks.
# Output: one CHECK line per probe (OK/WARN/CRIT) + SUMMARY line. Always exit 0.
# Read by the agent's cron job "Homelab health check" — do not print secrets.

STATE_DIR="/path/to/scripts"

# Containers stopped ON PURPOSE (the operator's call). Never CRIT on these and
# never "remediate" them by starting them — a stopped container here is the
# desired state. Their vhosts are also skipped in the services probe. Keep this
# list in sync with the per-host service inventory.
EXPECTED_DOWN="feed-curator"   # stopped deliberately 2026-10-26

echo "=== Homelab health check — $(date '+%Y-%m-%d %H:%M %Z') ==="

# --- Preflight: wait out a host/Docker boot window --------------------
# A Windows reboot leaves Docker Desktop's engine AND published ports down for
# several minutes. Ticks landing in that window used to emit false CRITs (0
# containers up, every vhost refused) and page the operator for a healthy reboot.
# Wait up to ~6 minutes for the engine, the proxy's port 80 and the DNS
# resolver — the shared dependencies every probe below relies on. If Docker
# never answers, say so explicitly: that is a genuine failure, not a probe bug.
PREFLIGHT_DEADLINE=$((SECONDS + 360))
PREFLIGHT_START=$SECONDS
while [ $SECONDS -lt $PREFLIGHT_DEADLINE ]; do
  docker info >/dev/null 2>&1 && break
  sleep 15
done
if ! docker info >/dev/null 2>&1; then
  echo "CHECK preflight CRIT — Docker daemon not responding after $((SECONDS - PREFLIGHT_START))s of retries (host reboot or Docker Desktop failure); remaining probes skipped"
  echo "SUMMARY done — preflight failed; restore Docker Desktop, then rerun"
  exit 0
fi
# Engine up; now wait for the proxy's published port and the DNS resolver.
while [ $SECONDS -lt $PREFLIGHT_DEADLINE ]; do
  CURLY=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1/ 2>/dev/null)
  DNS_OK=$(docker exec dns dig +short +time=1 +tries=1 @127.0.0.1 example.com 2>/dev/null | head -1)
  [ -n "$CURLY" ] && [ -n "$DNS_OK" ] && break
  sleep 10
done
PREFLIGHT_WAIT=$((SECONDS - PREFLIGHT_START))
if [ "$PREFLIGHT_WAIT" -ge 15 ]; then
  echo "CHECK preflight OK — Docker, proxy and DNS ready after ${PREFLIGHT_WAIT}s (host boot window absorbed)"
fi

# --- 1. Container status: anything not Up, or unhealthy -------------
BAD=$(docker ps -a --format '{{.Names}}|{{.Status}}' | awk -F'|' -v down="$EXPECTED_DOWN" '
  BEGIN { n = split(down, d, " "); for (i=1;i<=n;i++) skip[d[i]] = 1 }
  $2 !~ /^Up / && !($1 in skip) {print $1" ("$2")"}
  $2 ~ /^Up / && $2 ~ /\(unhealthy\)/ {print $1" ("$2")"}')
if [ -z "$BAD" ]; then
  N=$(docker ps -q | wc -l)
  echo "CHECK containers OK — all $N containers Up, none unhealthy"
else
  echo "CHECK containers CRIT — not healthy:"
  echo "$BAD" | sed 's/^/  - /'
fi

# --- 7. Service endpoints via the proxy (auto-derived from its config) ----
# Every vhost service on ANY host proxied through the primary host's proxy is
# probed here. New vhost services are covered automatically — no edit needed at
# deploy time.
# Verdicts: 000/timeout or 502/503/504 = CRIT (dead/unreachable backend),
# other 5xx = WARN (app answering but erroring), 1xx-4xx = OK (401/30x are
# normal auth walls / redirects).
CADDYFILE="/path/to/proxy/Caddyfile"
VHOSTS=$(grep -oE 'https?://[A-Za-z0-9.*-]+\.example\.local' "$CADDYFILE" 2>/dev/null \
  | sed 's|.*://||' | grep -v '\*' | sort -u)
SVC_TOTAL=0; SVC_BAD=0
for h in $VHOSTS; do
  # Skip vhosts whose backend container is intentionally stopped: their 502 is
  # the expected consequence, not an incident. Match on the first label.
  SVC_NAME=${h%%.*}
  case " $EXPECTED_DOWN " in *" $SVC_NAME "*) continue;; esac
  SVC_TOTAL=$((SVC_TOTAL+1))
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 -H "Host: $h" http://127.0.0.1/)
  [ -z "$CODE" ] && CODE=000
  case "$CODE" in
    000)
      SVC_BAD=$((SVC_BAD+1)); echo "CHECK svc-$h CRIT — no response via proxy (timeout/refused)" ;;
    502|503|504)
      SVC_BAD=$((SVC_BAD+1)); echo "CHECK svc-$h CRIT — HTTP $CODE via proxy (backend down?)" ;;
    5??)
      SVC_BAD=$((SVC_BAD+1)); echo "CHECK svc-$h WARN — HTTP $CODE (app answering but erroring)" ;;
  esac
done
if [ "$SVC_TOTAL" -eq 0 ]; then
  echo "CHECK services WARN — no vhosts parsed from $CADDYFILE (file moved?)"
elif [ "$SVC_BAD" -eq 0 ]; then
  echo "CHECK services OK — $SVC_TOTAL/$SVC_TOTAL vhosts responding via proxy"
else
  echo "CHECK services CRIT — $((SVC_TOTAL-SVC_BAD))/$SVC_TOTAL vhosts OK, failures above"
fi
