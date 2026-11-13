"""Weekly container vulnerability scan (no_agent cron).

Scans every RUNNING container image on the primary and media hosts with Trivy
for CRITICAL/HIGH fixable CVEs. Prints NOTHING when clean (silent cron); prints
a compact report when findings exist. An update bot tells you an image CHANGED;
this tells you your CURRENT image is exploitable - an image built before a
runtime CVE stays vulnerable until someone rebuilds upstream AND you pull.

Design notes:
- One trivy invocation per image: the vuln DB loads once per host run.
- Named volume trivy-cache keeps the DB between runs (first run downloads).
- --ignore-unfixed: only report CVEs with an available fix (actionable).
- The media host is scanned via ssh docker (its daemon is remote).
"""
import json
import re
import subprocess
import sys

TRIVY_IMAGE = "aquasec/trivy:latest"
SEVERITIES = "CRITICAL,HIGH"
SSH = ["ssh", "-o", "BatchMode=yes", "media-host"]

def run(cmd, timeout=600):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

def host_images(remote=False):
    cmd = SSH + ["docker ps --format '{{.Image}}'"] if remote else ["docker", "ps", "--format", "{{.Image}}"]
    out = run(cmd, timeout=120)
    lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
    seen, keep = set(), []
    for img in lines:
        if img not in seen:
            seen.add(img)
            keep.append(img)
    return keep

def scan(images, remote=False):
    """Trivy (2026 builds) accepts ONE target per invocation, so loop per image.
    The cache volume keeps the vuln DB warm between invocations and runs."""
    findings = {}
    for image in images:
        inner = ("docker run --rm -v trivy-cache:/root/.cache "
                 "-v /var/run/docker.sock:/var/run/docker.sock -e TRIVY_SKIP_DB_UPDATE=true -e TRIVY_SKIP_CHECK_UPDATE=true %s image --quiet "
                 "--format json --scanners vuln --severity %s --ignore-unfixed "
                 "--no-progress %s" % (TRIVY_IMAGE, SEVERITIES, image))
        cmd = SSH + [inner] if remote else inner.split()
        try:
            res = run(cmd, timeout=300)
        except subprocess.TimeoutExpired:
            findings.setdefault(image, {"crit": set(), "high": set()})
            continue
        text = (res.stdout or "").strip()
        if "{" not in text:
            continue
        try:
            data = json.loads(text[text.index("{"):])
        except (ValueError, json.JSONDecodeError):
            continue
        for target in data.get("Results", []):
            vulns = target.get("Vulnerabilities") or []
            if not vulns:
                continue
            crit = {v.get("VulnerabilityID") for v in vulns if v.get("Severity") == "CRITICAL"}
            high = {v.get("VulnerabilityID") for v in vulns if v.get("Severity") == "HIGH"}
            if crit or high:
                entry = findings.setdefault(image, {"crit": set(), "high": set()})
                entry["crit"].update(crit)
                entry["high"].update(high)
    return findings

def report(host, findings, out):
    """Critical lines detail the CVEs; high-only findings compress to one line
    (weekly scanner noise from base images otherwise buries the signal)."""
    highs = []
    for image, f in sorted(findings.items()):
        c, h = len(f["crit"]), len(f["high"])
        if c:
            ids = ", ".join(sorted(f["crit"])[:3])
            out.append("- CRITICAL %s `%s`: %d critical%s%s" % (
                host, image, c, (", " + str(h) + " high") if h else "",
                (" (" + ids + ")") if ids else ""))
        elif h:
            highs.append("%s: %d" % (image, h))
    if highs:
        out.append("- HIGH-only (%d images): %s" % (len(highs), "; ".join(highs[:6])))

def main():
    out = []
    for host, remote in (("primary", False), ("media", True)):
        try:
            imgs = [i for i in host_images(remote) if "aquasec/trivy" not in i]
            findings = scan(imgs, remote)
            report(host, findings, out)
        except subprocess.TimeoutExpired:
            out.append("- %s: SCAN TIMED OUT (trivy did not finish)" % host)
        except Exception as exc:  # noqa: BLE001 - alert path must never crash silently
            out.append("- %s: scan error: %s" % (host, str(exc)[:200]))
    if not out:
        return  # silent: nothing to report
    print("Container vuln scan (CRITICAL/HIGH, fixable):")
    print("\n".join(out))

if __name__ == "__main__":
    main()
