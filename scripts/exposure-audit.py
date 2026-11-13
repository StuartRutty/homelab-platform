"""Weekly exposure audit (no_agent cron).

Two passes, both THROUGH THE PUBLIC EDGE: resolve via a public resolver, connect
with --resolve to the edge IP, read headers. On a LAN with split-horizon DNS
*.example.local resolves to the local proxy, so a plain curl bypasses the CDN
completely and every host looks unprotected (measured 2026-10-24: no CDN header,
no Www-Authenticate).

1. Root path (who is protected at the edge at all):
     access        -> Www-Authenticate: Cloudflare-Access / CF_AppSession present
     public_open   -> edge serves the origin with only app-level auth
     unreachable   -> no public DNS (LAN-only by design, or stopped)

2. API path (per host in API_PROBES; catches an /api bypass nobody meant to add):
     access        -> Access still intercepts this path
     bypass        -> the edge reached the origin WITHOUT Access (intended only for
                      services whose consumer can hold a single credential)
     unreachable   -> no public DNS

Comparisons are against exposure-expectations.json / exposure-expectations-api.json
(seeded baselines, hand-editable; per-host values as above plus "ignore"). Prints
NOTHING when the world still matches expectations; prints a compact diff when a host
or API path is NEW, CHANGED, or drifted, and when an expected host disappears.
--init seeds the root expectations, --init-api seeds the API expectations.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPECT = HERE / "exposure-expectations.json"
STATE = HERE / "exposure-audit-state.json"
EXPECT_API = HERE / "exposure-expectations-api.json"
STATE_API = HERE / "exposure-audit-api-state.json"
CADDY = Path(r"/etc/caddy/Caddyfile")

# Paths that answer on the origin whether or not the caller is authenticated
# (401/403/400 all prove the request reached the app). Only hosts listed here
# get an API probe.
API_PROBES = {
    "media.example.local": "/api/v1/status",
    "notes.example.local": "/api/v1/auth/status",
    "photos.example.local": "/api/v1/status",
    "search.example.local": "/search?q=audit&format=json",
    "inventory.example.local": "/api/v1/status",
    "household.example.local": "/api/system/info",
    "dashboard.example.local": "/api/services",
    "vault.example.local": "/api/config",
    "uptime.example.local": "/metrics",
    "downloads.example.local": "/api/v2/app/version",
}

def vhosts():
    text = CADDY.read_text(encoding="utf-8", errors="ignore")
    hosts = set()
    for m in re.finditer(r"(?:https?://)?([a-z0-9][a-z0-9.-]*\.example\.local)", text, re.I):
        hosts.add(m.group(1).lower())
    return sorted(hosts)

def public_ip(host):
    """Resolve through a public resolver: split-horizon DNS on the LAN resolves
    *.example.local to the local proxy, so the system resolver never reaches the
    edge."""
    try:
        res = subprocess.run(["nslookup", "-query=A", host, "1.1.1.1"],
                             capture_output=True, text=True, timeout=20)
        ips = [ip for ip in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", res.stdout or "")
               if not ip.startswith("1.1.1.1")]
        return ips[-1] if ips else None
    except (subprocess.TimeoutExpired, OSError):
        return None

def _edge_headers(host, ip, path, accept):
    try:
        res = subprocess.run(
            ["curl", "-sk", "--resolve", "%s:443:%s" % (host, ip),
             "-D", "-", "-o", "/dev/null", "--max-time", "12",
             "-H", "Accept: " + accept,
             "-H", "User-Agent: Mozilla/5.0 (compatible; homelab-exposure-audit)",
             "https://%s%s" % (host, path)],
            capture_output=True, text=True, timeout=30)
        return (res.stdout or "").lower()
    except (subprocess.TimeoutExpired, OSError):
        return None

def classify(host):
    ip = public_ip(host)
    if not ip:
        return "unreachable"
    headers = _edge_headers(host, ip, "/", "text/html")
    if headers is None:
        return "unreachable"
    if "cloudflare-access" in headers or "cf_appsession" in headers:
        return "access"
    if "cf-ray" in headers or "server: cloudflare" in headers:
        return "public_open"
    return "unreachable"  # answered but not by the edge

def classify_api(host, path):
    ip = public_ip(host)
    if not ip:
        return "unreachable"
    headers = _edge_headers(host, ip, path, "application/json")
    if headers is None:
        return "unreachable"
    if ("cloudflare-access" in headers or "cf_appsession" in headers
            or "cloudflareaccess.com" in headers):
        return "access"
    if "cf-ray" in headers or "server: cloudflare" in headers:
        return "bypass"
    return "unreachable"  # answered but not by the edge

def diff(current, expect_path, state_path, kind):
    expected = json.loads(expect_path.read_text(encoding="utf-8")) if expect_path.exists() else {}
    prev = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    lines = []
    for host, now in sorted(current.items()):
        want = expected.get(host)
        if want is None:
            lines.append("- NEW %s `%s` -> %s (not in expectations)" % (kind, host, now))
        elif want != "ignore" and want != now:
            lines.append("- CHANGED %s `%s`: expected %s, now %s" % (kind, host, want, now))
        elif prev.get(host) and prev[host] != now:
            lines.append("- DRIFT %s `%s`: was %s, now %s" % (kind, host, prev[host], now))
    for host in expected:
        if host not in current and expected.get(host) != "ignore":
            lines.append("- GONE %s `%s` was %s, no longer probed" % (kind, host, expected[host]))
    state_path.write_text(json.dumps(current, indent=1, sort_keys=True), encoding="utf-8")
    return lines

def main():
    argv = sys.argv
    hosts = vhosts()
    if "--init" in argv:
        current = {h: classify(h) for h in hosts}
        EXPECT.write_text(json.dumps(current, indent=1, sort_keys=True), encoding="utf-8")
        print("exposure-expectations.json seeded with %d hosts:" % len(current))
        for h, c in sorted(current.items()):
            print("  %-38s %s" % (h, c))
        return
    if "--init-api" in argv:
        current = {}
        for host, path in sorted(API_PROBES.items()):
            if host in hosts:
                current[host] = classify_api(host, path)
        EXPECT_API.write_text(json.dumps(current, indent=1, sort_keys=True), encoding="utf-8")
        print("exposure-expectations-api.json seeded with %d hosts:" % len(current))
        for h, c in sorted(current.items()):
            print("  %-38s %-11s %s" % (h, c, API_PROBES[h]))
        return

    current = {h: classify(h) for h in hosts}
    current_api = {h: classify_api(h, p) for h, p in sorted(API_PROBES.items()) if h in hosts}

    lines = diff(current, EXPECT, STATE, "vhost")
    lines += diff(current_api, EXPECT_API, STATE_API, "api")
    if not lines:
        return
    print("Exposure audit (edge headers, public-resolver lookup):")
    print("\n".join(lines))

if __name__ == "__main__":
    main()
