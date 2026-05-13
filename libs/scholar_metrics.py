from __future__ import annotations

import csv
import html
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import requests
from rich.console import Console
from rich.table import Table


REPO_ROOT = Path(__file__).resolve().parents[1]
IN_CSV = REPO_ROOT / "data" / "scholars.csv"
WATCHLIST_CSV = REPO_ROOT / "data" / "scholars_watchlist.csv"
OUT_CSV = REPO_ROOT / "data" / "out" / "scholars_metrics.csv"
OUT_FIELDS = ["name", "citation_count", "citation_5_count", "h_index", "h5_index"]
POST_URL = (
    "https://defaultccebdfa5e6fe4a0aae39063ff11c9d.c4.environment.api.powerplatform.com:443"
    "/powerautomate/automations/direct/workflows/8a66ae6a8de345d28e33c9d84e3f72cd"
    "/triggers/manual/paths/invoke?api-version=1&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0"
    "&sig=BTzoAac16DT0QudEWfT4mEF8OCju6dIJBTbBNsfb9As"
)
MAX_AGE = timedelta(hours=18, minutes=0)


Snapshot = dict[str, dict[str, str]]
MetricsFetcher = Callable[[str], dict[str, int]]
Watchlist = tuple[set[str], set[str]]


def fetch_scholar_metrics(scholar_id: str) -> dict[str, int]:
    from scholarly import scholarly

    author = scholarly.search_author_id(scholar_id)
    author = scholarly.fill(author, sections=["indices", "counts"])
    return {
        "citation_count": author.get("citedby", 0),
        "citation_5_count": author.get("citedby5y", 0),
        "h_index": author.get("hindex", 0),
        "h5_index": author.get("hindex5y", 0),
    }


def load_snapshot(path: Path) -> Snapshot:
    if not path.exists():
        return {}

    with path.open(newline="", encoding="utf-8") as handle:
        return {row["name"]: row for row in csv.DictReader(handle)}


def load_watchlist(path: Path) -> Watchlist:
    if not path.exists():
        return set(), set()

    watched_ids: set[str] = set()
    watched_names: set[str] = set()

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            scholar_id = (row.get("scholar_id") or "").strip()
            name = (row.get("name") or "").strip()
            if scholar_id:
                watched_ids.add(scholar_id)
            if name:
                watched_names.add(name)

    return watched_ids, watched_names


def save_snapshot(path: Path, results: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUT_FIELDS)
        writer.writeheader()
        writer.writerows([{field: row[field] for field in OUT_FIELDS} for row in results])


def is_snapshot_expired(path: Path, max_age: timedelta = MAX_AGE) -> bool:
    if not path.exists():
        return True

    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    if age > max_age:
        logging.info("Cache expired (age: %s), re-fetching all.", age)
        return True
    return False


def coerce_snapshot_entry(row: dict[str, str]) -> dict[str, int | str]:
    return {
        "name": row["name"],
        "citation_count": int(row.get("citation_count", 0) or 0),
        "citation_5_count": int(row.get("citation_5_count", 0) or 0),
        "h_index": int(row.get("h_index", 0) or 0),
        "h5_index": int(row.get("h5_index", 0) or 0),
    }


def results_differ_from_snapshot(results: list[dict], snapshot: Snapshot) -> bool:
    result_names = {entry["name"] for entry in results}
    if result_names != set(snapshot):
        return True

    for entry in results:
        cached_row = snapshot.get(entry["name"])
        if cached_row is None:
            return True
        cached = coerce_snapshot_entry(cached_row)
        if any(entry.get(field) != cached.get(field) for field in OUT_FIELDS if field != "name"):
            return True
    return False


def cached_int(cache_row: dict[str, str] | None, key: str) -> int | None:
    if not cache_row:
        return None

    value = cache_row.get(key)
    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_metric_for_post(value: int, previous: int | None) -> str:
    if previous is None:
        return f"**{value} (new)**"

    delta = value - previous
    if delta == 0:
        return str(value)

    sign = "+" if delta > 0 else ""
    return f"**{value} ({sign}{delta})**"


