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

Use your virtual environment and run:

```bash
python /home/muray/Code/Research/etc/timed-automation-scripts/jobs/scholar_metric_bot.py
```

