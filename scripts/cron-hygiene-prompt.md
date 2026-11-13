You are auditing the agent's cron jobs for dead weight. This is READ-ONLY: never create, edit, pause, resume or remove a job, never restart the gateway.

1. Load the job store with python: `python -c "import json;d=json.load(open(r'/path/to/hermes/cron/jobs.json',encoding='utf-8-sig'));print(len(d['jobs']))"` then iterate the same file for the fields id, name, state, enabled, schedule.expr, script, prompt, paused_at, last_run_at, last_error. Also check whether any `/path/to/hermes/profiles/*/cron/jobs.json` exist and include those jobs too, tagging each with its profile name. Never match on `job_id` (the field is `id`) and never compare `schedule` to a string (it is an object).

2. Flag a job if ANY of these hold:
   - paused/disabled AND `paused_at` is more than 30 days old (compute the day count; it is a candidate for removal or resume)
   - its `script` basename does not exist under `/path/to/hermes/scripts/` (name the missing file)
   - `last_error` is non-empty while the job is paused (quote the first line only)
   - it is a near-duplicate or superseded pair: two jobs covering the same task, or a watcher/watchdog/state job whose name mentions relaunch, finish, regen or apply while a newer job covers the same run
   - it is a watcher/watchdog/state job and the run it watched is provably finished (check the job's own state/log file beside its script in the scripts dir for a completion marker). If you cannot prove completion, do not guess: put it under 'Needs your call'.

3. Also report orphaned state files: files in the scripts dir (e.g. *.state, .*-state.json) that belong to a job that no longer exists, one line each.

4. Output rules - your final response is delivered verbatim to the operator: second person, no greeting, no sign-off, no 'nothing to report' line, no 'all clear'. At most two headers: `**Removable**` and `**Needs your call**`. One line per finding in the form `- <name> (<id>) — <specific reason with the exact number, day count or error> → <one-line recommended action>`. Finish with `N jobs audited, M flagged.` Say nothing about healthy, scheduled jobs.
