#!/usr/bin/env python3
"""Fleet security sweep - collector for the weekly CVE/patch cron.

Deterministic, no LLM. Emits a compact markdown report to stdout; the cron
agent reads it and decides what to patch. Results are cached per image ID so
a weekly run only scans images that actually changed.

State/cache: /path/to/scripts/.security-sweep/
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(SCRIPTS, ".security-sweep")
CACHE_FILE = os.path.join(STATE_DIR, "image-scan-cache.json")
SEEN_FILE = os.path.join(STATE_DIR, "seen-cves.json")
POLICY_FILE = os.path.join(SCRIPTS, "security-sweep-policy.json")
TRIVY_IMAGE = "aquasec/trivy:latest"
TRIVY_CACHE_VOL = "trivy_cache"
MAX_SCAN_PER_RUN = int(os.environ.get("SWEEP_MAX_SCAN", "12"))  # cap runtime on a low-power host
SCAN_MAX_AGE_DAYS = 21         # rescan unchanged images at least this often

ET = timezone(timedelta(hours=-4))  # EDT; the fleet runs America/New_York

def run(cmd, timeout=120, stdin_data=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           input=stdin_data, shell=False)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except FileNotFoundError as e:
        return 127, "", str(e)

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)

def docker_ps(host):
    """Return [(name, image, container_id)] for running containers on a host."""
    if host == "primary":
        rc, out, err = run(["docker", "ps", "--format", "{{.Names}}|{{.Image}}|{{.ID}}"], timeout=60)
    else:
        rc, out, err = run(["ssh", "-o", "LogLevel=ERROR", "media-host",
                            "docker ps --format '{{.Names}}|{{.Image}}|{{.ID}}'"], timeout=90)
    rows = []
    if rc != 0:
        return rows, err.strip()[:200]
    for line in out.splitlines():
        line = line.strip()
        if line.count("|") == 2:
            n, i, c = line.split("|")
            rows.append((n.strip(), i.strip(), c.strip()))
    return rows, ""

def image_id(image):
    rc, out, _ = run(["docker", "image", "inspect", image, "--format", "{{.Id}}"], timeout=30)
    return out.strip() if rc == 0 and out.strip() else ""

def trivy_scan(image):
    """HIGH/CRITICAL, fixable only. Returns (findings, error)."""
    cmd = ["docker", "run", "--rm",
           "-v", "/var/run/docker.sock:/var/run/docker.sock",
           "-v", f"{TRIVY_CACHE_VOL}:/root/.cache/trivy",
           TRIVY_IMAGE, "image",
           "--severity", "HIGH,CRITICAL", "--scanners", "vuln",
           "--ignore-unfixed", "--quiet", "--format", "json", image]
    rc, out, err = run(cmd, timeout=420)
    if rc != 0:
        return None, (err or out).strip()[-300:]
    try:
        data = json.loads(out)
    except Exception:
        return None, "unparseable trivy output"
    findings = []
    for res in data.get("Results") or []:
        for v in res.get("Vulnerabilities") or []:
            findings.append({
                "id": v.get("VulnerabilityID"),
                "pkg": v.get("PkgName"),
                "installed": v.get("InstalledVersion"),
                "fixed": v.get("FixedVersion"),
                "sev": v.get("Severity"),
                "title": (v.get("Title") or "")[:120],
            })
    return findings, ""

def node_version(host, container=None):
    if host == "primary":
        cmd = (["docker", "exec", container, "node", "--version"] if container
               else ["node", "--version"])
    else:
        inner = (f"docker exec {container} node --version" if container
                 else "node --version")
        cmd = ["ssh", "-o", "LogLevel=ERROR", "media-host", inner]
    rc, out, err = run(cmd, timeout=45)
    out = (out or "").strip().replace("\r", "")
    m = re.search(r"v(\d+\.\d+\.\d+)", out)
    return m.group(1) if (rc == 0 and m) else ""

def pkg_version(host, container, binary):
    if host == "primary":
        cmd = ["docker", "exec", container, binary, "--version"]
    else:
        cmd = ["ssh", "media-host", f"docker exec {container} {binary} --version"]
    rc, out, _ = run(cmd, timeout=45)
    return (out or "").strip().splitlines()[0][:60] if rc == 0 else ""

def latest_versions():
    """Newest patch release per tracked line, from the runtimes' own release channels."""
    out = {}

    # Node.js: the release index carries every published version.
    rc, body, _ = run(["curl", "-sSL", "--max-time", "30",
                       "https://nodejs.org/dist/index.json"], timeout=60)
    if rc == 0 and body.strip().startswith("["):
        try:
            rel = json.loads(body)
            for major in (18, 20, 22, 24, 26):
                vers = [r["version"].lstrip("v") for r in rel if r["version"].startswith(f"v{major}.")]
                if vers:
                    def key(v):
                        return tuple(int(x) for x in v.split(".")[:3])
                    out[major] = max(vers, key=key)
        except Exception:
            pass
    return out

