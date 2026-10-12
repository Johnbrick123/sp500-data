"""Fetch one year's S&P Dow Jones Indices press releases about index changes from
press.spglobal.com and print their S&P 500 sentences (adds, removals, effective
dates) - the authority for membership dates. Read-only.

Usage: python fetch_releases.py YEAR  ->  data/releases_YEAR.txt (+ data/releases_YEAR/*.html)
The listing paginates 100 at a time (o=0, 100, 200...). Every release whose title
names the S&P 500 (or "Join"/"Set to Join"/"Replace", which covers the ones that
only name the incoming company) is kept. Years before 2012 come from PR Newswire's
S&P Indices account instead (press.spglobal.com's archive starts in 2012)."""
import glob, html, os, re, subprocess, sys, time, urllib.request
Y = sys.argv[1]
H = {"User-Agent": "Mozilla/5.0 (data verification; sp500-data)"}
OUT = f"data/releases_{Y}"; os.makedirs(OUT, exist_ok=True)


def get(u):
    for i in range(3):
        try:
            return urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=60).read().decode("utf-8", "replace")
        except Exception as e:
            err = e; time.sleep(3 * (i + 1))
    print(f"FAILED {u}: {type(err).__name__} {str(err)[:100]}"); return ""


links = {}
if int(Y) < 2012:
    # press.spglobal.com's archive starts in 2012. Earlier releases ("S&P Indices Announces Changes to
    # U.S. Indices", "Standard & Poor's Announces ...") are on PR Newswire under the S&P Indices account,
    # whose list pages carry a date beside each title: take every release of the year whose title is
    # about an index change (the list runs newest first, 100 a page).
    for page in range(1, 60):
        lst = get(f"https://www.prnewswire.com/news/s%26p-indices/?page={page}&pagesize=100")
        if not lst:
            break
        items = re.findall(r'(?s)<a[^>]+href="(/news-releases/[^"]+-\d+\.html)"[^>]*>(.*?)</a>', lst)
        dates = re.findall(r'(\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d\d, (\d{4}))', lst)
        if not items:
            break
        yrs = [y for _, y in dates]
        if yrs and max(yrs) < Y:
            break
        for href, title in items:
            title = re.sub(r'<[^>]+>', ' ', title)
            if re.search(r'Announces Change|Index Change|S&P 500|Join|Replace|Set to', title, re.I):
                links["https://www.prnewswire.com" + href] = title
        print(f"PRN page {page}: {len(items)} items, dates {min(yrs) if yrs else '-'}..{max(yrs) if yrs else '-'}, {len(links)} kept so far")
        if yrs and min(yrs) < Y:
            break
        time.sleep(1)
    keep = list(links)
    print(f"{len(keep)} index-change releases on PR Newswire around {Y} (the release date is in each page)")
    for u in keep:
        f = f"{OUT}/{u.rsplit('/', 1)[1][:120]}.html"
        if not os.path.exists(f):
            page_html = get(u); time.sleep(0.7)
            if page_html and (f", {Y}" in page_html[:200000] or f"{Y}, " in page_html[:20000]):
                open(f, "w").write(page_html)
    txt = subprocess.run([sys.executable, "tools/release_sentences.py", f"{OUT}/*.html"], capture_output=True, text=True).stdout
    open(f"data/releases_{Y}.txt", "w").write(f"{len(glob.glob(OUT + '/*.html'))} releases saved of {len(keep)} PR Newswire links around {Y}\n" + txt)
    print(txt[:3000]); sys.exit(0)
for o in range(0, 1000, 100):
    page = get(f"https://press.spglobal.com/index.php?s=2429&l=100&year={Y}&o={o}")
    found = re.findall(r'href="(https?://press\.spglobal\.com/' + Y + r'-\d\d-\d\d-[^"?#]+)"', page)
    found += ["https://press.spglobal.com/" + x for x in re.findall(r'href="/?(' + Y + r'-\d\d-\d\d-[^"?#]+)"', page)]
    new = [u for u in dict.fromkeys(found) if u not in links]
    for u in new:
        links[u] = None
    print(f"listing o={o}: {len(found)} links, {len(new)} new")
    if not new:
        break
    time.sleep(1)
keep = [u for u in links if re.search(r"S-P-500|Join|Replace|S-P-100|Announces-Change|U-S-Ind|Index-Change", u, re.I)]   # before mid-2012 the titles were the generic "S&P Indices Announces Change(s) to U.S. Index"
print(f"{len(links)} releases in {Y}, {len(keep)} about index changes")
for u in sorted(keep):
    f = f"{OUT}/{u.rsplit('/', 1)[1][:120]}.html"
    if not os.path.exists(f):
        open(f, "w").write(get(u)); time.sleep(0.7)
txt = subprocess.run([sys.executable, "tools/release_sentences.py", f"{OUT}/*.html"], capture_output=True, text=True).stdout
open(f"data/releases_{Y}.txt", "w").write(f"{len(keep)} releases of {len(links)} in {Y}\n" + txt)
print(txt[:3000])
