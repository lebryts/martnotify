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
import json
import time
import redis as redis_lib
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────
RAW_REDIS_URL = (os.environ.get("REDIS_URL") or "rediss://default:gQAAAAAAAUDJAAIgcDE0N2ViOTEzMzZkYzQ0Y2EyYTEzYmM0MmNjZGEyZWViYg@rare-dory-82121.upstash.io:6379").strip()
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

def run_scan(r_client=None):
    r = r_client
    if not r and REDIS_URL:
        for attempt in range(3):
            try:
                r = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=5, retry_on_timeout=True)
                r.ping()
                print(f"✅ Connected to Redis (Attempt {attempt+1})")
                break
            except Exception as e:
                print(f"❌ Redis connection failed (Attempt {attempt+1}): {e}")
                r = None
                time.sleep(1)

    # ── Configuration (Fetched from Dashboard/Redis) ──────────────────────────
    query = "cocopeat block"
    min_qty = 0
    min_val = 0
    ntfy_topic = DEFAULT_TOPIC
    cookie = os.environ.get("INDIAMART_COOKIE", "")
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    if r:
        try:
            # Match keys exactly with api/index.py
            query = r.get("config_search_query") or r.get("search_query") or query
            min_qty = float(r.get("config_min_qty_kg") or r.get("min_qty") or min_qty)
            min_val = float(r.get("config_min_value") or r.get("min_val") or min_val)
            ntfy_topic = r.get("ntfy_topic") or ntfy_topic
            cookie = r.get("im_cookie") or r.get("indiamart_cookie") or cookie
            ua = r.get("user_agent") or ua
            
            m_status = r.get("monitor_status") or r.get("monitoring_status") or "true"
            if str(m_status).lower() in ["off", "false"]:
                log(f"Monitoring is currently disabled ({m_status}). Skipping scan.", r)
                return False
        except Exception as e:
            print(f"Error reading config from Redis: {e}. Using defaults.")

    log(f"Scan Config: Query='{query}', MinQty={min_qty}kg, MinValue=₹{min_val}", r)
    # ─────────────────────────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────────────────────

    try:
        headers = {"User-Agent": ua}
        if cookie: headers["Cookie"] = cookie

        def fetch_leads(start_index):
            payload = {
                "options.filters.glusrid.data": "",
                "options.filters.glusrid.type": "value",
                "options.filters.type.data": "lead",
                "options.results": 20,
                "options.start": start_index,
                "q": query,
                "search_server": "blsearch.indiamart.com",
                "source": "eto.search.lead"
            }
            try:
                res = requests.post(API_URL, json=payload, headers=headers, timeout=20)
                if res.status_code == 200:
                    return res.json().get("results", [])
                else:
                    log(f"HTTP Error {res.status_code} at index {start_index}", r)
                    return []
            except Exception as e:
                log(f"Fetch Error at {start_index}: {e}", r)
                return []

        try:
            # Fetch 60 leads total in 3 batches
            batch1 = fetch_leads(0)
            # batch2 = fetch_leads(20)
            # batch3 = fetch_leads(40)
            results = batch1 #+ batch2 + batch3
            
            # Debug: Save to file for inspection
            try:
                with open("debug_data.json", "w") as f:
                    json.dump({"results": results, "total_fetched": len(results)}, f, indent=4)
                log(f"Leads (Total {len(results)}) saved to debug_data.json for inspection.", r)
            except Exception as fe:
                log(f"Failed to save debug file: {fe}", r)

            log(f"--- All {len(results)} Leads Fetched ---", r)
            for i, lead in enumerate(results):
                f = lead.get("fields", {})
                t = f.get("title", "No Title")
                i_id = f.get("displayid", "N/A")
                log(f"[{i+1}] {t[:25]} ({i_id})", r)

        except Exception as e:
            log(f"Fetch Error: {e}", r)
            return False

        processed_file = "processed_leads.txt"
        try:
            with open(processed_file, "w") as pf:
                pf.write(f"--- Scan at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n\n")
        except: pass

        found = 0
        skipped_seen = 0
        for lead in results:
            fields     = lead.get("fields", {})
            display_id = fields.get("displayid")
            
            if display_id and len(str(display_id)) > 12:
                continue

            status = fields.get("purchase_status", "OPEN")
            if status != "OPEN":
                continue

            # Extract dates
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
            
            # --- INTELLIGENT QUANTITY EXTRACTION ---
            unit_count = 0
            block_weight = 1
            is_explicit_weight = False
            
            qty_candidates = [title, str(fields.get("Quantity", ""))] + [str(x) for x in isq]
            
            for detail in qty_candidates:
                d = detail.lower()
                # 1. Skip price/value fields
                if any(x in d for x in ["value", "price", "rs", "rupee", "budget"]):
                    continue
                
                num = parse_quantity(d)
                if num <= 0: continue
                
                # 2. Check context
                if any(x in d for x in ["weight", "per", "size", "spec", "mass"]):
                    # If it says "Weight per block: 5kg", block_weight = 5
                    block_weight = num
                elif any(x in d for x in ["qty", "quantity", "piece", "pc", "nos", "bag", "box", "unit"]):
                    # If this specific string has weight, mark as explicit
                    if any(x in d for x in ["kg", "ton", "mt"]):
                        unit_count = num
                        is_explicit_weight = True
                    else:
                        unit_count = num
                        is_explicit_weight = False
                elif any(x in d for x in ["kg", "ton", "mt"]):
                     # Just a weight mentioned in a field (often already total weight)
                     if num > unit_count: unit_count = num
                     is_explicit_weight = True

            # Calculate total
            if not is_explicit_weight and block_weight > 1:
                total_qty = unit_count * block_weight
            else:
                total_qty = unit_count if unit_count > 0 else block_weight

            # --- VALUE EXTRACTION ---
            candidates_val = [str(fields.get("ordervalue", "")), str(fields.get("tendervalue", ""))] + [str(x) for x in isq]
            max_value = 0
            for c in candidates_val:
                cv = c.lower()
                if any(x in cv for x in ["value", "price", "rs", "rupee", "budget"]):
                    val = parse_value(cv)
                    if val > max_value: max_value = val

            # Construct the message
            href = f"https://trade.indiamart.com/details.mp?offer={display_id}"
            debug_msg  = f"📅 Posted: {post_display}\n📦 {title}\n📍 {location}\n📝 Status: {status}\n⚖️ {total_qty} KG (Parsed)\n💰 Rs. {max_value:,.0f} (Parsed)\n🔗 {href}\n"
            
            try:
                with open(processed_file, "a") as pf:
                    pf.write(f"--- Lead {display_id} ---\n{debug_msg}\n")
            except: pass

            # --- FILTERS ---
            if total_qty < min_qty:
                continue
            
            if max_value > 0 and max_value < min_val:
                continue

            if not display_id:
                continue
            
            is_seen = (r and r.sismember("seen_leads", display_id))
            if is_seen:
                skipped_seen += 1
                continue

            # Found a new high-value match!
            if found >= 10:
                found += 1
                continue

            found += 1
            log(f"  ⭐ MATCH FOUND! {title[:20]}... ({total_qty} KG)", r)
            
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
                    if r: r.sadd("seen_leads", display_id)
                else:
                    print(f"❌ ntfy failed: {resp.status_code} {resp.text}")
                    log(f"Error: ntfy failed ({resp.status_code})", r)
            except Exception as ne:
                print(f"❌ ntfy error: {ne}")
                log(f"Error: ntfy connection failed", r)

        log(f"Scan complete. Found {found} matches ({skipped_seen} were already notified).", r)
        return True

    except Exception as e:
        log(f"Error: {e}", r)
        return False

def main():
    run_scan()

if __name__ == "__main__":
    main()
