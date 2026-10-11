"""
Step 1b: Bridge point-in-time membership from 2019-01-11 to today, and
normalize renamed tickers.

The free historical-constituents file stops at 2019-01-11. Wikipedia kept a
change log (added / removed / reason) until mid-2026, when it was dropped from
the live page. We pull it from a page revision that still has it, replay every
change forward from the 2019 snapshot, then diff against today's constituent
list to close the final gap.

Renames: when a company changes its symbol (FB -> META, FISV -> FI), Yahoo keeps
the FULL history under the NEW symbol and purges the old one. So an old symbol
that "has no data" is often a live company wearing a new name. We rewrite the
membership table to the current symbol, which recovers that history for free.

Outputs:
  data/universe/members.parquet     (overwritten, now 1996 -> today)
  data/universe/renames.csv         old_ticker -> new_ticker map
"""
import io, json, re, urllib.request
from pathlib import Path
import pandas as pd
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
U = ROOT / "data" / "universe"
H = {"User-Agent": "Mozilla/5.0 (research script)"}
PAGE = "List_of_S%26P_500_companies"
SNAP_END = pd.Timestamp("2019-01-11")


def wiki_changes():
    """Newest page revision that still has the #changes table."""
    for ts in ["2026-09-01", "2026-07-01", "2026-05-01", "2026-03-01",
               "2026-01-01", "2025-11-01", "2025-09-01", "2025-06-01"]:
        api = (f"https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
               f"&titles={PAGE}&rvlimit=1&rvdir=older&rvstart={ts}T00:00:00Z"
               f"&rvprop=ids&format=json")
        j = json.load(urllib.request.urlopen(
            urllib.request.Request(api, headers=H), timeout=30))
        rev = list(j["query"]["pages"].values())[0]["revisions"][0]["revid"]
        html = urllib.request.urlopen(urllib.request.Request(
            f"https://en.wikipedia.org/w/index.php?title={PAGE}&oldid={rev}",
            headers=H), timeout=60).read().decode()
        t = BeautifulSoup(html, "lxml").find("table", id="changes")
        if t is not None:
            df = pd.read_html(io.StringIO(str(t)))[0]
            df.columns = ["date", "added_ticker", "added_name",
                          "removed_ticker", "removed_name", "reason"]
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"]).sort_values("date")
            df.to_csv(U / "wiki_changes.csv", index=False)
            print(f"change log: {len(df)} rows through {df.date.max().date()} "
                  f"(page revision {rev})")
            return df
    raise RuntimeError("no revision with a changes table found")


def norm_name(s):
    s = str(s).lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    for w in ["inc", "corp", "corporation", "co", "company", "plc", "ltd",
              "holdings", "group", "the", "class", "a", "b", "c"]:
        s = re.sub(rf"\b{w}\b", " ", s)
    return " ".join(s.split())


