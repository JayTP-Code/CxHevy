"""Pull prices, analyst targets and earnings dates for the dashboard's stocks panel.

Reads the watchlist from config/stocks.json and writes data/stocks_summary.json
(what the dashboard's `stocks/summary` document holds). Data comes from Yahoo
Finance's public endpoints and is delayed.

    python scripts/stocks_sync.py
"""
import json
import os
from datetime import datetime, timezone

import requests

ROOT = os.path.join(os.path.dirname(__file__), "..")
BASE = "https://query1.finance.yahoo.com"


def session():
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"
    s.get("https://fc.yahoo.com", timeout=20)  # sets the cookie the crumb needs
    crumb = s.get(f"{BASE}/v1/test/getcrumb", timeout=20).text.strip()
    return s, crumb


def history(s, symbol):
    r = s.get(f"{BASE}/v8/finance/chart/{symbol}", params={"range": "6mo", "interval": "1d"}, timeout=20)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    meta = res["meta"]
    closes = res["indicators"]["quote"][0]["close"]
    off = meta.get("gmtoffset") or 0  # dates in the exchange's own time zone

    def day(t):
        return datetime.fromtimestamp(t + off, timezone.utc).date().isoformat()
    pts = [{"date": day(t), "close": round(c, 4)}
           for t, c in zip(res.get("timestamp", []), closes) if c is not None]
    meta["market_day"] = day(meta["regularMarketTime"]) if meta.get("regularMarketTime") else None
    return meta, pts


def details(s, crumb, symbol):
    r = s.get(f"{BASE}/v10/finance/quoteSummary/{symbol}",
              params={"modules": "financialData,calendarEvents", "crumb": crumb}, timeout=20)
    if r.status_code != 200:
        return {}
    res = (r.json().get("quoteSummary") or {}).get("result") or [{}]
    return res[0]


def raw(d, key):
    v = d.get(key)
    return v.get("raw") if isinstance(v, dict) else v


def change(pts, days):
    """Percent change from the close `days` calendar days ago to the latest close."""
    if len(pts) < 2:
        return None
    last = datetime.fromisoformat(pts[-1]["date"])
    base = next((p for p in pts if (last - datetime.fromisoformat(p["date"])).days <= days), pts[0])
    return round((pts[-1]["close"] / base["close"] - 1) * 100, 2) if base["close"] else None


def build(item, s, crumb):
    symbol = item["symbol"]
    meta, pts = history(s, symbol)
    d = details(s, crumb, symbol)
    fin, cal = d.get("financialData") or {}, (d.get("calendarEvents") or {}).get("earnings") or {}

    price = meta.get("regularMarketPrice") or (pts[-1]["close"] if pts else None)
    # Previous close = last daily close before the latest trading day.
    before = [p for p in pts if meta.get("market_day") and p["date"] < meta["market_day"]]
    prev = before[-1]["close"] if before else meta.get("chartPreviousClose")
    if price and meta.get("market_day"):
        # Make the chart end at the latest price, for the latest trading day.
        pts = [p for p in pts if p["date"] < meta["market_day"]] + [{"date": meta["market_day"], "close": price}]
    earnings = [e.get("fmt") for e in cal.get("earningsDate") or [] if e.get("fmt")]
    target = raw(fin, "targetMeanPrice")

    return {
        "symbol": item.get("label", symbol),
        "yahoo": symbol,
        "name": meta.get("longName") or meta.get("shortName") or symbol,
        "exchange": meta.get("fullExchangeName"),
        "currency": meta.get("currency"),
        "price": price,
        "prev_close": prev,
        "day_pct": round((price / prev - 1) * 100, 2) if price and prev else None,
        "m1_pct": change(pts, 31),
        "m3_pct": change(pts, 92),
        "history": pts[-66:],  # about 3 months of trading days
        "analysts": {
            "target_mean": target,
            "target_high": raw(fin, "targetHighPrice"),
            "target_low": raw(fin, "targetLowPrice"),
            "count": raw(fin, "numberOfAnalystOpinions"),
            "rating": None if fin.get("recommendationKey") in (None, "none") else fin.get("recommendationKey"),
            "upside_pct": round((target / price - 1) * 100, 1) if target and price else None,
        } if target else None,
        "earnings_date": earnings[0] if earnings else None,
        "earnings_estimate": bool(cal.get("isEarningsDateEstimate")),
        "market_time": meta.get("regularMarketTime"),
    }


def main():
    with open(os.path.join(ROOT, "config", "stocks.json")) as f:
        watchlist = json.load(f)["watchlist"]
    s, crumb = session()
    stocks = []
    for item in watchlist:
        try:
            stocks.append(build(item, s, crumb))
        except Exception as e:  # keep the rest of the list if one ticker fails
            print(f"{item['symbol']}: {e}")
    out = {"synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "stocks": stocks}
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    path = os.path.join(ROOT, "data", "stocks_summary.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"{len(stocks)} stocks -> {os.path.relpath(path, ROOT)}")


if __name__ == "__main__":
    main()
