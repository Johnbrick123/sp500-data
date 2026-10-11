"""Print the S&P 500 sentences (adds, removals, effective dates) from saved S&P DJI
press-release HTML files. Usage: release_sentences.py 'DIR/*.html'"""
import re, html, sys, glob, os
for f in sorted(glob.glob(sys.argv[1])):
    s = open(f, encoding="utf-8", errors="replace").read()
    t = re.sub(r'(?is)<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>', '', s)
    t = re.sub(r'(?i)<(br|/p|/tr|/h\d|/li|/div)[^>]*>', '\n', t)
    t = re.sub(r'<[^>]+>', '', t); t = html.unescape(t)
    t = re.sub(r'[ \t\r\xa0​]+', ' ', t)
    i = t.find("NEW YORK"); t = t[i:] if i >= 0 else t
    j = t.find("Following is a summary"); t = t[:j] if j > 0 else t[:6000]
    sents = re.split(r'(?<=[.;])\s+(?=[A-Z])', t.replace("\n", " "))
    keep = [x.strip() for x in sents if ("S&P 500" in x and "S&P 500 Index is" not in x) or "effective prior" in x or "completed" in x or "expected" in x]
    print("===", os.path.basename(f)[:60])
    for x in keep[:8]:
        print("   ", x[:420])
