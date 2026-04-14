import csv
import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests
from rich.console import Console
from rich.table import Table
from scholarly import scholarly

# Setup logging with both console and file handlers
log_format = "%(asctime)s %(levelname)s %(message)s"
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter(log_format))
logger.addHandler(console_handler)

# File handler
log_dir = Path("logs/scholar_metric_bot")
log_dir.mkdir(parents=True, exist_ok=True)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
file_handler = logging.FileHandler(log_dir / f"{timestamp}.log")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter(log_format))
logger.addHandler(file_handler)

IN_CSV     = Path("data/scholars.csv")
OUT_CSV    = Path("data/out/scholars_metrics.csv")
OUT_FIELDS = ['name', 'citation_count', 'citation_5_count', 'h_index', 'h5_index']
POST_URL   = (
    'https://defaultccebdfa5e6fe4a0aae39063ff11c9d.c4.environment.api.powerplatform.com:443'
    '/powerautomate/automations/direct/workflows/8a66ae6a8de345d28e33c9d84e3f72cd'
    '/triggers/manual/paths/invoke?api-version=1&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0'
    '&sig=BTzoAac16DT0QudEWfT4mEF8OCju6dIJBTbBNsfb9As'
)
MAX_AGE = timedelta(hours=23, minutes=55)


# ── helpers ───────────────────────────────────────────────────────────────────

def has_changes(results: list[dict], cache: dict[str, dict]) -> bool:
    """Return True if any result differs from the cached version."""
    for entry in results:
        name = entry['name']
        if name not in cache:
            return True
        cached = {k: int(v) if k != 'name' else v for k, v in cache[name].items()}
        if any(entry.get(k) != cached.get(k) for k in OUT_FIELDS if k != 'name'):
            return True
    return False


def get_scholar_metrics(scholar_id: str) -> dict:
    author = scholarly.search_author_id(scholar_id)
    author = scholarly.fill(author, sections=['indices', 'counts'])
    return {
        'citation_count':   author.get('citedby',   0),
        'citation_5_count': author.get('citedby5y', 0),
        'h_index':          author.get('hindex',    0),
        'h5_index':         author.get('hindex5y',  0),
    }


def load_cached(path: Path) -> dict[str, dict]:
    """Return already-fetched rows keyed by name."""
    if not path.exists():
        return {}
    with open(path, newline='', encoding='utf-8') as f:
        return {row['name']: row for row in csv.DictReader(f)}


def is_cache_expired(path: Path) -> bool:
    """Return True if cache file is older than MAX_AGE."""
    if not path.exists():
        return True
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    if age > MAX_AGE:
        logging.info("Cache expired (age: %s), re-fetching all.", age)
        return True
    return False


def append_row(path: Path, row: dict) -> None:
    """Append a single result row; write header only when file is new."""
    is_new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def create_table_from_results(results: list[dict], title: str, console: Console | None = None) -> None:
    if console is None:
        console = Console()

    console.print(f"\n{title}", style="bold magenta")
    table = Table(show_header=True, header_style="bold magenta")
    for h, justify in [
        ('Name',               'left'),
        ('Citations',          'right'),
        ('Citations (5 years)','right'),
        ('H-Index',            'right'),
        ('H5-Index',           'right'),
    ]:
        table.add_column(h, justify=justify)

    for e in results:
        table.add_row(
            e.get('name', ''),
            str(e.get('citation_count',   0)),
            str(e.get('citation_5_count', 0)),
            str(e.get('h_index',          0)),
            str(e.get('h5_index',         0)),
        )
    console.print(table)


def _cached_int(cache_row: dict | None, key: str) -> int | None:
    """Return cached metric as int when available, otherwise None."""
    if not cache_row:
        return None
    value = cache_row.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_metric_for_post(value: int, previous: int | None) -> str:
    """Highlight changed values with markdown and show the delta."""
    if previous is None:
        return f"**{value} (new)**"

    delta = value - previous
    if delta == 0:
        return str(value)

    sign = "+" if delta > 0 else ""
    return f"**{value} ({sign}{delta})**"


def _format_fact_value(entry: dict, previous: dict | None) -> str:
    return (
        f"Citations: {_format_metric_for_post(entry['citation_count'], _cached_int(previous, 'citation_count'))} | "
        f"Citations (5 years): {_format_metric_for_post(entry['citation_5_count'], _cached_int(previous, 'citation_5_count'))} | "
        f"H-Index: {_format_metric_for_post(entry['h_index'], _cached_int(previous, 'h_index'))} | "
        f"H5-Index: {_format_metric_for_post(entry['h5_index'], _cached_int(previous, 'h5_index'))}"
    )


def post_results(results: list[dict], cache: dict[str, dict]) -> None:
    facts = [
        {
            "name": e['name'],
            "value": _format_fact_value(e, cache.get(e['name']))
        }
        for e in results
    ]

    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "0076D7",
        "summary": "Scholar Metrics Update",
        "sections": [{
            "activityTitle": "🎓 Scholar Metrics",
            "activitySubtitle": f"Ranked by Citation Count — {len(results)} authors",
            "facts": facts,
            "markdown": True
        }],
    }

    try:
        r = requests.post(POST_URL, json=payload, timeout=30)
        r.raise_for_status()
        logging.info("POST succeeded (%s)", r.status_code)
    except requests.RequestException as exc:
        logging.warning("POST failed: %s", exc)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    cache           = load_cached(OUT_CSV)  # Always load for change detection
    cache_expired   = is_cache_expired(OUT_CSV)
    results         = []

    with open(IN_CSV, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    for i, row in enumerate(rows, 1):
        name, scholar_id = row['name'], row['scholar_id']

        if name in cache and not cache_expired:
            logging.info("[%d/%d] skipping %s (cached)", i, len(rows), name)
            entry = {k: int(v) if k != 'name' else v for k, v in cache[name].items()}
        else:
            logging.info("[%d/%d] fetching %s …", i, len(rows), name)
            try:
                metrics = get_scholar_metrics(scholar_id)
            except Exception as exc:
                logging.error("  failed for %s: %s", name, exc)
                continue
            entry = {'name': name, **metrics}
            append_row(OUT_CSV, entry)   # persist immediately

        results.append(entry)


    results = sorted(results, key=lambda x: x['citation_count'], reverse=True)

    console = Console()
    create_table_from_results(
        sorted(results, key=lambda x: x['h_index'], reverse=True),
        title="Sorted by H-Index",
        console=console,
    )
    create_table_from_results(
        sorted(results, key=lambda x: x['citation_count'], reverse=True),
        title="Sorted by Citations",
        console=console,
    )

    if has_changes(results, cache):
        logging.info("Changes detected, posting results.")
        post_results(results, cache)
    else:
        logging.info("No changes detected, skipping POST.")


if __name__ == "__main__":
    main()