# Curated: old symbol -> symbol Yahoo keeps the full history under.
# Built from the replay diff (stale vs unaccounted symbols) plus known
# corporate renames. Chains are collapsed to the terminal symbol.
KNOWN_RENAMES = {
    "ABC": "COR",    # AmerisourceBergen -> Cencora
    "ADS": "BFH",    # Alliance Data Systems -> Bread Financial (2022, same company); BFH carries ADS's history back to 2001
    "ARNC": "HWM",   # Alcoa Inc. -> Arconic Inc. (2016) -> Howmet Aerospace (2020-04-01), one legal company; pre-2016-11 history spliced in from the Kaggle archive (series_splices.csv)
    "ANTM": "ELV",   # Anthem -> Elevance
    "BBT": "TFC",    # BB&T -> Truist
    "BHI": "BKR", "BHGE": "BKR",   # Baker Hughes
    "BK": "BNY",     # Bank of New York Mellon
    "BLL": "BALL",   # Ball Corp
    "CBS": "SKYD", "VIAC": "SKYD", "PARA": "SKYD", "PSKY": "SKYD",   # CBS -> ViacomCBS -> Paramount -> Paramount Skydance (PSKY, then SKYD from Oct 2026)
    "CDAY": "DAY",   # Ceridian -> Dayforce
    "DISCA": "WBD",  # Discovery Series A -> Warner Bros. Discovery (2022-04-11); WBD's series is the Series A lineage. DISCK stays its own class.
    "GPS": "GAP",    # Gap Inc changed its ticker 2024-08-22
    "HFC": "DINO",   # HollyFrontier -> HF Sinclair (2022-03-14, one-for-one); DINO carries HollyFrontier's history back to 1992
    "COG": "CTRA",   # Cabot Oil & Gas -> Coterra
    "CTL": "LUMN",   # CenturyLink -> Lumen
    "EQR": "VMRK",   # Equity Residential -> Vivmark Residential (AvalonBay merger closed 2026-08-17; EQR is the surviving issuer)
    "FB": "META",
    "FLT": "CPAY",   # FleetCor -> Corpay
    "FBHS": "FBIN",  # Fortune Brands
    "HCP": "DOC", "PEAK": "DOC",   # HCP -> Healthpeak
    "HRS": "LHX",    # Harris -> L3Harris
    "JEC": "J",      # Jacobs
    "KFT": "MDLZ",   # Kraft -> Mondelez
    "LB": "BBWI",    # L Brands -> Bath & Body Works
    "MMC": "MRSH",   # Marsh McLennan
    "MYL": "VTRS",   # Mylan -> Viatris
    "PKI": "RVTY",   # PerkinElmer -> Revvity
    "PX": "LIN",     # Praxair -> Linde
    "RE": "EG",      # Everest Re
    "SATS": "ECHO",  # EchoStar
    "SYMC": "GEN", "NLOK": "GEN",  # Symantec -> NortonLifeLock -> Gen Digital
    "TMK": "GL",     # Torchmark -> Globe Life
    "UTX": "RTX",
    "WLTW": "WTW",
    # NOT "WRK": "SW". Smurfit Kappa acquired WestRock (2024-07-05); SW's history
    # before then is Smurfit Kappa's, not WestRock's (a 2021 second-source check
    # found 85% of days differing). WestRock keeps its own label and series and
    # hands its index slot to SW on 2024-07-08 - see MANUAL_CHANGES.
    "DWDP": "DD",    # DowDuPont -> DuPont
    # Same company listed twice by the upstream snapshot file from 2016-01-04
    # (its old and new symbol side by side), which made 507 members instead of
    # 505 for most of 2016-2018. One label each; the series are identical.
    "KORS": "CPRI",       # Michael Kors -> Capri Holdings (ticker change 2018-12-31); both listed 2016-01-04..2018-09-18
    "PX-201810": "LIN",   # Praxair -> Linde plc (1:1, 2018-10-31); PX and PX-201810 both listed 2016-01-04..2018-09-17
    "CCEP": "CCE",        # Coca-Cola Enterprises listed twice 2016-01-04..2016-05-31 (under CCE and under CCEP, Yahoo's
                          #   continuation symbol; identical prices). Coca-Cola European Partners itself was NEVER an S&P 500
                          #   member: S&P DJI's 2016-05-24 release removes CCE and calls CCEP Spain-domiciled and ineligible.
}


