from __future__ import annotations

import configparser
import csv
import html
import logging
import os
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, cast
from urllib.parse import quote

import requests
from rich.console import Console
from rich.table import Table


REPO_ROOT = Path(__file__).resolve().parents[1]
IN_CSV = REPO_ROOT / "data" / "scholars.csv"
WATCHLIST_CSV = REPO_ROOT / "data" / "scholars_watchlist.csv"
SCHOLAR_METRICS_CONFIG_PATH = REPO_ROOT / "data" / "scholar_metrics.conf"
CACHE_BASE = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
OUT_CSV = CACHE_BASE / "timed-automation-scripts" / "scholar_metrics" / "scholars_metrics.csv"
OUT_FIELDS = ["name", "num_pubs", "citation_count", "citation_5_count", "cites_current_year", "h_index", "h5_index"]
MAX_AGE = timedelta(hours=18, minutes=0)


Snapshot = dict[str, dict[str, str]]
MetricsFetcher = Callable[[str], dict[str, int]]
Watchlist = tuple[set[str], set[str]]


def run_with_retry(action: str, operation: Callable[[], Any], retries: int = 3) -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return operation()
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt == retries:
                break
            delay = min(12.0, (2 ** (attempt - 1)) + random.uniform(0.2, 0.8))
            logging.warning(
                "%s failed (%d/%d): %s; retrying in %.1fs",
                action,
                attempt,
                retries,
                exc,
                delay,
            )
            time.sleep(delay)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{action} failed without an exception")


