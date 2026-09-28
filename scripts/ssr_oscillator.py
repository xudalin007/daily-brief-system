#!/usr/bin/env python3
"""Calculate SSR Oscillator from free data sources (GFW-compatible).
SSR Oscillator = (SSR - 200D_MA_of_SSR) / 200D_std_of_SSR

Data sources:
- Stablecoin MC: DeFiLlama stablecoins API (GFW-accessible)
- BTC Market Cap: blockchain.info API (GFW-accessible)

Usage: python3 ssr_oscillator.py
Output: JSON with current SSR, SSR Oscillator, and recent values
"""

import json
import urllib.request
import sys
from datetime import datetime, timezone
import math

def fetch_json(url, timeout=15):
    """Fetch JSON from URL with timeout"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())

def get_btc_mc_history():
    """Get BTC market cap history from blockchain.info (returns ~200 days)"""
    data = fetch_json("https://api.blockchain.info/charts/market-cap?timespan=200days&format=json")
    # Returns {"values": [{"x": timestamp, "y": market_cap_usd}, ...]}
    # Round timestamps to day boundary for alignment
    result = {}
    for v in data["values"]:
        day = int(v["x"]) - (int(v["x"]) % 86400)
        result[day] = v["y"]  # Last value of each day wins
    return result

def get_stablecoin_mc_history():
    """Get total stablecoin (USDT+USDC+DAI) MC history from DeFiLlama"""
    data = fetch_json("https://stablecoins.llama.fi/stablecoincharts/all?stablecoin=1")
    # Returns [{date: "timestamp", totalCirculating: {peggedUSD: value}}, ...]
    # Note: this is TOTAL of ALL stablecoins, not just USDT+USDC+DAI
    # That's actually fine for SSR - broader stablecoin measure is more conservative
    return {int(d["date"]): d["totalCirculating"]["peggedUSD"] for d in data}

def calculate_ssr(btc_mc, stable_mc):
    """SSR = BTC MC / Stablecoin MC (标准 Glassnode 口径)"""
    return btc_mc / stable_mc if stable_mc > 0 else 0

def ssr_oscillator(current_ssr, ssr_history):
    """
    SSR Oscillator = (SSR - SMA_200) / std_200
    Where SMA_200 and std_200 are over the last 200 days
    """
    if len(ssr_history) < 200:
        # Not enough history
        n = len(ssr_history)
        sma = sum(ssr_history) / n
        variance = sum((x - sma) ** 2 for x in ssr_history) / n
        std = math.sqrt(variance) if variance > 0 else 1e-10
        return (current_ssr - sma) / std, n, sma, std

    recent = ssr_history[-200:]
    sma = sum(recent) / 200
    variance = sum((x - sma) ** 2 for x in recent) / 200
    std = math.sqrt(variance) if variance > 0 else 1e-10
    return (current_ssr - sma) / std, 200, sma, std

def main():
    print("Fetching data...", file=sys.stderr)

    # BTC current: use blockchain.com API for consistency with historical data
    btc_hist_data = fetch_json("https://api.blockchain.info/charts/market-cap?timespan=1days&format=json")
    btc_mc_current = btc_hist_data["values"][-1]["y"]

    # Also get BTC price from 9992100.xyz for reference
    btc_data = fetch_json("https://9992100.xyz/api/ahr999")
    btc_price = btc_data["price_usd"]

    # Current stablecoin MC (ALL USD-pegged, matching DeFiLlama charts endpoint)
    stable_data = fetch_json("https://stablecoins.llama.fi/stablecoins?includePrices=false")
    stable_mc_current = sum(
        p.get("circulating", {}).get("peggedUSD", 0)
        for p in stable_data["peggedAssets"]
    )

    # Current SSR
    current_ssr = calculate_ssr(btc_mc_current, stable_mc_current)

    print(f"BTC MC: ${btc_mc_current/1e12:.2f}T", file=sys.stderr)
    print(f"Stablecoin MC: ${stable_mc_current/1e9:.1f}B", file=sys.stderr)
    print(f"Current SSR: {current_ssr:.4f}", file=sys.stderr)

    # Get histories and calculate SSR Oscillator
    print("Fetching histories...", file=sys.stderr)
    btc_mc_hist = get_btc_mc_history()
    stable_mc_hist = get_stablecoin_mc_history()

    # Align dates and calculate SSR history
    ssr_values = []
    for ts in sorted(btc_mc_hist.keys()):
        # Find nearest stablecoin MC (within same day)
        day_start = ts - (ts % 86400)  # Round to day boundary
        if day_start in stable_mc_hist:
            ssr = calculate_ssr(btc_mc_hist[ts], stable_mc_hist[day_start])
            ssr_values.append(ssr)

    print(f"SSR history: {len(ssr_values)} days aligned", file=sys.stderr)

    # Calculate SSR Oscillator
    osc, n_days, sma, std = ssr_oscillator(current_ssr, ssr_values)

    result = {
        "ssr": round(current_ssr, 4),
        "ssr_oscillator": round(osc, 3),
        "ssr_200d_sma": round(sma, 4),
        "ssr_200d_std": round(std, 4),
        "history_days": n_days,
        "stablecoin_mc_b": round(stable_mc_current / 1e9, 1),
        "btc_mc_t": round(btc_mc_current / 1e12, 2),
        "interpretation": (
            "极端高位(稳定币购买力极弱，BTC被高估)" if osc > 2.0 else
            "偏高(稳定币购买力偏弱)" if osc > 0.5 else
            "平衡区域" if abs(osc) <= 0.5 else
            "偏低(稳定币购买力偏强)" if osc > -2.0 else
            "极端低位(稳定币购买力极强，BTC被低估)"
        ),
        "note": (
            "SSR Oscillator > 0 = 稳定币相对BTC购买力弱于200日均值(BTC相对高估)。"
            "正值扩大往往出现在BTC上涨后，历史上对应顶部派发区。"
            "非机械买卖信号。"
        )
    }

    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