# Removals the upstream snapshot file dates at the deal's close. S&P removes a
# name on the effective date in its announcement and holds it until then (at
# its last price once it stops trading), so the name stays a member through
# the day before that date. (label, effective removal date) - S&P DJI releases.
HOLD_UNTIL = [
    ("CFN-201503",  "2015-03-18"),   # Henry Schein replaced CareFusion after the close of Tue 3/17 (S&P DJI 2015-03-13); Becton Dickinson closed 3/17, CFN's last trade
    ("AGN-201503",  "2015-03-23"),   # AAL/EQIX/HBI/SLG replaced Allergan, Avon, Denbury and Nabors after the close of Fri 3/20 (S&P DJI 2015-03-13 and -16); Actavis closed 3/17, old Allergan's last trade 3/16
    ("TEG-201506",  "2015-07-01"),   # J.B. Hunt and Baxalta joined after the close of Tue 6/30, Integrys and QEP out (S&P DJI 2015-06-19 and -26); WEC closed 6/29, TEG's last trade
    ("FDO-201507",  "2015-07-09"),   # Advance Auto Parts replaced Family Dollar after the close of Wed 7/8 (S&P DJI 2015-07-06); Dollar Tree closed 7/6, FDO's last trade
    ("DTV-201507",  "2015-07-29"),   # Signet replaced DIRECTV after the close of Tue 7/28 (S&P DJI 2015-07-24); AT&T closed 7/24, DTV's last trade
    ("HCBK-201510", "2015-11-03"),   # Hudson City left after the close of Mon 11/2 while its replacement HPE joined at the HP separation after the 10/30 close - both lines were members on 11/2 (S&P DJI 2015-10-27); M&T's merger was effective 11/1, HCBK last traded 10/30
    ("SIAL-201511", "2015-11-19"),   # Illumina replaced Sigma-Aldrich after the close of Wed 11/18 (S&P DJI 2015-11-12); Merck KGaA closed 11/18, SIAL's last trade
    ("ALTR-201512", "2015-12-29"),   # Church & Dwight replaced Altera after the close of Mon 12/28 (S&P DJI 2015-12-22); Intel closed 12/28, ALTR's last trade
    ("CB-201601",   "2016-01-19"),   # Extra Space replaced Chubb Corp after the close of Fri 1/15 = before the 1/19 open, MLK between (S&P DJI 2016-01-13); ACE closed the deal 1/14, CB's last trade
    ("GMCR-201603", "2016-03-07"),   # UDR replaced Keurig Green Mountain after the close of Fri 3/4 (S&P DJI 2016-03-03); JAB closed 3/3, GMCR last traded 3/2
    ("POM-201603",  "2016-03-30"),   # Hologic and Centene replaced Pepco and Ensco after the close of Tue 3/29 (S&P DJI 2016-03-24); Exelon closed 3/23, POM's last trade
    ("ADT-201604",  "2016-05-03"),   # Acuity Brands replaced The ADT Corp after the close of Mon 5/2 (S&P DJI 2016-04-26); Apollo closed 5/2, ADT last traded 4/29
    ("SNDK-201605", "2016-05-13"),   # Alaska Air replaced SanDisk after the close of Thu 5/12 (S&P DJI 2016-05-10); Western Digital closed 5/12, SNDK last traded 5/11
    ("CVC-201606",  "2016-06-24"),   # Fortune Brands replaced Cablevision after the close of Thu 6/23 (S&P DJI 2016-06-21); Altice closed 6/21, CVC last traded 6/20
    ("CPGX-201606", "2016-07-06"),   # Columbia Pipeline left after the close of Tue 7/5, its replacement Fortive having joined 7/5 at the spin-off - both members on 7/5 (S&P DJI 2016-06-23); TransCanada closed 7/1, CPGX's last trade 6/30
    ("EMC-201609",  "2016-09-08"),   # Charter replaced EMC after the close of Wed 9/7 (S&P DJI 2016-08-31); Dell closed 9/7, EMC last traded 9/6
    ("SE-201702",   "2017-02-28"),   # Incyte replaced Spectra Energy before the 2/28 open (S&P DJI 2017-02-23); Enbridge closed 2/27, SE last traded 2/24
    ("HAR-201703",  "2017-03-16"),   # Synopsys replaced Harman before the 3/16 open (S&P DJI 2017-03-13); Samsung closed 3/10
    ("MJN-201706",  "2017-06-19"),   # Everest Re replaced Mead Johnson before the 6/19 open (S&P DJI 2017-06-12); Reckitt closed 6/15, MJN last traded 6/14
    ("RAI-201707",  "2017-07-26"),   # MGM Resorts replaced Reynolds American before the 7/26 open (S&P DJI 2017-07-19); BAT closed before the 7/25 open
    ("WFM-201708",  "2017-08-29"),   # Quintiles IMS replaced Whole Foods before the 8/29 open (S&P DJI 2017-08-24); Amazon closed 8/28, WFM last traded 8/25
    ("SPLS-201709", "2017-09-18"),   # Cadence replaced Staples before the 9/18 open (S&P DJI 2017-09-08); Sycamore closed 9/12
    ("BCR-201712",  "2018-01-03"),   # Huntington Ingalls replaced C.R. Bard before the 2018-01-03 open (S&P DJI 2017-12-28); Becton Dickinson closed 12/29
    ("TWX-201806",  "2018-06-20"),   # FleetCor replaced Time Warner before the 6/20 open (S&P DJI 2018-06-15); AT&T closed 6/14
    ("XL-201809",   "2018-09-17"),   # WellCare replaced XL Group before the 9/17 open (S&P DJI 2018-09-11); AXA closed 9/12
    ("COL-201811",  "2018-12-03"),   # Lamb Weston replaced Rockwell Collins before the 12/3 open (S&P DJI 2018-11-26); UTC closed 11/26
    ("AET-201811",  "2018-12-03"),   # Maxim replaced Aetna before the 12/3 open (same release); CVS closed 11/28
    ("ESRX-201812", "2018-12-24"),   # Celanese replaced Express Scripts before the 12/24 open (S&P DJI 2018-12-19); Cigna closed 12/20
]