def format_fact_value(entry: dict, previous: dict[str, str] | None) -> str:
    return (
        f"Citations: {format_metric_for_post(entry['citation_count'], cached_int(previous, 'citation_count'))} | "
        f"Citations (5 years): {format_metric_for_post(entry['citation_5_count'], cached_int(previous, 'citation_5_count'))} | "
        f"H-Index: {format_metric_for_post(entry['h_index'], cached_int(previous, 'h_index'))} | "
        f"H5-Index: {format_metric_for_post(entry['h5_index'], cached_int(previous, 'h5_index'))}"
    )


def format_fact_name(name: str, scholar_id: str | None) -> str:
    if not scholar_id:
        return name
    safe_name = html.escape(name)
    safe_scholar_id = quote(scholar_id, safe="")
    return f"<a href=\"https://scholar.google.com/citations?user={safe_scholar_id}\">{safe_name}</a>"


def rank_by_citations(results: list[dict]) -> dict[str, int]:
    ranked = sorted(
        results,
        key=lambda entry: (
            -int(entry.get("citation_count", 0)),
            -int(entry.get("h_index", 0)),
            str(entry.get("name", "")).lower(),
        ),
    )
    return {entry["name"]: index for index, entry in enumerate(ranked, start=1)}


def is_watched_scholar(entry: dict, watched_ids: set[str], watched_names: set[str]) -> bool:
    scholar_id = (entry.get("scholar_id") or "").strip()
    name = (entry.get("name") or "").strip()
    watched_by_id = bool(scholar_id) and scholar_id in watched_ids
    watched_by_name = bool(name) and name in watched_names
    return watched_by_id or watched_by_name


def detect_overtakes(
    current_results: list[dict], baseline_snapshot: Snapshot, watchlist: Watchlist
) -> list[str]:
    watched_ids, watched_names = watchlist
    if not watched_ids and not watched_names:
        return []

    current_by_name = {entry["name"]: entry for entry in current_results}
    previous_entries: list[dict] = []
    for name, row in baseline_snapshot.items():
        current = current_by_name.get(name)
        if current is None:
            continue
        previous_entries.append({**coerce_snapshot_entry(row), "scholar_id": current.get("scholar_id")})

    if not previous_entries:
        return []

    previous_rank = rank_by_citations(previous_entries)
    current_rank = rank_by_citations(current_results)
    events: list[str] = []

    for watched in current_results:
        if not is_watched_scholar(watched, watched_ids, watched_names):
            continue

        watched_name = watched["name"]
        prev_watched = previous_rank.get(watched_name)
        curr_watched = current_rank.get(watched_name)
        if prev_watched is None or curr_watched is None:
            continue

        for other in current_results:
            other_name = other["name"]
            if other_name == watched_name:
                continue

            prev_other = previous_rank.get(other_name)
            curr_other = current_rank.get(other_name)
            if prev_other is None or curr_other is None:
                continue

            if prev_watched > prev_other and curr_watched < curr_other:
                events.append(
                    (
                        f"{format_fact_name(watched_name, watched.get('scholar_id'))} moved from "
                        f"#{prev_watched} to #{curr_watched} and overtook "
                        f"{format_fact_name(other_name, other.get('scholar_id'))} "
                        f"(#{prev_other} to #{curr_other})"
                    )
                )

    return events


def create_table_from_results(results: list[dict], title: str, console: Console) -> None:
    console.print(f"\n{title}", style="bold magenta")
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Name", justify="left")
    table.add_column("Citations", justify="right")
    table.add_column("Citations (5 years)", justify="right")
    table.add_column("H-Index", justify="right")
    table.add_column("H5-Index", justify="right")

    for entry in results:
        table.add_row(
            entry.get("name", ""),
            str(entry.get("citation_count", 0)),
            str(entry.get("citation_5_count", 0)),
            str(entry.get("h_index", 0)),
            str(entry.get("h5_index", 0)),
        )
    console.print(table)


