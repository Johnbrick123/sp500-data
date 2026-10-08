"""
Probe: Stooq fronts its CSV downloads with a JavaScript browser check.
Does a real (headless) Chromium pass it on a GitHub runner, and does Stooq
hold daily history for dead S&P 500 members? Read-only.
"""
import io, time, sys
import pandas as pd
from playwright.sync_api import sync_playwright
TEST = ["BMC", "LEH", "LEHMQ", "BSC", "CFC", "MOLX", "LIFE", "IGT", "BGEN", "EOP", "APCC", "CEPH", "AW", "CMVT", "WAMUQ", "ENRNQ", "AAPL"]
out = []
def say(s): print(s, flush=True); out.append(s)
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                        viewport={"width": 1280, "height": 900}, locale="en-US")
    page = ctx.new_page()
    page.goto("https://stooq.com/q/d/?s=bmc.us", wait_until="domcontentloaded", timeout=60000)
    time.sleep(6)
    say(f"quote page title: {page.title()!r}; url: {page.url}")
    body = page.inner_text("body")[:400].replace("\n", " ")
    say(f"body: {body!r}")
    for t in TEST:
        try:
            r = page.request.get(f"https://stooq.com/q/d/l/?s={t.lower()}.us&i=d", timeout=60000)
            txt = r.text()
            if txt.startswith("Date,"):
                df = pd.read_csv(io.StringIO(txt))
                say(f"  {t:7s} {len(df):6,} rows {df.Date.min()}..{df.Date.max()}  last close {df.Close.iloc[-1]}")
            else:
                say(f"  {t:7s} -> {txt[:120]!r}")
        except Exception as e:
            say(f"  {t:7s} error {type(e).__name__}: {str(e)[:100]}")
        time.sleep(2)
    b.close()
open("data/stooq_probe.txt", "w").write("\n".join(out))
