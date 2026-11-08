# Florida census globe

Local viewer for the 67 Florida counties already modeled by this pipeline. It is a review build. It is not deployed, and the Globe Catalog on the Ubuntu machine was not changed.

## What you can see

- MapLibre GL JS 5.6.1 draws the counties on a globe (a flat map toggle is in the same view).
- Layers are the governed metrics in `semantic/metrics.yml`: population, median household income, median age, Hispanic share, population growth, and nominal median household income growth.
- Class breaks are equal-count groups of the published county values. A missing estimate stays gray. It is not drawn as zero.
- A county panel shows the estimate, the 90% margin of error when the Bureau published one, the annotation when the margin is absent, and the official state figure beside it.
- Two counties can be compared. A 90% difference test is shown only when both margins of error exist and the metric is not a ratio or a growth rate. The difference is labeled as calculated here.
- The ask panel routes a question to one of those metrics. It does not accept SQL.

Vintages in this export are ACS 5-year 2019 (2015–2019) and ACS 5-year 2024 (2020–2024). Growth uses that non-overlapping pair. Income change is nominal.

## Reproduce the export

From a checkout, with [uv](https://docs.astral.sh/uv/) installed:

```bash
uv sync
uv run census-ingest --source summary_file --years 2019 2024
cd dbt && uv run dbt build --profiles-dir . && cd ..
uv run census-globe-export
```

`census-ingest` reads the official ACS table-based Summary File from `www2.census.gov` and sends `GeoKnightGIS-census-pipeline/1.0`. `www2.census.gov/robots.txt` does not disallow that agent. The files are the published bulk data, not scraped HTML pages. If a download returns a block (anything other than success), stop.

County lines are the Census Bureau 2024 cartographic boundary file `cb_2024_us_county_500k`, filtered to `STATEFP = 12`, joined on the 5-digit GEOID. Shorelines are generalized for display. Land behind the globe is Natural Earth 1:110m (public domain) and is not a census geography.

If those two zips are not already under `data/cache/geo/`, refresh them with:

```bash
uv run census-globe-export --download-geography
```

That command spaces the two downloads by 20 seconds and stops on a non-200 response.

The DuckDB warehouse stays gitignored. The committed globe files are:

- `globe/data/florida.json` — metrics, margins, flags, reconciliation
- `globe/data/fl_counties.geojson` — Florida counties
- `globe/data/ne_110m_land.geojson` — land context
- `globe/data/QC.md` — measured reconciliation and SHA-256 hashes of the source files used for this export

This export's measured state population is 20,901,636 (2019) and 22,416,077 (2024), each equal to the sum of the 67 counties. State population growth from 2019 to 2024 rounds to 0.0725. Those figures match `examples/agent_queries.md`, which was produced from an earlier Summary File load of the same vintages.

## Run the viewer

```bash
uv run census-globe
```

The process binds to `127.0.0.1:8765` only. Open `http://127.0.0.1:8765/`. The card layouts used for the catalog artwork are `http://127.0.0.1:8765/card` and `http://127.0.0.1:8765/card?variant=jazz`.

No Census API key is required for the Summary File path. Do not put a key in the page. If you create `.env`, restrict it with `chmod 600`. An `.env` that is readable by group or others is ignored for the language-model settings.

## Language model

The ask panel is grounded in the export:

1. A fixed router picks one governed metric, a county, a comparison, or a rank. Questions that look like SQL are refused.
2. The answer text is filled from that evidence. A test checks that the numbers in the sentence also appear in the evidence.
3. A model is called only when the adapter is explicitly enabled.

The adapter ships **disabled**. This checkout does not contain the local LLM Lab's base URL, request schema, authentication, or model id. A different public project, `jax-property-mcp`, calls `ollama.chat` with `AGENT_MODEL`. That is not treated as the Lab.

To attempt a later local connection, all of the following must be set, and the URL must be on `127.0.0.1` or `localhost`:

```
CENSUS_LLM_ENABLED=1
CENSUS_LLM_LAB_PROTOCOL=ollama_chat
CENSUS_LLM_LAB_URL=http://127.0.0.1:11434
CENSUS_LLM_LAB_MODEL=
```

`ollama_chat` posts to `{URL}/api/chat` with `stream: false` and `temperature: 0`. That shape is **not** verified as the LLM Lab contract. The response still says `contract_verified: false` and `live: false`. External URLs are refused so a paid API is not called by accident. `CENSUS_LLM_LAB_PROTOCOL=mock` repeats the governed text for tests and is not a model.

Still needed before anyone should describe the panel as the LLM Lab:

- The Lab's real base URL and transport (HTTP, stdio, or MCP).
- The Lab's real request and response schema.
- The model id the Lab expects.
- Whether the Lab expects to choose tools itself or to receive an evidence packet like this one.
- Any authentication header name. Do not put the secret in the browser or in git.

Questions are limited to 480 characters. The ask route allows 10 requests a minute per client address and reads at most 4 KB. A configured call times out after 20 seconds (maximum 30).

## Catalog

See `globe/catalog/INTEGRATION.md`. The catalog application was not reachable from this environment. Nothing here edits `~/dev/geoknight-openatlas`, Caddy, plans, or the live site.

## Tests

```bash
uv run pytest -q
```

`tests/test_globe.py` checks the export, the router, the disabled adapter, and the loopback server. `tests/test_metrics.py` still runs the warehouse metrics when `data/census.duckdb` is present. On the machine that produced this branch, `uv run pytest -q` reported 57 passed.

Screenshots from that local run are in `globe/catalog/`. The phone-width capture shows the county choropleth on the globe. A wide headless capture sometimes drew the globe limb before every county fill finished; the loopback page itself is the check. The catalog card images are orthographic drawings of the same county file and the same export.
