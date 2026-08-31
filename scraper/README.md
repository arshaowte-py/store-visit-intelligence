# Doctor Database Scraper

Builds a database of doctors and clinics — **city-wise and area-wise** — from
Google Maps, via [SerpAPI](https://serpapi.com). Results land in a local SQLite
database (`doctors.db`) and export to CSV for outreach lists, CRM import, or
mapping against store catchments.

## Why SerpAPI + Google Maps

- Google Maps is the most complete public directory of clinics in India:
  name, address, phone, website, rating, review count, GPS coordinates,
  category — all structured.
- SerpAPI does the scraping legally on their side and returns clean JSON, so
  there is nothing to maintain when Google changes markup, and no risk of
  your own IPs getting blocked.
- A single city-level query caps out around 100 results. This scraper instead
  queries **per area per specialty** ("orthopaedic doctor in Baner, Pune"),
  which is what surfaces the long tail of local clinics — and gives you the
  area column for free.

Directories like Practo/JustDial have richer profiles (consultation fees,
qualifications) but no official API, and scraping them directly violates their
terms. Google Maps via SerpAPI is the clean starting point; enrich later if
needed.

## Setup

**1. Get a SerpAPI key**

Register at [serpapi.com](https://serpapi.com) → the key is on
[Your Account → API key](https://serpapi.com/manage-api-key). The free tier
gives a small monthly search quota — enough to pilot one city. Full multi-city
runs need a paid plan (see [serpapi.com/pricing](https://serpapi.com/pricing);
entry plans are a few thousand searches per month).

**2. Install**

```bash
cd scraper
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**3. Configure the key**

```bash
cp .env.example .env
# open .env and paste your key
```

**4. Pick cities, areas and specialties**

Edit `config.yaml`. It ships with Pune (14 areas) active and Mumbai/Bangalore
commented out, and three specialties: orthopaedic doctor, physiotherapist,
podiatrist. Every area × specialty combination is one query; every query costs
up to `max_pages_per_query` searches (1 page = 1 search = up to 20 results).

## Usage

Always start with `plan` — it shows every query and the exact search budget
before you spend anything:

```bash
python scrape_doctors.py plan
```

Pilot run (20 searches, well inside the free tier):

```bash
python scrape_doctors.py run --max-searches 20
```

Then keep running until done — **re-runs resume automatically**, already
fetched pages are never re-bought:

```bash
python scrape_doctors.py run --max-searches 100
```

Check progress and export:

```bash
python scrape_doctors.py status
python scrape_doctors.py export              # all cities -> exports/doctors_YYYYMMDD.csv
python scrape_doctors.py export --city Pune  # one city
```

Narrow a run when you need to:

```bash
python scrape_doctors.py run --city Pune --specialty "orthopaedic doctor"
python scrape_doctors.py run --area "Koregaon Park" --max-pages 3
```

## What gets stored

`doctors.db` (SQLite, gitignored) has three tables:

| Table | Purpose |
|---|---|
| `doctors` | One row per unique place (deduped on Google `place_id`): name, category, city, area, address, phone, website, rating, reviews, GPS, raw JSON. |
| `sightings` | Every (doctor, city, area, specialty) pair — a clinic on an area boundary can rank in several queries. |
| `searches` | Every API search made, which is what makes runs resumable and the quota spend auditable. |

The CSV export carries: name, specialty, category, city, area, address, phone,
website, rating, reviews, latitude, longitude, place_id, first_seen.

## Cost planning

`searches = areas × specialties × pages`. The default config
(Pune: 14 areas × 3 specialties × 2 pages) is **84 searches** for full Pune
coverage. Adding Mumbai (9 areas) and Bangalore (6 areas) at the same depth
adds 90 more. `plan` always shows the current number.

Two guard rails protect the quota:

- `max_searches_per_run` (default 50) stops a run before it overspends;
  override per run with `--max-searches`.
- Completed query pages are recorded in the `searches` table and skipped
  forever, so interrupting and re-running never wastes a search.

## Notes

- This collects **public business-listing data** (what anyone sees on Google
  Maps). Use it responsibly for outreach — no bulk unsolicited messaging.
- Keep `.env` out of git (already covered by the repo `.gitignore`). If the
  key ever leaks, regenerate it in the SerpAPI dashboard.
- To refresh data later, delete rows from `searches` (or the whole
  `doctors.db`) and re-run; the upsert refreshes phone/website/rating on
  existing doctors rather than duplicating them.
