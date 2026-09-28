#!/usr/bin/env python3
"""并行拉取所有免费链上数据源，一次性输出完整 JSON。
数据源：9992100.xyz, Coin Metrics, Blockchair, checkonchain, LookIntoBitcoin
用法: python3 fetch_all.py [--json] [--cache CACHE_DIR]
"""
import json, os, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

CACHE_DIR = os.path.expanduser("~/.hermes/data")
os.makedirs(CACHE_DIR, exist_ok=True)

def fetch(url, timeout=15, headers=None, retries=2):
    """HTTP GET with retry."""
    hdrs = {"User-Agent": "Mozilla/5.0"}
    if headers:
        hdrs.update(headers)
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:
            last_err = e
            if attempt < retries:
                import time
                time.sleep(2)
    raise last_err

# ── 各数据源抓取函数 ──

def fetch_ahr999():
    """9992100.xyz — AHR999 + BTC价格 + 200日定投成本 + 市占率 + F&G"""
    data = fetch("https://9992100.xyz/api/ahr999")
    return {
        "btc_price": data.get("price_usd"),
        "ahr999": data.get("ahr999"),
        "gma200_usd": data.get("gma200_usd"),         # 200日定投成本
        "btc_dominance_pct": data.get("btc_dominance_pct"),
        "fear_greed": data.get("fear_greed", {}).get("value"),
        "fear_greed_class": data.get("fear_greed", {}).get("classification"),
        "coin_age_days": data.get("coin_age_days"),
        "ahr999_7d": data.get("series_7d"),
        "source": "9992100.xyz",
    }

def fetch_puell():
    """Puell Multiple —— 首选 LookIntoBitcoin（1 天滞后）+ 本地自算互校。

    2026-09-25 换源：原 bgeometrics 的 /v1/puell-multiple 数据日期比当天晚约 7 天
    （源侧滞后），改用 puell_multiple.py：LITB 真值（Edge+CDP）+ Coin Metrics 自算互校。
    """
    import importlib.util
    path = os.path.expanduser("~/.hermes/scripts/puell_multiple.py")
    spec = importlib.util.spec_from_file_location("puell_multiple_local", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.fetch_puell(use_litb=True)


def fetch_puell_bgeo():
    """api.bgeometrics.com — Puell Multiple（已弃用为主源：滞后约 7 天，仅留作第三方互校）"""
    data = fetch("https://api.bgeometrics.com/v1/puell-multiple", timeout=20)
    if isinstance(data, list) and data:
        latest = data[-1]
        return {
            "puell_multiple": latest.get("puellMultiple"),
            "puell_date": latest.get("d"),
            "lag_note": "源侧滞后约 7 天，仅作互校",
            "source": "api.bgeometrics.com",
        }
    return {"puell_multiple": None, "source": "api.bgeometrics.com", "error": "no data"}

def fetch_realized_price():
    """api.bgeometrics.com — Realized Price 最新值"""
    data = fetch("https://api.bgeometrics.com/v1/realized-price", timeout=20)
    if isinstance(data, list) and data:
        latest = data[-1]
        return {
            "realized_price": latest.get("realizedPrice"),
            "realized_price_date": latest.get("d"),
            "source": "api.bgeometrics.com",
        }
    return {"realized_price": None, "source": "api.bgeometrics.com", "error": "no data"}

def fetch_reserve_risk():
    """api.bgeometrics.com — Reserve Risk 最新值"""
    try:
        data = fetch("https://api.bgeometrics.com/v1/reserve-risk", timeout=20)
        if isinstance(data, list) and data:
            latest = data[-1]
            return {
                "reserve_risk": latest.get("reserveRisk"),
                "reserve_risk_date": latest.get("d"),
                "source": "api.bgeometrics.com",
            }
    except:
        pass
    return {"reserve_risk": None, "source": "api.bgeometrics.com", "error": "failed"}

def fetch_mvrv_zscore():
    """Coin Metrics — MVRV Z-Score（调用已有脚本）"""
    import subprocess
    script = os.path.expanduser("~/.hermes/scripts/mvrv_zscore.py")
    result = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=60)
    if result.returncode == 0:
        data = json.loads(result.stdout)
        return {
            "mvrv_ratio": data.get("mvrv_ratio"),
            "mvrv_zscore": data.get("mvrv_zscore"),
            "realized_cap_b": data.get("realized_cap_b"),
            "market_cap_b": data.get("market_cap_b"),
            "mvrv_date": data.get("data_date"),
            "interval": data.get("interval"),
            "source": "Coin Metrics + jsDelivr",
        }
    return {"mvrv_zscore": None, "source": "Coin Metrics", "error": result.stderr[:200]}