# Memberships the upstream snapshot file runs LONGER than S&P's own release:
# snapshot rows for the label on or after the date are dropped, so the
# interval ends exactly there. (label, first non-member date) - S&P DJI releases.
END_EARLY = [
    ("FCPT", "2015-11-11"),   # Four Corners (Darden's spin) joined the S&P 500 after the 11/9 close and moved to the SmallCap 600 after the close of Tue 11/10 - one member day (S&P DJI 2015-11-02). The upstream file carries it through 11/16.
]


def end_early(all_m, label, end):
    """The snapshots are sparse (change dates), so ending a membership early
    means inserting a snapshot AT `end` that carries the prior composition
    without the label, then dropping the label's own rows from `end` on."""
    end = pd.Timestamp(end)
    dates = sorted(all_m.date.unique())
    if end not in set(dates):
        prev = max(d for d in dates if d < end)
        snap = all_m[(all_m.date == prev) & (all_m.ticker != label)].assign(date=end)
        all_m = pd.concat([all_m, snap], ignore_index=True)
    m = (all_m.ticker == label) & (all_m.date >= end)
    print(f"  end {label} before {end.date()}: -{int(m.sum())} snapshot dates"
          + ("" if end in set(dates) else f", snapshot inserted at {end.date()}"))
    return all_m[~m]


def hold_until(all_m, label, end):
    """Keep `label` a member on every snapshot date from its last listing up to
    (not including) `end`, and make `end` a snapshot date without it, so its
    membership interval ends exactly there."""
    end = pd.Timestamp(end)
    have = all_m.loc[all_m.ticker == label, "date"]
    if have.empty or have.max() >= end:
        print(f"  hold {label} until {end.date()}: nothing to do"); return all_m
    dates = sorted(all_m.date.unique())
    if end not in set(dates):                                 # a new snapshot = the composition just before it
        prev = max(d for d in dates if d < end)
        snap = all_m[(all_m.date == prev) & (all_m.ticker != label)].assign(date=end)
        all_m = pd.concat([all_m, snap], ignore_index=True)
        dates = sorted(all_m.date.unique())
    gap = [d for d in dates if have.max() < d < end]
    all_m = pd.concat([all_m, pd.DataFrame({"date": gap, "ticker": label})], ignore_index=True)
    print(f"  hold {label} until {end.date()}: +{len(gap)} snapshot dates")
    return all_m


# Tickers REUSED by a different company after the original delisted. The
# original's membership rows (before the cutoff) get a dated label so its
# recovered prices (fetch_perma.py) never collide with today's owner.
#   bare ticker: (dated label, first date that belongs to the NEW owner)
RELABEL = {   # bare ticker: list of (dated label, first date, first date of the NEXT owner)
    "S":    [("S-200503",   "1900-01-01", "2005-08-01"),    # Sears Roebuck (merged into Kmart 2005)
             ("S-202004",   "2005-08-01", "2020-05-01")],   # Sprint Nextel / Sprint -> SentinelOne
    "NFX":  [("NFX-201902", "1900-01-01", "2019-03-01")],   # Newfield Exploration -> now an ETF
    "STI":  [("STI-201912", "1900-01-01", "2020-01-01")],   # SunTrust -> reused 2024
    "APC":  [("APC-201908", "1900-01-01", "2019-09-01")],   # Anadarko -> reused 2026
    "INFO": [("INFO-202203","1900-01-01", "2022-03-01")],   # IHS Markit -> reused 2024
    "IR":   [("TT",         "1900-01-01", "2020-03-02")],   # Ingersoll-Rand plc renamed Trane Technologies; a NEW Ingersoll Rand Inc took IR on 2020-03-02
    "FOXA": [("FOXA-201903","1900-01-01", "2019-03-19")],   # (old News Corp ->) 21st Century Fox Class A; Fox Corp took FOXA on 2019-03-19
    "FOX":  [("FOX-201903", "1900-01-01", "2019-03-19")],   # 21st Century Fox Class B; Fox Corp took FOX on 2019-03-19
    "BBBY": [("BBBY-202305","1900-01-01", "2023-10-01")],   # Bed Bath & Beyond Inc. (S&P 500 1999-10-01..2017-07-26; Nasdaq delisting 2023-05, shares
                                                            #   cancelled 2023-09); the symbol was reused later - Yahoo's BBBY series starts 2026-07-17 and is another company
    "NE":   [("NE-202102",  "1900-01-01", "2021-06-01")],   # Noble Corporation, the offshore driller (S&P 500 2001-2009 and 2011-2015; removed 2009 on the
                                                            #   Swiss redomicile policy like ACE and Tyco, re-added 2011; its shares were cancelled in the
                                                            #   Feb 2021 Chapter 11 emergence). Yahoo's NE series starts 2021-06 with the re-listed
                                                            #   successor's new shares - a different security, never an index member. WIKI's NE is the
                                                            #   old company (Jan 2015 closes 15-17, = the Aug-2018 archive).
}


