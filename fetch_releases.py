"""Fetch one year's S&P Dow Jones Indices press releases about index changes from
press.spglobal.com and print their S&P 500 sentences (adds, removals, effective
dates) - the authority for membership dates. Read-only.

Usage: python fetch_releases.py YEAR  ->  data/releases_YEAR.txt (+ data/releases_YEAR/*.html)
The listing paginates 100 at a time (o=0, 100, 200...). Every release whose title
names the S&P 500 (or "Join"/"Set to Join"/"Replace", which covers the ones that
only name the incoming company) is kept."""
import html, os, re, subprocess, sys, time, urllib.request
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
keep = [u for u in links if re.search(r"S-P-500|Join|Replace|S-P-100", u, re.I)]
print(f"{len(links)} releases in {Y}, {len(keep)} about index changes")
for u in sorted(keep):
    f = f"{OUT}/{u.rsplit('/', 1)[1][:120]}.html"
    if not os.path.exists(f):
        open(f, "w").write(get(u)); time.sleep(0.7)
txt = subprocess.run([sys.executable, "tools/release_sentences.py", f"{OUT}/*.html"], capture_output=True, text=True).stdout
open(f"data/releases_{Y}.txt", "w").write(f"{len(keep)} releases of {len(links)} in {Y}\n" + txt)
print(txt[:3000])