def main():
    started = time.time()
    now = datetime.now(ET)
    policy = load_json(POLICY_FILE, {})
    auto = set(policy.get("auto", []))
    cache = load_json(CACHE_FILE, {})
    seen = load_json(SEEN_FILE, {})
    first_run = not seen

    container_rows, primary_err = docker_ps("primary")
    sp_rows, media_err = docker_ps("media")

    # ---- image scan (primary only: it holds the internet-exposed services) ----
    scanned, cached, scan_errors, new_findings = [], [], [], []
    per_image = {}
    budget = MAX_SCAN_PER_RUN
    for name, image, cid in container_rows:
        iid = cache.get(image, {}).get("id") or image_id(image)
        entry = cache.get(image)
        fresh = bool(entry and entry.get("id") == iid
                     and time.time() - entry.get("scanned_at", 0) < SCAN_MAX_AGE_DAYS * 86400)
        if fresh:
            cached.append(image)
            per_image[image] = entry.get("findings", [])
            continue
        if budget <= 0:
            continue
        findings, err = trivy_scan(image)
        budget -= 1
        if findings is None:
            scan_errors.append(f"{image}: {err}")
            continue
        cache[image] = {"id": iid, "scanned_at": time.time(), "findings": findings}
        save_json(CACHE_FILE, cache)
        scanned.append(image)
        per_image[image] = findings

    # refresh cached entries into per_image even when not rescanned
    for image, entry in cache.items():
        per_image.setdefault(image, entry.get("findings", []))

    for image, findings in per_image.items():
        for f in findings:
            key = f"{f['id']}|{f['pkg']}|{image}"
            if key not in seen:
                new_findings.append((image, f))
                seen[key] = now.strftime("%Y-%m-%d")

    # ---- runtimes ----
    node_rows = []
    for name, image, cid in container_rows:
        v = node_version("primary", name)
        if v:
            node_rows.append(("primary", name, v))
    for name, image, cid in sp_rows:
        v = node_version("media", name)
        if v:
            node_rows.append(("media", name, v))

    # Explicit path FIRST: under cron, `node` on PATH is the agent runtime's
    # bundled node, not the system install, and reporting that as the host Node is wrong.
    host_node = "n/a"
    for cand in ([r"/usr/local/bin/node", "--version"], ["node", "--version"]):
        rc, out, _ = run(cand, timeout=30)
        if rc == 0 and out.strip().startswith("v"):
            host_node = out.strip().replace("\r", "")
            break

    latest = latest_versions()
    latest_node = latest.get(24) or latest.get(22) or ""

    eol = [r for r in node_rows if r[2].split(".")[0] in ("18", "20")]
    behind = [r for r in node_rows if latest and r[2] != latest.get(
        int(r[2].split(".")[0]))]

    # ---- report ----
    L = []
    L.append(f"SWEEP {now.strftime('%Y-%m-%d %H:%M')} ET")
    L.append(f"containers: {len(container_rows)} primary / {len(sp_rows)} media"
             + (f" (primary probe error: {primary_err})" if primary_err else "")
             + (f" (media probe error: {media_err})" if media_err else ""))
    L.append("")
    L.append(f"IMAGES SCANNED THIS RUN: {len(scanned)} (cached: {len(cached)})")
    if scan_errors:
        L.append("SCAN ERRORS: " + "; ".join(scan_errors[:5]))

    live_images = {img for _, img, _ in container_rows}
    with_findings = [(img, fs) for img, fs in per_image.items()
                     if img in live_images and fs]
    with_findings.sort(key=lambda kv: -len(kv[1]))
    if with_findings:
        L.append("")
        L.append("FIXABLE HIGH/CRITICAL FINDINGS (primary images; media images are NOT scanned)")
        for img, fs in with_findings[:15]:
            crit = sum(1 for f in fs if f["sev"] == "CRITICAL")
            L.append(f"- {img}: {len(fs)} fixable ({crit} critical)")
            for f in fs[:3]:
                L.append(f"    {f['sev']} {f['id']} {f['pkg']} {f['installed']} -> {f['fixed']}  {f['title'][:90]}")
    else:
        L.append("")
        L.append("FIXABLE HIGH/CRITICAL FINDINGS (primary images): none")

    if new_findings:
        L.append("")
        label = "NEW SINCE LAST RUN" if not first_run else "NEW (baseline run - everything is new)"
        L.append(f"{label}: {len(new_findings)}")
        for img, f in new_findings[:10]:
            L.append(f"- {img}: {f['sev']} {f['id']} {f['pkg']} -> {f['fixed']}")

    L.append("")
    L.append("NODE RUNTIMES" + (f" (latest in current lines: 22={latest.get(22)} 24={latest.get(24)} 26={latest.get(26)})" if latest else ""))
    for h, n, v in sorted(node_rows):
        L.append(f"- {h}/{n}: {v}")
    if host_node != "n/a":
        L.append(f"- primary host node (system install): {host_node}")
    if eol:
        L.append("EOL NODE (no security fixes possible): " +
                 ", ".join(f"{n} {v}" for _, n, v in eol))
    if behind:
        L.append("BEHIND LATEST PATCH: " +
                 ", ".join(f"{n} {v}->{latest.get(int(v.split('.')[0]))}" for _, n, v in behind))
    if not behind and not eol:
        L.append("NODE: every Node runtime is on the newest patch of its line")

    L.append("")
    L.append("AUTO-PATCH ALLOWLIST: " + (", ".join(sorted(auto)) if auto else "(empty - nothing auto-applies)"))

    scan_notes = []
    if cache:
        stale = [img for img, e in cache.items() if img in live_images and e.get("id") != image_id(img)]
        if stale:
            scan_notes.append("image changed since last scan: " + ", ".join(stale[:8]))
    if scan_notes:
        L.append("")
        L.extend(scan_notes)

    save_json(SEEN_FILE, seen)
    L.append("")
    L.append(f"({time.time()-started:.0f}s)")
    print("\n".join(L))
    return 0

if __name__ == "__main__":
    sys.exit(main())
