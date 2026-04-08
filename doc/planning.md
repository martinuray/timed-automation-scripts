You’re at the right point to put a small “job platform” in place now so adding future scripts stays clean.

- [ ] Standardize job layout (scripts, config, logs, data, ops)
- [ ] Make each Python script cron-safe (absolute paths, env-based secrets, exit codes)
- [ ] Add a single reusable runner shell script for locking/logging/venv
- [ ] Add one cron entry per job interval
- [ ] Keep job definitions documented in-repo (`README` + `crontab.example`)

Here’s the setup I’d recommend for your current `get_data.py` and future scripts.

## Recommended structure

```text
scholar-grep/
  jobs/
    scholar_metrics.py          # renamed from get_data.py (or keep original)
    other_job.py
  lib/
    common.py                   # shared helpers later (locking, notif, io)
  config/
    jobs.env                    # secrets / URLs / toggles (not committed)
    jobs.env.example            # committed template
  data/
    scholars.csv
    out/
  logs/
  ops/
    cron/
      run_job.sh
      crontab.example
  README.md
```

Why this scales:
- `jobs/` stays a flat list of runnable scripts.
- `ops/cron/run_job.sh` centralizes execution behavior once.
- Future scripts just plug in via one new cron line.

## Important fixes for `get_data.py` before cron

In `get_data.py`, there are 2 cron-sensitive issues to address:

- Relative paths: `IN_CSV = Path("scholars.csv")`, `OUT_CSV = Path("out/...")` depend on current working directory.
- Hardcoded webhook secret in `POST_URL` should be moved to env var.

Suggested pattern in code:
- Base paths from `Path(__file__).resolve().parent` (or repo root).
- `POST_URL = os.environ["SCHOLAR_POST_URL"]` (fail fast if missing).

## Reusable runner script (for all jobs)

Create `ops/cron/run_job.sh` and run every job through it:

```bash
#!/usr/bin/env bash
set -euo pipefail

JOB_NAME="${1:?job name required}"         # e.g. scholar_metrics
REPO_DIR="/home/muray/Code/Research/etc/scholar-grep"
VENV_PY="$REPO_DIR/.venv/bin/python"
JOB_FILE="$REPO_DIR/jobs/${JOB_NAME}.py"
LOG_FILE="$REPO_DIR/logs/${JOB_NAME}.log"
LOCK_FILE="/tmp/${JOB_NAME}.lock"

mkdir -p "$REPO_DIR/logs"

# Prevent overlapping runs
exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

cd "$REPO_DIR"
# Optional: source env file if you use one
# set -a; source "$REPO_DIR/config/jobs.env"; set +a

"$VENV_PY" "$JOB_FILE" >>"$LOG_FILE" 2>&1
```

## Cron setup

Example `ops/cron/crontab.example`:

```cron
# Every 6 hours
0 */6 * * * /home/muray/Code/Research/etc/scholar-grep/ops/cron/run_job.sh scholar_metrics

# Another job daily at 08:15
15 8 * * * /home/muray/Code/Research/etc/scholar-grep/ops/cron/run_job.sh other_job
```

Install/edit cron:

```bash
crontab -e
```

Optional check:

```bash
crontab -l
```

## Operating model as scripts grow

- Keep each job independent and idempotent (safe to rerun).
- One cron line per job is simplest and very maintainable up to dozens of jobs.
- If you later need dependencies/retries/monitoring, migrate to:
  1) `systemd` timers (great on Linux host), or  
  2) workflow orchestrator (Airflow/Prefect) if complexity grows a lot.

If you want, I can sketch the exact refactor plan for your current `get_data.py` into `jobs/scholar_metrics.py` (including env loading + absolute paths + safer logging) without changing behavior.