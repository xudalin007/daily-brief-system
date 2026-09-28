#!/usr/bin/env python3
"""Fetch macro quotes from CNBC (GFW-direct). Outputs key=value lines."""
import re, subprocess, json, sys

QUOTES = {
    "brent": "@LCO.1",
    "wti": "@CL.1",
    "us10y": "US10Y",
    "us2y": "US2Y",
    "us30y": "US30Y",
    "spx": ".SPX",
    "ixic": ".IXIC",
    "dji": ".DJI",
    "dxy": "DXY",
    "vix": ".VIX",
    "gold": "@GC.1",
    "silver": "@SI.1",
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

out = {}
for name, code in QUOTES.items():
    url = f"https://www.cnbc.com/quotes/{code}"
    try:
        r = subprocess.run(["curl", "-s", "-A", UA, "--max-time", "20", url],
                           capture_output=True, text=True, timeout=30)
        html = r.stdout
        m = re.search(r'class="QuoteStrip-lastPrice">([^<]*)', html)
        last = m.group(1).strip() if m else None
        m2 = re.search(r'class="QuoteStrip-lastPriceStrip">(.*?)</div>', html, re.S)
        chg = None
        if m2:
            txt = re.sub(r"<[^>]+>", " ", m2.group(1))
            txt = re.sub(r"\s+", " ", txt).strip()
            chg = txt
        out[name] = {"last": last, "change": chg}
    except Exception as e:
        out[name] = {"last": None, "change": None, "error": str(e)}

print(json.dumps(out, ensure_ascii=False, indent=2))
