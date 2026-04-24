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
    from datetime import timedelta
    # UTC + 5:30 = IST
    ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
    ts = ist_now.strftime('%H:%M:%S')
    entry = f"{ts} - {msg}"
    print(entry)
    if r:
        try:
            r.lpush("monitor_logs", entry)
            r.ltrim("monitor_logs", 0, 49)
            r.set("last_check_time", ts)
        except: pass

def parse_quantity(qty_str):
    qty_str = qty_str.lower().replace(",", "").strip()
    # Find numbers and their following words/units
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*([a-z\s]*)", qty_str)
    best = 0
    for num_str, suffix in matches:
        try:
            val = float(num_str)
            if any(x in suffix for x in ["ton", "mt"]): val *= 1000
            if val > best: best = val
        except: continue
    return best

def parse_value(val_str):
    val_str = val_str.lower().replace(",", "").strip()
    multiplier = 1
    if "lakh" in val_str: multiplier = 100000
    if "cr" in val_str or "crore" in val_str: multiplier = 10000000
    numbers = re.findall(r"(\d+(?:\.\d+)?)", val_str)
    if not numbers: return 0
    return max([float(n) for n in numbers]) * multiplier

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
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://trade.indiamart.com",
        "referer": f"https://trade.indiamart.com/buyersearch.mp?ss={query.replace(' ', '+')}",
        "user-agent": user_agent,
        "sec-ch-ua": '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Linux"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "priority": "u=1, i"
    }
    if cookie:
        headers["Cookie"] = cookie

    payload = {
        "options.filters.glusrid.data": "",
        "options.filters.glusrid.type": "value",
        "options.filters.type.data": "lead",
        "options.results": 40,
        "options.start": 0,
        "q": query,
        "search_server": "blsearch.indiamart.com",
        "source": "eto.search.lead"
    }

    try:
        response = requests.post(API_URL, json=payload, headers=headers, timeout=20)
        if response.status_code != 200:
            log(f"HTTP Error {response.status_code}", r)
            sys.exit(1)

        data    = response.json()
        results = data.get("results", [])
        
        log(f"--- Top 20 / {len(results)} Leads Fetched ---", r)
        for i, lead in enumerate(results[:20]):
            f = lead.get("fields", {})
            t = f.get("title", "No Title")
            i_id = f.get("displayid", "N/A")
            log(f"[{i+1}] {t[:25]} ({i_id})", r)

        found = 0
        for lead in results:
            fields     = lead.get("fields", {})
            display_id = fields.get("displayid")
            
            # --- FILTERS ---
            # 1. Skip IDs longer than 12 digits (these are usually stale BizFeed/Marketing items)
            if display_id and len(str(display_id)) > 12:
                continue

            # 2. Status must be OPEN
            status = fields.get("purchase_status", "OPEN")
            if status != "OPEN":
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

            post_display = "N/A"
            if "T" in raw_post_date:
                post_display = get_relative_time(raw_post_date)
            
            title      = fields.get("title", "Unknown Product")
            city       = fields.get("city_string") or fields.get("city") or "India"
            state      = fields.get("state") or ""
            location   = f"{city}, {state}".strip(", ")
            isq        = fields.get("isqdetails", [])
            
            # --- DATA EXTRACTION ---
            # 1. Extract raw numbers for Qty and Value
            unit_qty = 0
            block_weight = 1 
            qty_is_pieces = False
            
            # Check all possible quantity field names (capitalized and lowercase)
            qty_candidates = [
                str(fields.get("quantity", "")),
                str(fields.get("Quantity", ""))
            ] + [str(x) for x in isq]
            
            for detail in qty_candidates:
                d = detail.lower()
                q = parse_quantity(d)
                if q > 0:
                    if "piece" in d or "pc" in d or "box" in d or "bag" in d:
                        unit_qty = q
                        qty_is_pieces = True
                    elif "weight" in d or "size" in d or "kg" in d:
                        # Extract weight per item
                        block_weight = q
                    else:
                        if q > unit_qty: unit_qty = q

            # Multiplication Logic for: "320 pieces" x "5 Kg"
            if qty_is_pieces and block_weight > 1:
                total_qty = unit_qty * block_weight
            else:
                total_qty = max(unit_qty, block_weight) if not qty_is_pieces else unit_qty

            # 2. Value Extraction (picks higher end of range)
            candidates_val = [str(fields.get("ordervalue", "")), str(fields.get("tendervalue", ""))] + [str(x) for x in isq]
            max_value = max([parse_value(c) for c in candidates_val]) if candidates_val else 0

            # --- FILTERS ---
            if total_qty < min_qty:
                continue

            found += 1
            href = f"https://trade.indiamart.com/details.mp?offer={display_id}"
            msg  = f"📅 Posted: {post_display}\n📦 {title}\n📍 {location}\n📝 Status: {status}\n⚖️ {total_qty} KG\n💰 Rs. {max_value:,.0f}\n🔗 {href}"
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
