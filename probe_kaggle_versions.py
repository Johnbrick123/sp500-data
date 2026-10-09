"""Probe: can we download OLD VERSIONS of a daily-updated Kaggle dataset? A
version from early 2022 would hold IHS Markit (INFO) through its Feb 2022
delisting; versions at other dates would hold any later delisting too."""
import io, json, os, sys, zipfile, urllib.request
import pandas as pd
TOK = os.environ["KAGGLE_API_TOKEN"].strip()
H = {"Authorization": f"Bearer {TOK}", "User-Agent": "sp500-data probe"}
slug = sys.argv[1] if len(sys.argv) > 1 else "andrewmvd/sp-500-stocks"
out = []
def say(s): print(s, flush=True); out.append(str(s))
def get(url):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=120)
    return r.status, r.read(), r.headers
for u in [f"https://www.kaggle.com/api/v1/datasets/view/{slug}",
          f"https://www.kaggle.com/api/v1/datasets/list/{slug}/versions",
          f"https://www.kaggle.com/api/v1/datasets/metadata/{slug}"]:
    try:
        st, b, h = get(u); j = json.loads(b.decode())
        keys = list(j)[:25] if isinstance(j, dict) else f"list[{len(j)}]"
        say(f"{u.split('/v1/')[1]} -> {st} keys {keys}")
        if isinstance(j, dict):
            for k in ("currentVersionNumber", "lastUpdated", "totalBytes"):
                if k in j: say(f"   {k}: {str(j[k])[:300]}")
            if "versions" in j:
                vs = [(v.get("versionNumber"), str(v.get("creationDate", ""))[:10]) for v in j["versions"]]
                say(f"   versions listed: {len(vs)}; " + " ".join(f"{n}@{d}" for n, d in vs[:400]))
    except Exception as e:
        say(f"{u.split('/v1/')[1]} -> {type(e).__name__} {str(e)[:120]}")
# try a few explicit versions
for v in [int(x) for x in sys.argv[2:]] or [1, 50, 100]:
    u = f"https://www.kaggle.com/api/v1/datasets/download/{slug}?datasetVersionNumber={v}"
    try:
        st, b, h = get(u)
        z = zipfile.ZipFile(io.BytesIO(b)); names = z.namelist()
        info = f"version {v}: {len(b)/1e6:.1f} MB, files {names[:5]}"
        f = next((n for n in names if "stock" in n.lower() and n.endswith(".csv")), next((n for n in names if n.endswith(".csv")), None))
        if f:
            df = pd.read_csv(z.open(f), nrows=5); info += f" cols {list(df.columns)[:8]}"
            full = pd.read_csv(z.open(f), usecols=lambda c: c.lower() in ("date", "symbol", "ticker"))
            dc = [c for c in full.columns if c.lower() == "date"][0]; sc = [c for c in full.columns if c.lower() in ("symbol", "ticker")]
            info += f" dates {full[dc].min()}..{full[dc].max()}" + (f" tickers {full[sc[0]].nunique()} INFO? {'INFO' in set(full[sc[0]].astype(str))}" if sc else "")
        say(info)
    except Exception as e:
        say(f"version {v}: {type(e).__name__} {str(e)[:160]}")
os.makedirs("data", exist_ok=True); open("data/kaggle_versions_probe.txt", "w").write("\n".join(out))
