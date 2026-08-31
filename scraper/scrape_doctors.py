#!/usr/bin/env python3
"""
Doctor database scraper — SerpAPI, Google Maps engine.

Builds a SQLite database of doctors/clinics organised city-wise and
area-wise, from the queries defined in config.yaml.

Commands:
  plan     Show every query the config expands to and the search budget it needs.
  run      Execute searches (resumable — already-fetched pages are skipped).
  status   Show what is in the database so far.
  export   Write the doctors table to a CSV in exports/.

Setup and usage: see README.md in this directory.
"""

import argparse
import csv
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import os

import requests
import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional; a real env var works too
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "config.yaml"
DEFAULT_DB = BASE_DIR / "doctors.db"
EXPORT_DIR = BASE_DIR / "exports"
SERPAPI_URL = "https://serpapi.com/search.json"
RESULTS_PER_PAGE = 20  # google_maps returns at most 20 local_results per page

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id          INTEGER PRIMARY KEY,
    query       TEXT NOT NULL,
    city        TEXT NOT NULL,
    area        TEXT NOT NULL,
    specialty   TEXT NOT NULL,
    page        INTEGER NOT NULL,
    num_results INTEGER NOT NULL,
    searched_at TEXT NOT NULL,
    UNIQUE (query, page)
);

CREATE TABLE IF NOT EXISTS doctors (
    place_id   TEXT PRIMARY KEY,
    name       TEXT,
    category   TEXT,
    specialty  TEXT,
    city       TEXT,
    area       TEXT,
    address    TEXT,
    phone      TEXT,
    website    TEXT,
    rating     REAL,
    reviews    INTEGER,
    latitude   REAL,
    longitude  REAL,
    first_seen TEXT,
    last_seen  TEXT,
    raw_json   TEXT
);