# Index changes after the last Wikipedia-logged change, at their EFFECTIVE dates.
# (date, added, removed). Source: S&P DJI announcements.
MANUAL_CHANGES = [   # (effective date, added, removed) from S&P DJI announcements: changes after the Wikipedia log ends,
                     # and spin-offs the log leaves out. Since Oct 2015 S&P adds every spin-off to its parent's index at a
                     # zero price after the close before the ex-date; one that does not qualify stays until S&P moves it.
    ("2019-03-19", "FOXA-201903", ""),   # 21st Century Fox stays in the S&P 500 one day alongside Fox Corp (added before the 3/19 open);
    ("2019-03-19", "FOX-201903", ""),    #   S&P removed 21CF before the 3/20 open, after Disney's deal closed (S&P DJI release 2019-03-14)
    ("2019-03-20", "", "FOXA-201903"),
    ("2019-03-20", "", "FOX-201903"),
    ("2023-07-03", "FTRE", ""),          # Fortrea (spun off by Labcorp) - S&P 500 on 7/3 and 7/5, then the SmallCap 600
    ("2023-07-06", "", "FTRE"),          #   before the 2023-07-06 open (S&P DJI release 2023-06-28)
    ("2020-04-01", "ARNC-202308", ""),   # Arconic Corp (spun off by Arconic Inc./Howmet) - S&P 500 until it moved to the SmallCap 600
    ("2020-04-06", "", "ARNC-202308"),   #   before the 2020-04-06 open (S&P DJI release 2020-04-01)
    ("2020-12-15", "AIRC", ""),          # Apartment Income REIT (spun off by Aimco) - S&P 500 until it moved to the MidCap 400
    ("2020-12-21", "", "AIRC"),          #   before the 2020-12-21 open, when Tesla replaced AIV (S&P DJI release 2020-12-11)
    ("2024-07-08", "SW",   "WRK"),    # Smurfit WestRock takes WestRock's slot (S&P: WestRock is the surviving entity for index purposes; merger closed 2024-07-05, SW first traded 2024-07-08)
    ("2026-08-05", "FERG", "EA"),     # Ferguson replaces Electronic Arts
    ("2026-08-18", "RDDT", "AVB"),    # Reddit replaces AvalonBay (merged into Equity Residential -> Vivmark, VMRK)
    ("2026-09-21", "BE",   "TAP"),    # quarterly rebalance: Bloom Energy, Everpure, Illumina replace Molson Coors, Trade Desk, Builders FirstSource
    ("2026-09-21", "P",    "TTD"),
    ("2026-09-21", "ILMN", "BLDR"),
    ("2026-10-01", "VYLR", ""),       # Vylor added on its spin-off from Corteva
    ("2026-10-06", "",     "CTVA"),   # Corteva removed (Vylor replaces it)
    ("2026-10-06", "TWLO", "WBD"),    # Twilio replaces Warner Bros. Discovery (acquired by Paramount Skydance)
]


def norm(t):
    """One symbol convention everywhere: dots -> hyphens (BRK.B -> BRK-B)."""
    return str(t).strip().replace(".", "-")


