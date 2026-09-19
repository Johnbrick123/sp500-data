# Setup — about 5 minutes, all free

The database is a public GitHub repo. GitHub Actions refreshes the data every
weeknight and publishes it as a Release asset at a fixed URL. Anyone with the
URL — you, a colleague, Claude — queries it directly with DuckDB. No server,
no account needed to read, nothing to pay.

## 1. Create the repo
GitHub → New repository → name it (e.g. `sp500-data`) → **Public** → Create.

Public is what makes this work: reading needs no token, so Claude can query it
in any session and a colleague needs only the link. The data is Yahoo-derived
daily prices — nothing proprietary. (A private repo works too, but then every
reader needs a token, including Claude, every time.)

## 2. Upload the files
Unzip `sp500-dataset-pipeline.zip` and upload everything to the repo
(drag-and-drop on the GitHub website works; `.github/workflows/update.yml`
must keep its folder path).

## 3. Optional secrets  (Settings → Secrets and variables → Actions)
- `TIINGO_API_KEY` — free key from tiingo.com. Recovers the ~588 delisted
  companies Yahoo purged. Strongly recommended for backtesting.
- `MOTHERDUCK_TOKEN` — only if you also want a hosted SQL console.

## 4. Run it once
Actions tab → "Nightly market data update" → Run workflow. First run is the
full 20-minute backfill; every night after that takes a couple of minutes.
It then runs itself, weekdays at 6 pm Chicago.

## 5. Share it
The data URL is stable and public:

    https://github.com/<you>/<repo>/releases/download/data/prices.parquet

Send that to a colleague with `query_remote.py`, or the repo link. To give a
colleague edit rights: Settings → Collaborators.

Send the repo URL to Claude and it can query the live data in any conversation.
