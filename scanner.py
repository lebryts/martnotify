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
import sys
import requests
import redis as redis_lib
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────
RAW_REDIS_URL = os.environ.get("REDIS_URL", "rediss://default:gQAAAAAAAUDJAAIgcDE0N2ViOTEzMzZkYzQ0Y2EyYTEzYmM0MmNjZGEyZWViYg@rare-dory-82121.upstash.io:6379").strip()
# If it's just a token (doesn't have a scheme), build the URL
if "://" not in RAW_REDIS_URL:
    REDIS_URL = f"rediss://default:{RAW_REDIS_URL}@rare-dory-82121.upstash.io:6379"
else:
    REDIS_URL = RAW_REDIS_URL

DEFAULT_TOPIC  = "indiamart_leads"
API_URL        = "https://trade.indiamart.com/tradereact/searchpage"
# ─────────────────────────────────────────────────────────────────────────────

def log(msg, r=None):
    ts = datetime.now().strftime('%H:%M:%S')
    entry = f"{ts} - {msg}"
    print(entry)
    if r:
        try:
            r.lpush("monitor_logs", entry)
            r.ltrim("monitor_logs", 0, 49)
            r.set("last_check_time", ts)
        except: pass

def parse_quantity(qty_str):
    qty_str = qty_str.lower().replace("quantity:", "").replace(",", "").strip()
    match = re.search(r"(\d+(\.\d+)?)", qty_str)
    if not match: return 0
    value = float(match.group(1))
    if "ton" in qty_str or "mt" in qty_str: return value * 1000
    return value

def parse_value(val_str):
    val_str = val_str.lower().replace("probable order value:", "").replace(",", "").strip()
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
        sys.exit(1)

    # Read config from Redis
    if r.get("monitor_status") != "true":
        log("Monitoring is currently disabled (OFF). Skipping scan.", r)
        return

    query      = r.get("config_search_query") or "cocopeat block"
    
    def safe_int(val, default):
        if val is None or str(val).lower() == 'none' or not str(val).strip():
            return default
        try:
            return int(float(str(val).replace(",", "")))
        except:
            return default

    min_val    = safe_int(r.get("config_min_value"), 1000)
    min_qty    = safe_int(r.get("config_min_qty_kg"), 300)
    ntfy_topic = str(r.get("ntfy_topic") or DEFAULT_TOPIC).strip().replace("\n", "").replace("\r", "")
    if ntfy_topic.lower() == "none": ntfy_topic = DEFAULT_TOPIC
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
        "options.results": 100,
        "options.sort": "indexeddate desc"
    }

    try:
        response = requests.post(API_URL, data=payload, headers=headers, timeout=20)
        if response.status_code != 200:
            log(f"HTTP Error {response.status_code} — check your cookie!", r)
            sys.exit(1)

        data    = response.json()
        results = data.get("results", [])
        
        log(f"--- Top 20 / {len(results)} Leads Fetched ---", r)
        for i, lead in enumerate(results[:20]):
            f = lead.get("fields", {})
            t = f.get("title", "No Title")
            i_id = f.get("displayid", "N/A")
            d_type = f.get("datatype", "lead")
            log(f"[{i+1}] {t[:25]}... ({i_id}) [{d_type}]", r)

        found = 0
        for lead in results:
            fields     = lead.get("fields", {})
            display_id = fields.get("displayid")
            
            # --- FILTERS ---
            # 1. Must be a 'lead' type (skip bizfeed/tenders which are often broken/expired)
            if fields.get("datatype") != "lead":
                continue
            
            # 2. Must be an 'OPEN' status
            status = fields.get("purchase_status", "OPEN")
            if status != "OPEN":
                continue

            # 3. Skip 13-digit IDs (often unreliable/transient)
            if display_id and len(str(display_id)) > 12:
                continue

            if not display_id or r.sismember("seen_leads", display_id):
                continue

            # Extract date and format nicely
            raw_post_date = fields.get("releasedate") or fields.get("postdate") or fields.get("indexeddate") or fields.get("lastactiondate") or "N/A"
            
            def get_relative_time(iso_str):
                try:
                    from datetime import datetime, timedelta
                    now = datetime.utcnow()
                    past = datetime.fromisoformat(iso_str.replace("Z", ""))
                    diff = now - past
                    seconds = diff.total_seconds()
                    if seconds < 60: return "Just now"
                    if seconds < 3600: return f"{int(seconds//60)} min ago"
                    if seconds < 86400: return f"{int(seconds//3600)} hr ago"
                    return f"{int(diff.days)} days ago"
                except: return iso_str[:16].replace("T", " ")

            is_recent = False
            post_display = "N/A"
            if "T" in raw_post_date:
                post_display = get_relative_time(raw_post_date)
                try:
                    from datetime import datetime, timedelta
                    now = datetime.utcnow()
                    past = datetime.fromisoformat(raw_post_date.replace("Z", ""))
                    is_recent = (now - past).total_seconds() < 172800 # 48 hours
                except: pass
            
            # Filter: only leads from last 48 hours
            if not is_recent:
                continue

            title      = fields.get("title", "Unknown Product")
            city       = fields.get("city", "Unknown")
            isq        = fields.get("isqdetails", [])
            
            total_qty = 0
            max_value = 0

            # 1. Check direct fields
            if fields.get("quantity"):
                total_qty = parse_quantity(str(fields.get("quantity")))
            if fields.get("ordervalue"):
                max_value = parse_value(str(fields.get("ordervalue")))

            # 2. Check ISQ details (sometimes has more specific data)
            for detail in isq:
                qty_val = parse_quantity(detail)
                if qty_val > total_qty: total_qty = qty_val
                
                val_val = parse_value(detail)
                if val_val > max_value: max_value = val_val


            if total_qty >= min_qty or max_value >= min_val:
                found += 1
                href = f"https://trade.indiamart.com/details.mp?offer={display_id}"
                msg  = f"📅 Posted: {post_display}\n📦 {title}\n📍 {city}\n📝 Status: {status}\n⚖️ {total_qty} KG\n💰 Rs. {max_value:,.0f}\n🔗 {href}"
                try:
                    resp = requests.post(
                        f"https://ntfy.sh/{ntfy_topic}",
                        data=msg.encode("utf-8"),
                        headers={"Title": "New IndiaMart Lead!", "Priority": "5"},
                        timeout=10
                    )
                    if resp.status_code == 200:
                        print(f"  📲 Notified: {title} ({city})")
                        log(f"Alert Sent: {title[:20]}... ({city})", r)
                        r.sadd("seen_leads", display_id)
                    else:
                        print(f"❌ ntfy failed: {resp.status_code} {resp.text}")
                        log(f"Error: ntfy failed ({resp.status_code})", r)
                except Exception as ne:
                    print(f"❌ ntfy error: {ne}")
                    log(f"Error: ntfy connection failed", r)

        log(f"Scan complete. {found} matches out of {len(results)} leads.", r)

    except Exception as e:
        log(f"Error: {e}", r)

if __name__ == "__main__":
    main()