def build_intervals(snapshots):
    """Snapshots (date, ticker) -> [start, end) membership intervals.
    The end of a span is the next SNAPSHOT date, not the ticker's next
    appearance - a removed name must stop being a member on the day the
    next snapshot no longer lists it. end is NULL while still a member."""
    dates = sorted(snapshots.date.unique())
    nxt = {d: (dates[i + 1] if i + 1 < len(dates) else pd.NaT) for i, d in enumerate(dates)}
    rows = []
    for t, g in snapshots.groupby("ticker"):
        ds = sorted(g.date.unique())
        start, prev = ds[0], ds[0]
        for d in ds[1:]:
            if d != nxt[prev]:                    # gap: the name left and came back
                rows.append((t, start, nxt[prev])); start = d
            prev = d
        rows.append((t, start, nxt[prev]))
    iv = pd.DataFrame(rows, columns=["ticker", "start", "end"])
    iv["end"] = pd.to_datetime(iv["end"])
    return iv.sort_values(["ticker", "start"]).reset_index(drop=True)


def main():
    members = pd.read_parquet(U / "members.parquet")
    members["date"] = pd.to_datetime(members["date"])
    members = members[members.date <= SNAP_END]
    ch = wiki_changes()
    curr = pd.read_csv(U / "current_constituents.csv")
    current = set(curr["Symbol"].astype(str).str.strip())   # normalized below

    # --- replay changes forward from the 2019 snapshot ---
    members["ticker"] = members["ticker"].map(norm).map(lambda t: KNOWN_RENAMES.get(t, t))
    for c in ["added_ticker", "removed_ticker"]:
        ch[c] = ch[c].astype(str).map(norm).map(lambda t: KNOWN_RENAMES.get(t, t))
    current = {norm(t) for t in current}
    # manual post-log changes become ordinary change rows at their effective dates
    extra = pd.DataFrame([{"date": pd.Timestamp(d), "added_ticker": a, "added_name": "",
                           "removed_ticker": r, "removed_name": "", "reason": "manual"}
                          for d, a, r in MANUAL_CHANGES])
    ch = pd.concat([ch, extra], ignore_index=True).sort_values("date")
    held = set(members[members.date == members.date.max()].ticker)
    print(f"2019-01-11 snapshot: {len(held)} names")
    rows = []
    for d, g in ch[ch.date > SNAP_END].groupby("date"):
        for _, r in g.iterrows():
            rem = str(r.removed_ticker).strip()
            add = str(r.added_ticker).strip()
            if rem and rem != "nan":
                held.discard(rem)
            if add and add != "nan":
                held.add(add)
        rows.extend((d, t) for t in sorted(held))
    replayed = pd.DataFrame(rows, columns=["date", "ticker"])
    print(f"replayed through {replayed.date.max().date()}: {len(held)} names")

    # --- close the final gap vs today's list ---
    missed_add = current - held
    missed_rem = held - current
    print(f"vs today: +{len(missed_add)} not in replay {sorted(missed_add)}, "
          f"-{len(missed_rem)} in replay but not current {sorted(missed_rem)}")
    today = pd.Timestamp.today().normalize()
    rows = [(today, t) for t in sorted(current)]
    final = pd.DataFrame(rows, columns=["date", "ticker"])

    # --- rename map: same company, new symbol ---
    # Source 1: change-log rows where the added and removed company match.
    renames = {}
    for _, r in ch.iterrows():
        a, b = str(r.added_ticker).strip(), str(r.removed_ticker).strip()
        if a in ("", "nan") or b in ("", "nan") or a == b:
            continue
        an, rn = norm_name(r.added_name), norm_name(r.removed_name)
        if not an or not rn or str(r.reason) == "manual":
            continue                              # no names -> no rename inference
        same = an == rn
        if same:
            renames[b] = a
    renames.update(KNOWN_RENAMES)
    # Source 2: dead tickers whose last snapshot name matches a current name.
    # (Only possible where we have names; the 2019 file has tickers only.)
    # Chase chains (A->B->C) to the terminal symbol.
    def terminal(t, seen=()):
        n = renames.get(t)
        return t if n is None or n in seen else terminal(n, seen + (t,))
    renames = {k: terminal(k) for k in renames}
    rn = pd.DataFrame(sorted(renames.items()), columns=["old", "new"])
    rn.to_csv(U / "renames.csv", index=False)
    print(f"renames detected: {len(rn)}")

    all_m = pd.concat([members, replayed, final], ignore_index=True)
    all_m["ticker"] = all_m["ticker"].map(lambda t: renames.get(t, t))
    # data/relabels.csv: entries fetch_perma.py adds as it recovers dead companies
    relabel = {k: list(v) for k, v in RELABEL.items()}
    rl = ROOT / "data" / "relabels.csv"
    if rl.exists():
        for r in pd.read_csv(rl).itertuples():
            relabel.setdefault(r.bare, []).append((r.label, str(r.start), str(r.cutoff)))
    for bare, spans in relabel.items():
        for lab, start, cutoff in spans:
            rows = (all_m.ticker == bare) & (all_m.date >= pd.Timestamp(start)) & (all_m.date < pd.Timestamp(cutoff))
            all_m.loc[rows, "ticker"] = lab
    # The upstream file switches some delisted names to a 'TICKER-YYYYMM' label
    # partway through (e.g. AET until 2018-09, AET-201811 from 2016-01). Merge
    # the bare label into the suffixed one when the bare label never appears
    # after the delisting month and is not a current member; the suffixed label
    # is the canonical id for that security.
    suffixed = {t: t.rsplit("-", 1)[1] for t in set(all_m.ticker) if re.search(r"-\d{6}$", t)}
    last_seen = all_m.groupby("ticker").date.max()
    merge = {}
    for sfx, ym in suffixed.items():
        bare = sfx.rsplit("-", 1)[0]
        if bare in last_seen.index and bare not in current:
            if last_seen[bare] <= pd.Timestamp(ym[:4] + "-" + ym[4:] + "-01") + pd.Timedelta(days=60):
                merge[bare] = sfx
    all_m["ticker"] = all_m["ticker"].map(lambda t: merge.get(t, t))
    print(f"merged {len(merge)} bare labels into their delisted-suffix ids")
    for lab, end in HOLD_UNTIL:
        all_m = hold_until(all_m, lab, end)
    for lab, end in END_EARLY:
        all_m = end_early(all_m, lab, end)
    # A current member that the snapshots or the replay lost without any change-log
    # removal (Linde after Praxair's rename, Trane after the IR relabel) is bridged:
    # it is held continuously from its last appearance through today.
    today_ = all_m.date.max()
    cur_set = set(all_m[all_m.date == today_].ticker)
    all_dates = sorted(all_m.date.unique())
    removed_on = {}
    for _, r in ch.iterrows():
        t = str(r.removed_ticker).strip()
        if t and t != "nan":
            removed_on.setdefault(t, []).append(r.date)
    bridged = []
    for t in sorted(cur_set):
        seen = sorted(all_m[all_m.ticker == t].date.unique())
        if len(seen) < 2:
            continue
        last_before_today = max(d for d in seen if d < today_) if any(d < today_ for d in seen) else None
        if last_before_today is None:
            continue
        gap = [d for d in all_dates if last_before_today < d < today_]
        if not gap:
            continue
        if any(last_before_today < pd.Timestamp(x) <= today_ for x in removed_on.get(t, [])):
            continue                                   # a real removal explains the gap
        all_m = pd.concat([all_m, pd.DataFrame({"date": gap, "ticker": t})], ignore_index=True)
        bridged.append(f"{t} {pd.Timestamp(last_before_today).date()}->today")
    print(f"bridged {len(bridged)} current members across unlogged gaps: {bridged}")
    raw = ROOT / "data" / "raw"                       # move any price file saved under the bare label
    for bare, sfx in merge.items():
        if (raw / f"{bare}.parquet").exists():
            if (raw / f"{sfx}.parquet").exists():
                (raw / f"{bare}.parquet").unlink()          # same company twice: keep the canonical id
            else:
                (raw / f"{bare}.parquet").rename(raw / f"{sfx}.parquet")
    all_m = all_m.drop_duplicates().sort_values(["date", "ticker"])
    all_m.to_parquet(U / "members.parquet", index=False)
    iv = build_intervals(all_m)
    iv.to_parquet(U / "membership_intervals.parquet", index=False)
    today_members = iv[iv.end.isna()].ticker.nunique()
    print(f"intervals: {len(iv)} spans, {today_members} current members "
          f"(current list has {len(current)})")

    ever = sorted(set(all_m.ticker))
    etfs = [t for t in (U / "tickers.txt").read_text().split("\n")
            if t.strip() and t not in ever]
    (U / "tickers.txt").write_text("\n".join(sorted(set(ever) | set(etfs))))
    print(f"membership now {all_m.date.min().date()} -> "
          f"{all_m.date.max().date()}, {len(ever)} distinct tickers")


if __name__ == "__main__":
    main()
