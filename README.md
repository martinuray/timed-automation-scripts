# timed-automation-scripts

Automated scripts for recurring data tasks, plus ops tooling for scheduled runs.

## Repository layout

- `jobs/` - Python jobs (for example `jobs/scholar_metric_bot.py`)
- `data/` - input/output CSV files
- `ops/git/auto_pull.sh` - scheduled Git update helper
- `logs/` - runtime logs

## Git auto-pull script

The script `ops/git/auto_pull.sh` safely keeps this repo updated when run from cron.

What it does:
- prevents overlapping runs with `flock`
- skips pull if the repo has uncommitted changes
- fetches from `origin/<current-branch>`
- pulls only with `--ff-only` (no merge commits)
- logs all outcomes to `logs/git/auto_pull.log`

### Setup

1. Ensure requirements exist:
   - Linux with `bash`, `git`, and `flock` available
   - repository cloned locally
   - branch has an upstream set (for example `origin/main`)

2. Make the script executable:

```bash
chmod +x /home/muray/Code/Research/etc/timed-automation-scripts/ops/git/auto_pull.sh
```

3. Test one manual run:

```bash
/home/muray/Code/Research/etc/timed-automation-scripts/ops/git/auto_pull.sh
```

4. Review the log output:

```bash
tail -n 50 /home/muray/Code/Research/etc/timed-automation-scripts/logs/git/auto_pull.log
```

5. Add cron schedule (example: every 15 minutes):

```bash
crontab -e
```

Add this line:

```cron
*/15 * * * * /home/muray/Code/Research/etc/timed-automation-scripts/ops/git/auto_pull.sh
```

6. Verify installed cron entries:

```bash
crontab -l
```

### Notes

- If local edits exist, the script logs `skip: working tree has uncommitted changes` and does nothing.
- If your branch is diverged, the script logs and skips, so you can resolve manually.
- If upstream is not set for the current branch, set it once:

```bash
git -C /home/muray/Code/Research/etc/timed-automation-scripts branch --set-upstream-to=origin/$(git -C /home/muray/Code/Research/etc/timed-automation-scripts rev-parse --abbrev-ref HEAD)
```

## Running the scholar job manually

From the repository root, either activate your virtual environment first or call its Python interpreter directly.

```bash
cd /home/muray/Code/Research/etc/timed-automation-scripts
/path/to/venv/bin/python jobs/scholar_metric_bot.py
```

If you already activated the virtual environment in your shell, `python jobs/scholar_metric_bot.py` works as well.

### Running the scholar job via cron

Cron runs with a very small environment, so use absolute paths and prefer the virtual environment's Python executable directly.

1. Identify your virtual environment path (example: `/path/to/venv`).

2. Edit your crontab:

```bash
crontab -e
```

3. Add a line for your desired schedule. Example: daily at 9 AM:

```cron
0 9 * * * cd /home/muray/Code/Research/etc/timed-automation-scripts && /path/to/venv/bin/python jobs/scholar_metric_bot.py
```

If you explicitly want to activate the environment first, run the command through the matching shell instead of relying on cron's default `/bin/sh`:

```cron
0 9 * * * /bin/zsh -lc 'source /path/to/venv/bin/activate && cd /home/muray/Code/Research/etc/timed-automation-scripts && python jobs/scholar_metric_bot.py'
```

**Important notes:**
- Replace `/path/to/venv` with the actual path to your Python virtual environment.
- Every run writes the same CLI output to `logs/scholar_metric_bot/<timestamp>.log` in addition to the terminal/cron output.
- Running with `cd /home/muray/Code/Research/etc/timed-automation-scripts` keeps imports and relative job paths predictable.
- The script reads `data/scholars.csv` and stores the latest snapshot in `data/out/scholars_metrics.csv`.
- Cron usually does not load your interactive shell profile, so do not rely on aliases or shell-initialized environment variables unless you load them yourself.

4. Verify the cron entry was added:

```bash
crontab -l
```

