"""Probe: fetch web pages (press releases, SEC filings, OCC/exchange memos) from
Actions and print the text around a pattern - for checking spin-off ratios, deal
terms and dividend declarations when the page cannot be read from the sandbox.
Read-only. Usage: python probe_pages.py "URL URL ..." "REGEX" [CONTEXT_CHARS]
-> data/pages_probe.txt"""
import html, re, sys, time, urllib.request
urls = sys.argv[1].split()
pat = re.compile(sys.argv[2], re.I) if len(sys.argv) > 2 and sys.argv[2] else None
ctx = int(sys.argv[3]) if len(sys.argv) > 3 else 400
H = {"User-Agent": "sp500-data research sp500-data@users.noreply.github.com", "Accept-Encoding": "identity"}   # SEC EDGAR refuses requests without a contact in the User-Agent
out = []
def say(s=""): print(s, flush=True); out.append(str(s))
for u in urls:
    say(f"\n================ {u}")
    try:
        raw = urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=60).read()
        if raw[:4] == b"%PDF":
            say("PDF - text extraction not supported here"); continue
        s = raw.decode("utf-8", "replace")
    except Exception as e:
        say(f"ERROR {type(e).__name__} {str(e)[:150]}"); continue
    t = re.sub(r'(?is)<script.*?</script>|<style.*?</style>', ' ', s)
    t = re.sub(r'(?i)<(br|/p|/tr|/td|/h\d|/li|/div)[^>]*>', '\n', t)
    t = html.unescape(re.sub(r'<[^>]+>', ' ', t))
    t = re.sub(r'[ \t\r\xa0​]+', ' ', t); t = re.sub(r'\n\s*\n+', '\n', t)
    if not pat:
        say(t[:6000]); continue
    spans, last = [], -1
    for m in pat.finditer(t):
        a, b = max(0, m.start() - ctx), min(len(t), m.end() + ctx)
        if a <= last:
            spans[-1] = (spans[-1][0], b)
        else:
            spans.append((a, b))
        last = b
    say(f"{len(t):,} chars, {len(spans)} matching passages")
    for a, b in spans[:25]:
        say("... " + t[a:b].replace("\n", " | ") + " ...")
    time.sleep(0.5)
open("data/pages_probe.txt", "w").write("\n".join(out))