def fetch_scholar_metrics(scholar_id: str) -> dict[str, int]:
    from scholarly import scholarly

    try:
        from scholarly import ProxyGenerator
        pg = ProxyGenerator()
        if pg.FreeProxies():  # or pg.ScraperAPI("your_key") for reliability
            scholarly.use_proxy(pg)
            logging.info("Proxy setup successful.")
        else:
            logging.info("Proxy setup unavailable, continuing without proxy.")
    except Exception as exc:
        logging.debug("Proxy setup failed, continuing without proxy: %s", exc)

    author = cast(dict[str, Any], run_with_retry(
        "Author lookup",
        lambda: scholarly.search_author_id(scholar_id),
    ))
    author = cast(dict[str, Any], run_with_retry(
        "Author fill",
        lambda: scholarly.fill(author, sections=["indices", "counts", "publications"]),
    ))

    ## cites per year
    cpy = author.get('cites_per_year', {})
    cy = max(list(cpy.keys()))
    cites_current_year = cpy.get(cy, 0)

    self_citations = 0
    cleaned_cite_list = []
    publications = author.get('publications', [])
    num_pubs = len(publications)

    for pub in author.get('publications', []):
        break # till strategy on how to handle huge requests
        try:
            pub_filled = cast(dict[str, Any], run_with_retry("Publication fill", lambda: scholarly.fill(pub), retries=2))
        except Exception as exc:
            logging.debug("Skipping publication fill failure (scholar_id=%s): %s", scholar_id, exc)
            cleaned_cite_list.append(0)
            continue

        cits = 0

        # Some publications returned by Google Scholar do not expose citedby_url.
        if not pub_filled.get('citedby_url'):
            logging.debug(
                "Skipping citedby expansion for publication without citedby_url (scholar_id=%s)",
                scholar_id,
            )
            cleaned_cite_list.append(cits)
            continue

        try:
            for citing_paper in scholarly.citedby(pub_filled):
                # Check author overlap
                citing_author_ids = citing_paper.get('author_id', [])
                if author['scholar_id'] in citing_author_ids:
                    self_citations += 1
                else:
                    cits += 1
        except KeyError as exc:
            logging.debug(
                "Skipping citedby expansion for publication due to missing key %s (scholar_id=%s)",
                exc,
                scholar_id,
            )
        except Exception as exc:
            logging.debug(
                "Skipping citedby expansion after fetch error (scholar_id=%s): %s",
                scholar_id,
                exc,
            )

        cleaned_cite_list.append(cits)

    return {
        "num_pubs": num_pubs,
        "citation_count": author.get("citedby", 0),
        "citation_5_count": author.get("citedby5y", 0),
        "cites_current_year": cites_current_year,
        "h_index": author.get("hindex", 0),
        "h5_index": author.get("hindex5y", 0),
        'self_citations': self_citations,
        'cleaned_h_index': h_index(cleaned_cite_list),
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


def load_post_url_from_config(config_path: Path = SCHOLAR_METRICS_CONFIG_PATH) -> str | None:
    if not config_path.exists():
        return None

    config = configparser.RawConfigParser()
    config.read(config_path)
    post_url = config.get("WEBHOOK", "post_url", fallback="").strip()
    return post_url or None


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
        "num_pubs": int(row.get("num_pubs", 0) or 0),
        "citation_count": int(row.get("citation_count", 0) or 0),
        "citation_5_count": int(row.get("citation_5_count", 0) or 0),
        "cites_current_year": int(row.get("cites_current_year", 0) or 0),
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
        return f"<strong>{value}</strong> <em>(new)</em>"

    delta = value - previous
    if delta == 0:
        return str(value)

    sign = "+" if delta > 0 else ""
    return f"<strong>{value}</strong> <em>({sign}{delta})</em>"


def format_fact_value(entry: dict, previous: dict[str, str] | None) -> str:
    return (
        f"#Pubs: {format_metric_for_post(entry['num_pubs'], cached_int(previous, 'num_pubs'))} | "
        f"Citations: {format_metric_for_post(entry['citation_count'], cached_int(previous, 'citation_count'))} | "
        f"Citations (5 years): {format_metric_for_post(entry['citation_5_count'], cached_int(previous, 'citation_5_count'))} | "
        f"Citations (current year): {format_metric_for_post(entry['cites_current_year'], cached_int(previous, 'cites_current_year'))} | "
        f"H-Index: {format_metric_for_post(entry['h_index'], cached_int(previous, 'h_index'))} | "
        f"H5-Index: {format_metric_for_post(entry['h5_index'], cached_int(previous, 'h5_index'))}"
    )


def build_metrics_html_table(results: list[dict], baseline_snapshot: Snapshot) -> str:
    header = (
        "<table>"
        "<thead><tr>"
        "<th align=\"left\">Scholar</th>"
        "<th align=\"right\">#</th>"
        "<th align=\"right\">Citations</th>"
        "<th align=\"right\">Citations (5y)</th>"
        "<th align=\"right\">Citations (current y)</th>"
        "<th align=\"right\">H-Index</th>"
        "<th align=\"right\">H5-Index</th>"
        "</tr></thead><tbody>"
    )
    rows: list[str] = []
    for entry in results:
        previous = baseline_snapshot.get(entry["name"])
        rows.append(
            "<tr>"
            f"<td>{format_fact_name(entry['name'], entry.get('scholar_id'))}</td>"
            f"<td align=\"right\">{format_metric_for_post(entry['num_pubs'], cached_int(previous, 'num_pubs'))}</td>"
            f"<td align=\"right\">{format_metric_for_post(entry['citation_count'], cached_int(previous, 'citation_count'))}</td>"
            f"<td align=\"right\">{format_metric_for_post(entry['citation_5_count'], cached_int(previous, 'citation_5_count'))}</td>"
            f"<td align=\"right\">{format_metric_for_post(entry['cites_current_year'], cached_int(previous, 'cites_current_year'))}</td>"
            f"<td align=\"right\">{format_metric_for_post(entry['h_index'], cached_int(previous, 'h_index'))}</td>"
            f"<td align=\"right\">{format_metric_for_post(entry['h5_index'], cached_int(previous, 'h5_index'))}</td>"
            "</tr>"
        )

    return f"{header}{''.join(rows)}</tbody></table>"


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


def h_index(citations: list[int]) -> int:
    citations = sorted(citations, reverse=True)
    h = 0
    for i, c in enumerate(citations):
        if c >= i + 1:
            h = i + 1
        else:
            break
    return h

def create_table_from_results(results: list[dict], title: str, console: Console) -> None:
    console.print(f"\n{title}", style="bold magenta")
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Name", justify="left")
    table.add_column("#Publications", justify="right")
    table.add_column("Citations", justify="right")
    table.add_column("Citations (5 years)", justify="right")
    table.add_column("Citations (current year)", justify="right")
    table.add_column("H-Index", justify="right")
    table.add_column("H5-Index", justify="right")
    table.add_column("Self-Citations", justify="right")
    table.add_column("Cleaned H-Index", justify="right")

    for entry in results:
        table.add_row(
            entry.get("name", ""),
            str(entry.get("num_pubs", 0)),
            str(entry.get("citation_count", 0)),
            str(entry.get("citation_5_count", 0)),
            str(entry.get("cites_current_year", 0)),
            str(entry.get("h_index", 0)),
            str(entry.get("h5_index", 0)),
            str(entry.get("self_citations", 0)),
            str(entry.get("cleaned_h_index", 0)),
        )
    console.print(table)


class ScholarMetricsBot:
    def __init__(
        self,
        input_csv: Path = IN_CSV,
        watchlist_csv: Path = WATCHLIST_CSV,
        config_path: Path = SCHOLAR_METRICS_CONFIG_PATH,
        output_csv: Path = OUT_CSV,
        post_url: str | None = None,
        max_age: timedelta = MAX_AGE,
        force_refresh: bool = False,
        force_cache: bool = False,
        force_post: bool = False,
        fetcher: MetricsFetcher = fetch_scholar_metrics,
        console_factory: Callable[[], Console] = Console,
    ) -> None:
        self.input_csv = input_csv
        self.watchlist_csv = watchlist_csv
        self.config_path = config_path
        self.output_csv = output_csv
        self.post_url = post_url or load_post_url_from_config(config_path)
        self.max_age = max_age
        self.force_refresh = force_refresh
        self.force_cache = force_cache
        self.force_post = force_post
        self.fetcher = fetcher
        self.console_factory = console_factory

    def read_authors(self) -> list[dict[str, str]]:
        with self.input_csv.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def collect_results(self) -> tuple[list[dict], Snapshot, bool]:

        fetching_possible = True

        stored_snapshot = load_snapshot(self.output_csv)
        if self.force_refresh:
            snapshot_expired = True
        elif self.force_cache:
            snapshot_expired = False
        else:
            snapshot_expired = is_snapshot_expired(self.output_csv, self.max_age)

        if self.force_refresh:
            logging.info("Force mode enabled, bypassing cache age and fetching all metrics.")
        elif self.force_cache:
            logging.info("Force cache mode enabled, using stored metrics even if cache age is expired.")

        results: list[dict] = []
        snapshot_needs_write = (snapshot_expired or not self.output_csv.exists()) and not self.force_cache
        authors = self.read_authors()

        for index, row in enumerate(authors, start=1):
            name = row["name"]
            scholar_id = row["scholar_id"]

            if name in stored_snapshot and (self.force_cache or not snapshot_expired or not fetching_possible):
                logging.info("[%d/%d] using cache for %s", index, len(authors), name)
                entry = {**coerce_snapshot_entry(stored_snapshot[name]), "scholar_id": scholar_id}
            elif self.force_cache:
                logging.warning(
                    "[%d/%d] no cached entry for %s; skipping in force-cache mode",
                    index,
                    len(authors),
                    name,
                )
                continue
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
                    fetching_possible = False # to make it faster, when already in captcha mode.
                    continue

            results.append(entry)

        if not self.force_cache and {entry["name"] for entry in results} != set(stored_snapshot):
            snapshot_needs_write = True

        return results, stored_snapshot, snapshot_needs_write

    def post_results(
        self, results: list[dict], baseline_snapshot: Snapshot, overtakes: list[str]
    ) -> None:
        if not self.post_url:
            logging.warning("No webhook URL configured, skipping POST.")
            return

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

        if self.force_post:
            logging.info("Force post mode enabled, posting even when no metric changes are detected.")

        if snapshot_needs_write:
            save_snapshot(self.output_csv, results)

        ranked_results = sorted(results, key=lambda entry: entry["citation_count"], reverse=True)
        self.render_results(ranked_results)

        overtakes = detect_overtakes(ranked_results, stored_snapshot, watchlist)
        if overtakes:
            logging.info("Detected %d watchlist overtake event(s).", len(overtakes))

        changes_detected = results_differ_from_snapshot(results, stored_snapshot)
        should_post = changes_detected or bool(overtakes) or self.force_post

        if should_post:
            if self.force_post and not (changes_detected or overtakes):
                logging.info("No changes detected, but force post is enabled; posting results.")
            else:
                logging.info("Changes detected, posting results.")
            self.post_results(ranked_results, stored_snapshot, overtakes)
        else:
            logging.info("No changes detected, skipping POST.")

        return ranked_results


def main() -> None:
    ScholarMetricsBot().run()
