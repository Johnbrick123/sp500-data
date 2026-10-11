# Checking tools (run locally against downloaded release assets)

Download the published build first (GitHub's release CLI is blocked in some sandboxes; plain `curl -sSL` works):

    mkdir -p /tmp/b && cd /tmp/b
    for a in prices.parquet membership_intervals.parquet wiki_changes.csv; do
      curl -sSL -o $a https://github.com/Johnbrick123/sp500-data/releases/download/data/$a; done

| Tool | What it answers |
|---|---|
| `daily_coverage.py DIR Y0 Y1` | Does every index member have a price on every trading day of each year? Lists the members with missing days (days within 7 of a name's last trade count as "carried": S&P held it after its deal closed). |
| `member_changes.py DIR YEAR` | Our membership changes in a year next to Wikipedia's change log (a lead only: S&P DJI's releases are the authority). |
| `bridge_test.py WORKDIR [Y0 Y1]` | Rebuilds membership from the upstream file with the repo's `bridge_membership.py` and prints each year's daily member count and every interval that differs from the published one. Run it before committing a membership change. |
| `bigmoves.py DIR YEAR [THRESH]` | Members' one-day total returns beyond a threshold (default 10%), with that row's dividend, split factor and source — finds unbooked spin-offs and bad bars. |
| `spin_inventory_2015_2026.csv` | Candidate spin-off / large-distribution days found from Yahoo factors and big dividends (`done` = converted in `spinoffs.csv`). Not complete: Biogen 2017 had no vendor record at all and was found by the Nasdaq audit. |
| `spin_dry.py DIR` | Runs the spin-off step of `compute_adjusted.py` for every parent in `spinoffs.csv` against raw files in `DIR/data/raw` and prints each outcome (applied / refused and why). |
| `div_scale_scan.py RAWDIR` | Yahoo dividends left on a different basis than the closes (AT&T before its 2022 spin-off). |
| `div_pattern_scan.py DIR Y0 Y1` | Dividend schedules with a skipped payment, an interleaved extra stream, or an amount far from its neighbours. |
| `release_sentences.py 'DIR/*.html'` | The S&P 500 sentences from saved S&P DJI press releases (adds, removals, effective dates). |

The year-by-year sign-off procedure and its results are in the project's verification log (`claude/data-verification-log.md`).
