#!/usr/bin/env python3
"""restic-verify.py - monthly repo integrity check + restore drill.

Watchdog contract: prints NOTHING on success (empty stdout = no message).
Prints a short failure report otherwise. Always exits 0, so the cron
job's delivery is the stdout rather than an engine-level error.

What it proves, in order:
  1. `restic check --read-data-subset=N%`  -> repo structure + a real slice of pack data
  2. restore latest app db from the repo   -> SQLite opens it AND passes integrity_check
  3. restore latest proxy config from repo -> file is present and looks like our config

A backup that has never been restored is a hypothesis. This runs monthly.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

REPO = r"/srv/restic-repo"
RESTIC = r"/usr/local/bin/restic"
SUBSET = "5%"
TIMEOUT = 5400  # 90 min hard ceiling per restic call

DB_TAG = "app_data"
DB_INCLUDE = "/data/app.db"
CFG_TAG = "proxy"
CFG_INCLUDE = "/data/proxy/Caddyfile"

failures: list[str] = []
notes: list[str] = []

def restic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [RESTIC, "-r", REPO, *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )

def tail(text: str, n: int = 400) -> str:
    text = (text or "").strip().replace("\r", "")
    return text[-n:] if len(text) > n else text

def latest_snapshot(tag: str) -> str | None:
    p = restic("snapshots", "--json", "--tag", tag)
    if p.returncode != 0:
        failures.append(f"snapshots --tag {tag}: exit {p.returncode} :: {tail(p.stderr)}")
        return None
    try:
        snaps = json.loads(p.stdout or "[]")
    except json.JSONDecodeError as exc:
        failures.append(f"snapshots --tag {tag}: unparseable json ({exc})")
        return None
    if not snaps:
        failures.append(f"no snapshots found for tag {tag}")
        return None
    return max(snaps, key=lambda s: s.get("time", ""))["short_id"]

def step_check() -> None:
    t0 = time.time()
    p = restic("check", "--read-data-subset", SUBSET)
    if p.returncode != 0:
        failures.append(f"restic check --read-data-subset {SUBSET}: exit {p.returncode} :: {tail(p.stderr)}")
    else:
        notes.append(f"check ok ({time.time() - t0:.0f}s)")

def step_restore_db(scratch: str) -> None:
    snap = latest_snapshot(DB_TAG)
    if not snap:
        return
    target = os.path.join(scratch, "db")
    p = restic("restore", snap, "--target", target, "--include", DB_INCLUDE)
    if p.returncode != 0:
        failures.append(f"restore {DB_TAG}@{snap}: exit {p.returncode} :: {tail(p.stderr)}")
        return
    restored = os.path.join(target, DB_INCLUDE.lstrip("/").replace("/", os.sep))
    if not os.path.isfile(restored):
        failures.append(f"restore {DB_TAG}@{snap}: {DB_INCLUDE} missing after restore")
        return
    try:
        con = sqlite3.connect(f"file:{restored}?mode=ro", uri=True)
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        recipes = con.execute("select count(*) from recipes").fetchone()[0]
        con.close()
    except Exception as exc:  # noqa: BLE001 - any failure here is the finding
        failures.append(f"restored app db will not open: {type(exc).__name__}: {exc}")
        return
    if integrity != "ok":
        failures.append(f"restored app db integrity_check = {integrity!r}")
    else:
        notes.append(f"db ok ({recipes} recipes, {os.path.getsize(restored) // 1024} KiB)")

def step_restore_config(scratch: str) -> None:
    snap = latest_snapshot(CFG_TAG)
    if not snap:
        return
    target = os.path.join(scratch, "cfg")
    p = restic("restore", snap, "--target", target, "--include", CFG_INCLUDE)
    if p.returncode != 0:
        failures.append(f"restore {CFG_TAG}@{snap}: exit {p.returncode} :: {tail(p.stderr)}")
        return
    restored = os.path.join(target, CFG_INCLUDE.lstrip("/").replace("/", os.sep))
    if not os.path.isfile(restored):
        failures.append(f"restore {CFG_TAG}@{snap}: {CFG_INCLUDE} missing after restore")
        return
    body = open(restored, "r", encoding="utf-8", errors="replace").read()
    if len(body) < 500 or "example.local" not in body:
        failures.append(f"restored proxy config looks wrong ({len(body)} bytes, no 'example.local')")
    else:
        notes.append(f"config ok ({len(body)} bytes)")

def main() -> int:
    verbose = "--verbose" in sys.argv
    if not os.environ.get("RESTIC_PASSWORD"):
        print("RESTIC VERIFY FAILED - RESTIC_PASSWORD not set in this environment; drill cannot run.")
        return 0
    if not os.path.isfile(RESTIC):
        print(f"RESTIC VERIFY FAILED - restic binary not found at {RESTIC}")
        return 0

    scratch = tempfile.mkdtemp(prefix="restic-drill-")
    t0 = time.time()
    try:
        step_check()
        step_restore_db(scratch)
        step_restore_config(scratch)
    except subprocess.TimeoutExpired as exc:
        failures.append(f"timeout after {TIMEOUT}s in {exc.cmd}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"unexpected error: {type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    if failures:
        stamp = time.strftime("%Y-%m-%d %H:%M")
        print(f"RESTIC VERIFY FAILED - {stamp} (repo {REPO})")
        for f in failures:
            print(f"- {f}")
        if notes:
            print("passed before the failure: " + "; ".join(notes))
        return 0

    if verbose:
        print(f"restic verify OK - {time.strftime('%Y-%m-%d %H:%M')} - " + "; ".join(notes))
    return 0

if __name__ == "__main__":
    sys.exit(main())
