"""Run the adjust pipeline only for the spin-off parents, print SPUN outcomes."""
import sys, importlib.util
from pathlib import Path
import pandas as pd
d = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("ca", d / "compute_adjusted.py")
ca = importlib.util.module_from_spec(spec); spec.loader.exec_module(ca)
tks = sorted(set(ca.SPINS.ticker))
for tk in tks:
    f = ca.RAW / f"{tk}.parquet"
    if not f.exists():
        print("MISSING raw", tk); continue
    df = pd.read_parquet(f)
    out = ca.adjust_one(ca.trim_filler(ca.apply_patches(ca.drop_placeholders(ca.apply_splice(df, tk), tk), tk), tk))
    src = df["source"].astype(str).mode().iloc[0] if "source" in df else "?"
    for x in [s for s in ca.SPUN if s[0] == tk]:
        pass
print("source by parent:")
for tk in tks:
    f = ca.RAW / f"{tk}.parquet"
    if f.exists():
        df = pd.read_parquet(f, columns=["source"]) if "source" in pd.read_parquet(f).columns else None
        print(" ", tk, None if df is None else df.source.astype(str).value_counts().to_dict())
print("SPUN:")
for x in ca.SPUN:
    print("  ", x)
ok = sum(1 for x in ca.SPUN if str(x[-1]).startswith("applied"))
print(f"applied {ok} / {len(ca.SPUN)}  (events in table: {ca.SPINS.groupby(['ticker','ex_date']).ngroups})")