class ScholarMetricsBot:
    def __init__(
        self,
        input_csv: Path = IN_CSV,
        watchlist_csv: Path = WATCHLIST_CSV,
        output_csv: Path = OUT_CSV,
        post_url: str = POST_URL,
        max_age: timedelta = MAX_AGE,
        fetcher: MetricsFetcher = fetch_scholar_metrics,
        console_factory: Callable[[], Console] = Console,
    ) -> None:
        self.input_csv = input_csv
        self.watchlist_csv = watchlist_csv
        self.output_csv = output_csv
        self.post_url = post_url
        self.max_age = max_age
        self.fetcher = fetcher
        self.console_factory = console_factory

    def read_authors(self) -> list[dict[str, str]]:
        with self.input_csv.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def collect_results(self) -> tuple[list[dict], Snapshot, bool]:
        stored_snapshot = load_snapshot(self.output_csv)
        snapshot_expired = is_snapshot_expired(self.output_csv, self.max_age)
        results: list[dict] = []
        snapshot_needs_write = snapshot_expired or not self.output_csv.exists()
        authors = self.read_authors()

        for index, row in enumerate(authors, start=1):
            name = row["name"]
            scholar_id = row["scholar_id"]

            if name in stored_snapshot and not snapshot_expired:
                logging.info("[%d/%d] skipping %s (cached)", index, len(authors), name)
                entry = {**coerce_snapshot_entry(stored_snapshot[name]), "scholar_id": scholar_id}
            else:
                logging.info("[%d/%d] fetching %s …", index, len(authors), name)
                try:
                    entry = {"name": name, "scholar_id": scholar_id, **self.fetcher(scholar_id)}
                    snapshot_needs_write = True
                except Exception as exc:
                    if name in stored_snapshot:
                        logging.error("  failed for %s, using stored snapshot: %s", name, exc)
                        entry = {**coerce_snapshot_entry(stored_snapshot[name]), "scholar_id": scholar_id}
                    else:
                        logging.error("  failed for %s: %s", name, exc)
                        continue

            results.append(entry)

        if {entry["name"] for entry in results} != set(stored_snapshot):
            snapshot_needs_write = True

        return results, stored_snapshot, snapshot_needs_write

    def post_results(
        self, results: list[dict], baseline_snapshot: Snapshot, overtakes: list[str]
    ) -> None:
        facts = [
            {
                "name": format_fact_name(entry["name"], entry.get("scholar_id")),
                "value": format_fact_value(entry, baseline_snapshot.get(entry["name"])),
            }
            for entry in results
        ]

        sections: list[dict] = [
            {
                "activityTitle": "🎓 Scholar Metrics",
                "activitySubtitle": f"Ranked by Citation Count — {len(results)} authors",
                "facts": facts,
                "markdown": True,
            }
        ]

        if overtakes:
            sections.append(
                {
                    "activityTitle": "📈 Watchlist Overtakes",
                    "activitySubtitle": f"{len(overtakes)} overtake event(s)",
                    "facts": [{"name": "Event", "value": event} for event in overtakes],
                    "markdown": False,
                }
            )

        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": "0076D7",
            "summary": "Scholar Metrics Update",
            "sections": sections,
        }

        try:
            response = requests.post(self.post_url, json=payload, timeout=30)
            response.raise_for_status()
            logging.info("POST succeeded (%s)", response.status_code)
        except requests.RequestException as exc:
            logging.warning("POST failed: %s", exc)

    def render_results(self, results: list[dict]) -> None:
        console = self.console_factory()
        create_table_from_results(
            sorted(results, key=lambda entry: entry["h_index"], reverse=True),
            title="Sorted by H-Index",
            console=console,
        )
        create_table_from_results(
            sorted(results, key=lambda entry: entry["citation_count"], reverse=True),
            title="Sorted by Citations",
            console=console,
        )

    def run(self) -> list[dict]:
        results, stored_snapshot, snapshot_needs_write = self.collect_results()
        watchlist = load_watchlist(self.watchlist_csv)

        if snapshot_needs_write:
            save_snapshot(self.output_csv, results)

        ranked_results = sorted(results, key=lambda entry: entry["citation_count"], reverse=True)
        self.render_results(ranked_results)

        overtakes = detect_overtakes(ranked_results, stored_snapshot, watchlist)
        if overtakes:
            logging.info("Detected %d watchlist overtake event(s).", len(overtakes))

        if results_differ_from_snapshot(results, stored_snapshot) or overtakes:
            logging.info("Changes detected, posting results.")
            self.post_results(ranked_results, stored_snapshot, overtakes)
        else:
            logging.info("No changes detected, skipping POST.")

        return ranked_results


def main() -> None:
    ScholarMetricsBot().run()