-- One clinic often ranks in several area/specialty queries; sightings keeps
-- every (doctor, city, area, specialty) pair while doctors keeps one row.
CREATE TABLE IF NOT EXISTS sightings (
    place_id  TEXT NOT NULL,
    city      TEXT NOT NULL,
    area      TEXT NOT NULL,
    specialty TEXT NOT NULL,
    UNIQUE (place_id, city, area, specialty)
);
"""


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for key in ("cities", "specialties", "query_template"):
        if key not in cfg:
            sys.exit(f"config error: missing required key '{key}' in {path}")
    return cfg


def get_api_key():
    if load_dotenv is not None:
        load_dotenv(BASE_DIR / ".env")
    key = os.environ.get("SERPAPI_API_KEY", "").strip()
    if not key:
        sys.exit(
            "SERPAPI_API_KEY is not set.\n"
            "Copy .env.example to .env and paste your key from "
            "https://serpapi.com/manage-api-key, or export it in your shell."
        )
    return key


def open_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def iter_queries(cfg, city_filter=None, area_filter=None, specialty_filter=None):
    """Yield (city, area, specialty, query_text) for every combination."""
    template = cfg["query_template"]
    for city, areas in cfg["cities"].items():
        if city_filter and city.lower() != city_filter.lower():
            continue
        for area in areas:
            if area_filter and area.lower() != area_filter.lower():
                continue
            for specialty in cfg["specialties"]:
                if specialty_filter and specialty.lower() != specialty_filter.lower():
                    continue
                yield city, area, specialty, template.format(
                    specialty=specialty, area=area, city=city
                )


def serpapi_search(api_key, cfg, query, page):
    """One SerpAPI google_maps search. Returns a list of result dicts."""
    params = {
        "engine": "google_maps",
        "type": "search",
        "q": query,
        "gl": cfg.get("gl", "in"),
        "hl": cfg.get("hl", "en"),
        "start": page * RESULTS_PER_PAGE,
        "api_key": api_key,
    }
    for attempt in range(3):
        resp = requests.get(SERPAPI_URL, params=params, timeout=60)
        if resp.status_code == 429:
            wait = 2 ** (attempt + 2)
            print(f"    rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        data = resp.json()
        error = data.get("error")
        if error:
            # SerpAPI signals an empty result set through the error field.
            if "hasn't returned any results" in error:
                return []
            sys.exit(f"SerpAPI error: {error}")
        results = data.get("local_results", [])
        if not results and "place_results" in data:
            results = [data["place_results"]]
        return results
    sys.exit("SerpAPI kept rate-limiting after 3 attempts; try again later.")


def upsert_doctor(conn, r, city, area, specialty):
    """Insert or refresh one result. Returns True if the doctor is new."""
    place_id = r.get("place_id") or r.get("data_id")
    if not place_id:
        return False
    gps = r.get("gps_coordinates") or {}
    ts = now_utc()
    is_new = (
        conn.execute(
            "SELECT 1 FROM doctors WHERE place_id = ?", (place_id,)
        ).fetchone()
        is None
    )
    conn.execute(
        """INSERT INTO sightings (place_id, city, area, specialty)
           VALUES (?, ?, ?, ?)
           ON CONFLICT (place_id, city, area, specialty) DO NOTHING""",
        (place_id, city, area, specialty),
    )
    cur = conn.execute(
        """INSERT INTO doctors (place_id, name, category, specialty, city, area,
                                address, phone, website, rating, reviews,
                                latitude, longitude, first_seen, last_seen, raw_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (place_id) DO UPDATE SET
               phone     = COALESCE(excluded.phone, doctors.phone),
               website   = COALESCE(excluded.website, doctors.website),
               rating    = COALESCE(excluded.rating, doctors.rating),
               reviews   = COALESCE(excluded.reviews, doctors.reviews),
               last_seen = excluded.last_seen""",
        (
            place_id,
            r.get("title"),
            r.get("type"),
            specialty,
            city,
            area,
            r.get("address"),
            r.get("phone"),
            r.get("website"),
            r.get("rating"),
            r.get("reviews"),
            gps.get("latitude"),
            gps.get("longitude"),
            ts,
            ts,
            json.dumps(r, ensure_ascii=False),
        ),
    )
    return is_new


def cmd_plan(args, cfg, conn):
    max_pages = args.max_pages or cfg.get("max_pages_per_query", 2)
    queries = list(iter_queries(cfg, args.city, args.area, args.specialty))
    done = {
        (q, p)
        for q, p in conn.execute("SELECT query, page FROM searches").fetchall()
    }
    remaining = 0
    print(f"{'CITY':<12} {'AREA':<20} {'SPECIALTY':<22} QUERY")
    for city, area, specialty, query in queries:
        print(f"{city:<12} {area:<20} {specialty:<22} {query}")
        remaining += sum(1 for p in range(max_pages) if (query, p) not in done)
    print(f"\n{len(queries)} queries x up to {max_pages} pages "
          f"= {len(queries) * max_pages} searches max")
    print(f"{remaining} searches not yet done (re-runs skip completed pages)")
    print(f"budget per run: {args.max_searches or cfg.get('max_searches_per_run', 50)}"
          " (override with --max-searches)")


def cmd_run(args, cfg, conn):
    api_key = get_api_key()
    max_pages = args.max_pages or cfg.get("max_pages_per_query", 2)
    max_searches = args.max_searches or cfg.get("max_searches_per_run", 50)
    delay = cfg.get("delay_seconds", 1.0)

    searches_done = 0
    new_doctors = 0
    for city, area, specialty, query in iter_queries(
        cfg, args.city, args.area, args.specialty
    ):
        for page in range(max_pages):
            already = conn.execute(
                "SELECT 1 FROM searches WHERE query = ? AND page = ?", (query, page)
            ).fetchone()
            if already:
                continue
            if searches_done >= max_searches:
                print(f"\nStopping: hit the {max_searches}-search budget for this run.")
                print("Run again to continue — completed pages are skipped.")
                _summary(searches_done, new_doctors, conn)
                return
            print(f"[{searches_done + 1}/{max_searches}] {query} (page {page + 1})")
            results = serpapi_search(api_key, cfg, query, page)
            for r in results:
                if upsert_doctor(conn, r, city, area, specialty):
                    new_doctors += 1
            conn.execute(
                """INSERT INTO searches (query, city, area, specialty, page,
                                         num_results, searched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (query, city, area, specialty, page, len(results), now_utc()),
            )
            conn.commit()
            searches_done += 1
            print(f"    {len(results)} results")
            if len(results) < RESULTS_PER_PAGE:
                break  # last page for this query
            time.sleep(delay)
    print("\nAll configured queries are complete.")
    _summary(searches_done, new_doctors, conn)


def _summary(searches_done, new_doctors, conn):
    total = conn.execute("SELECT COUNT(*) FROM doctors").fetchone()[0]
    print(f"This run: {searches_done} searches, {new_doctors} new doctors.")
    print(f"Database total: {total} doctors.")


def cmd_status(args, cfg, conn):
    total = conn.execute("SELECT COUNT(*) FROM doctors").fetchone()[0]
    searches = conn.execute("SELECT COUNT(*) FROM searches").fetchone()[0]
    print(f"{total} doctors from {searches} searches\n")
    print("By city / area:")
    for city, area, n in conn.execute(
        """SELECT city, area, COUNT(*) FROM doctors
           GROUP BY city, area ORDER BY city, COUNT(*) DESC"""
    ):
        print(f"  {city:<12} {area:<20} {n}")
    print("\nBy specialty searched:")
    for specialty, n in conn.execute(
        "SELECT specialty, COUNT(*) FROM doctors GROUP BY specialty ORDER BY COUNT(*) DESC"
    ):
        print(f"  {specialty:<22} {n}")


def cmd_export(args, cfg, conn):
    EXPORT_DIR.mkdir(exist_ok=True)
    where, params = "", []
    if args.city:
        where = "WHERE city = ?"
        params.append(args.city)
    rows = conn.execute(
        f"""SELECT name, specialty, category, city, area, address, phone, website,
                   rating, reviews, latitude, longitude, place_id, first_seen
            FROM doctors {where}
            ORDER BY city, area, rating DESC""",
        params,
    ).fetchall()
    if not rows:
        sys.exit("Nothing to export yet — run some searches first.")
    suffix = f"_{args.city.lower()}" if args.city else ""
    out = EXPORT_DIR / f"doctors{suffix}_{datetime.now():%Y%m%d}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["name", "specialty", "category", "city", "area", "address", "phone",
             "website", "rating", "reviews", "latitude", "longitude", "place_id",
             "first_seen"]
        )
        writer.writerows(rows)
    print(f"Wrote {len(rows)} doctors to {out}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--city", help="limit to one city from the config")
    common.add_argument("--area", help="limit to one area")
    common.add_argument("--specialty", help="limit to one specialty")
    common.add_argument("--max-pages", type=int, help="pages per query (1 page = 1 search)")
    common.add_argument("--max-searches", type=int, help="search budget for this run")

    sub.add_parser("plan", parents=[common], help="show queries and search budget")
    sub.add_parser("run", parents=[common], help="execute searches")
    sub.add_parser("status", help="show database contents")
    export_p = sub.add_parser("export", help="write doctors to CSV")
    export_p.add_argument("--city", help="export a single city")

    args = parser.parse_args()
    # subcommands without the common parent still need these attributes
    for attr in ("city", "area", "specialty", "max_pages", "max_searches"):
        if not hasattr(args, attr):
            setattr(args, attr, None)

    cfg = load_config(args.config)
    conn = open_db(args.db)
    try:
        {"plan": cmd_plan, "run": cmd_run, "status": cmd_status, "export": cmd_export}[
            args.command
        ](args, cfg, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
