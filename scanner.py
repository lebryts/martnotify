"""
MartNotify Local Scanner
========================
Run this on your local machine (not Vercel) so IndiaMart doesn't block it.
It reads config from your Upstash Redis and writes results back to it,
so your Vercel dashboard stays in sync automatically.

Usage:
  python3 scanner.py                  # Run once
  watch -n 3600 python3 scanner.py   # Run every hour (Linux)
  
Or set up a cron job:
  crontab -e
  0 * * * * cd /path/to/martnotify && python3 scanner.py >> /tmp/scanner.log 2>&1
"""

import os
import re
import requests
import redis as redis_lib
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────
REDIS_URL      = os.environ.get("REDIS_URL", "rediss://default:gQAAAAAAAUDJAAIgcDE0N2ViOTEzMzZkYzQ0Y2EyYTEzYmM0MmNjZGEyZWViYg@rare-dory-82121.upstash.io:6379")
DEFAULT_TOPIC  = "indiamart_leads"
API_URL        = "https://trade.indiamart.com/tradereact/searchpage"
# ─────────────────────────────────────────────────────────────────────────────

def log(msg, r=None):
    ts = datetime.now().strftime('%H:%M:%S')
    entry = f"{ts} - {msg}"
    print(entry)
    if r:
        r.lpush("activity_log", entry)
        r.ltrim("activity_log", 0, 49)
        r.set("last_check_time", ts)

def parse_quantity(qty_str):
    qty_str = qty_str.lower().replace("quantity:", "").strip()
    match = re.search(r"(\d+(\.\d+)?)", qty_str)
    if not match: return 0
    value = float(match.group(1))
    if "ton" in qty_str or "mt" in qty_str: return value * 1000
    return value

def parse_value(val_str):
    val_str = val_str.lower().replace("probable order value:", "").strip()
    multiplier = 1
    if "lakh" in val_str: multiplier = 100000
    elif "cr" in val_str: multiplier = 10000000
    numbers = re.findall(r"(\d+(\.\d+)?)", val_str)
    if not numbers: return 0
    return max([float(n[0]) for n in numbers]) * multiplier

def main():
    # Connect to Redis
    try:
        r = redis_lib.from_url(REDIS_URL, decode_responses=True)
        r.ping()
        print("✅ Connected to Redis")
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        return

    # Read config from Redis
    query      = r.get("config_search_query") or "cocopeat block"
    min_val    = int(r.get("config_min_value")  or 1000)
    min_qty    = int(r.get("config_min_qty_kg") or 300)
    ntfy_topic = r.get("ntfy_topic") or DEFAULT_TOPIC
    cookie     = r.get("im_cookie") or os.environ.get("INDIAMART_COOKIE", "")
    user_agent = r.get("user_agent") or "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"

    log(f"Scanning for: {query}", r)

    headers = {
        "User-Agent": user_agent,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": "https://trade.indiamart.com/buyersearch.mp?ss=cocopeat+block"
    }
    if cookie:
        headers["Cookie"] = cookie

    payload = {
        "source": "eto.search.lead",
        "q": query,
        "options.start": 0,
        "options.results": 20
    }

    try:
        response = requests.post(API_URL, data=payload, headers=headers, timeout=20)
        if response.status_code != 200:
            log(f"HTTP Error {response.status_code} — check your cookie!", r)
            return

        data    = response.json()
        results = data.get("results", [])
        log(f"Got {len(results)} leads from API", r)

        found = 0
        for lead in results:
            fields     = lead.get("fields", {})
            display_id = fields.get("displayid")
            if not display_id or r.sismember("seen_leads", display_id):
                continue

            title = fields.get("title", "Unknown Product")
            city  = fields.get("city", "Unknown")
            isq   = fields.get("isqdetails", [])

            total_qty = 0
            max_value = 0
            for detail in isq:
                if "quantity" in detail.lower(): total_qty = parse_quantity(detail)
                if "value"    in detail.lower(): max_value = parse_value(detail)

            if total_qty >= min_qty or max_value >= min_val:
                found += 1
                href = f"https://trade.indiamart.com/details.mp?offer={display_id}"
                msg  = f"📦 {title}\n📍 {city}\n⚖️ {total_qty} KG\n💰 Rs. {max_value:,.0f}\n🔗 {href}"
                try:
                    requests.post(
                        f"https://ntfy.sh/{ntfy_topic}",
                        data=msg.encode("utf-8"),
                        headers={"Title": "Lead Match!", "Priority": "5"},
                        timeout=5
                    )
                    print(f"  📲 Notified: {title} ({city})")
                except Exception as ne:
                    print(f"  ⚠️ ntfy failed: {ne}")
                r.sadd("seen_leads", display_id)

        log(f"Scan complete. {found} matches out of {len(results)} leads.", r)

    except Exception as e:
        log(f"Error: {e}", r)

if __name__ == "__main__":
    main()