def fetch_cvdd():
    """Blockchair stats — CVDD 每日增量"""
    import subprocess
    script = os.path.expanduser("~/.hermes/scripts/cvdd_estimator.py")
    result = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=30)
    if result.returncode == 0:
        data = json.loads(result.stdout)
        return {
            "cvdd": data.get("cvdd"),
            "price_cvdd": data.get("price_cvdd"),
            "btc_price_cvdd": data.get("btc_price"),
            "cvdd_30d": data.get("cvdd_30d"),
            "cvdd_30d_change_pct": data.get("cvdd_30d_change_pct"),
            "cvdd_90d": data.get("cvdd_90d"),
            "cvdd_90d_change_pct": data.get("cvdd_90d_change_pct"),
            "cvdd_180d": data.get("cvdd_180d"),
            "cvdd_180d_change_pct": data.get("cvdd_180d_change_pct"),
            "cvdd_360d": data.get("cvdd_360d"),
            "cvdd_360d_change_pct": data.get("cvdd_360d_change_pct"),
            "cvdd_date": data.get("data_date"),
            "market_age_days": data.get("market_age_days"),
            "source": "Blockchair stats",
        }
    return {"cvdd": None, "source": "Blockchair", "error": result.stderr[:200]}


def fetch_sth_supply():
    """checkonchain.com 免费图表 — STH/LTH Supply（短期·长期持币量）

    2026-09-18 新增：替换原先「每周人工查看 LookIntoBitcoin」的手动流程。
    解析逻辑在 sth_supply.py 里（自包含 Plotly HTML + base64 数组解码）。
    """
    import importlib.util
    path = os.path.expanduser("~/.hermes/scripts/sth_supply.py")
    spec = importlib.util.spec_from_file_location("sth_supply_local", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.fetch_sth_supply()


def fetch_cvdd_litb():
    """LookIntoBitcoin 标准口径 CVDD（Edge+CDP 自动抓取，litb_chart.py）

    2026-09-20 新增：以前只能每周人工截图取值/校准，现在全自动。
    这是「标准口径」的权威值，可与下面的 axeladler×因子 结果互相印证。
    """
    import importlib.util
    path = os.path.expanduser("~/.hermes/scripts/litb_chart.py")
    spec = importlib.util.spec_from_file_location("litb_chart_local", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    data = mod.fetch_chart("cvdd", days=8)
    traces = {t.get("name"): t for t in data.get("traces", [])}
    cvdd = traces.get("CVDD") or {}
    price = traces.get("BTC Price") or {}
    last = cvdd.get("last") or [None, None]
    prev7 = cvdd.get("prev7") or [None, None]
    p_last = (price.get("last") or [None, None])[1]
    chg = None
    if isinstance(last[1], (int, float)) and isinstance(prev7[1], (int, float)) and prev7[1]:
        chg = round((last[1] / prev7[1] - 1) * 100, 3)
    return {
        "cvdd_litb": last[1],
        "cvdd_litb_date": (last[0] or "")[:10],
        "cvdd_litb_7d_ago": prev7[1],
        "cvdd_litb_7d_change_pct": chg,
        "price_litb": p_last,
        "series_8d": cvdd.get("series"),
        "source": "lookintobitcoin.com (Edge+CDP 自动)",
    }


# ── 主函数：并行拉取 ──

def main():
    import argparse
    parser = argparse.ArgumentParser(description="并行拉取所有链上数据")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON")
    parser.add_argument("--cache", default=CACHE_DIR, help="缓存目录")
    args = parser.parse_args()

    tasks = {
        "ahr999": fetch_ahr999,
        "realized_price": fetch_realized_price,
        "reserve_risk": fetch_reserve_risk,
        "mvrv": fetch_mvrv_zscore,
        "cvdd": fetch_cvdd,
        "sth_supply": fetch_sth_supply,
        "cvdd_litb": fetch_cvdd_litb,
    }

    results = {}
    errors = []

    if not args.json:
        print("并行拉取链上数据...", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
                if not args.json:
                    status = "✅" if results[name].get(list(results[name].keys())[0]) else "❌"
                    print(f"  {status} {name}", file=sys.stderr)
            except Exception as e:
                results[name] = {"error": str(e), "source": name}
                errors.append(f"{name}: {e}")
                if not args.json:
                    print(f"  ❌ {name}: {e}", file=sys.stderr)

    # Puell：走 LITB(Edge)，与 cvdd_litb 共用同一个 Windows CDP 端口 → 必须串行（并行会互相 kill）
    try:
        results["puell"] = fetch_puell()
    except Exception as e:
        results["puell"] = {"error": str(e)[:200], "source": "puell_multiple"}
        errors.append(f"puell: {e}")

    # 输出
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": results,
        "errors": errors if errors else None,
    }

    if args.json:
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(output, indent=2, ensure_ascii=False))

    # 缓存到本地
    cache_file = os.path.join(args.cache, "onchain_snapshot.json")
    with open(cache_file, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    return 0 if not errors else 1

if __name__ == "__main__":
    sys.exit(main())
