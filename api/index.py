import os
import re
import time
import random
import requests
import json
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from bs4 import BeautifulSoup
import redis
from urllib.parse import urlparse, parse_qs

# Optional Selenium for local robust scraping
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

app = Flask(__name__)
CORS(app)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- CONFIGURATION & REDIS ---
DEFAULT_NTFY_TOPIC = os.environ.get('NTFY_TOPIC', 'indiamart_leads')
REDIS_URL = os.environ.get('REDIS_URL') or "rediss://default:gQAAAAAAAUDJAAIgcDE0N2ViOTEzMzZkYzQ0Y2EyYTEzYmM0MmNjZGEyZWViYg@rare-dory-82121.upstash.io:6379"

if REDIS_URL:
    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        r.ping()
    except Exception as e:
        print(f"Redis connection error: {e}")
        REDIS_URL = None # Force fallback to MockRedis
if not REDIS_URL:
    class MockRedis:
        def __init__(self): self.data = {}
        def get(self, k): return self.data.get(k)
        def set(self, k, v): self.data[k] = v
        def lpush(self, k, v): 
            if k not in self.data: self.data[k] = []
            self.data[k].insert(0, v)
        def lrange(self, k, s, e): return self.data.get(k, [])[s:e+1 if e != -1 else None]
        def ltrim(self, k, s, e): self.data[k] = self.data.get(k, [])[s:e+1 if e != -1 else None]
        def sismember(self, k, v): 
            seen = self.data.get(k, set())
            return v in seen
        def sadd(self, k, v):
            if k not in self.data: self.data[k] = set()
            self.data[k].add(v)
        def expire(self, k, t): pass
    r = MockRedis()

def add_log(msg):
    log_entry = f"{datetime.now().strftime('%H:%M:%S')} - {msg}"
    print(log_entry)
    r.lpush("monitor_logs", log_entry)
    r.ltrim("monitor_logs", 0, 49)

def parse_quantity(qty_str):
    if not qty_str: return 0
    qty_str = str(qty_str).lower().replace("quantity", "").replace(":", "").replace(",", "").strip()
    match = re.search(r"(\d+(\.\d+)?)", qty_str)
    if not match: return 0
    value = float(match.group(1))
    if any(unit in qty_str for unit in ["ton", "mt", "tonne"]): return value * 1000
    return value

def parse_value(val_str):
    if not val_str: return 0
    val_str = str(val_str).lower().replace("probable order value", "").replace("rs", "").replace(":", "").replace(",", "").strip()
    multiplier = 100000 if "lakh" in val_str else 10000000 if "cr" in val_str else 1
    numbers = re.findall(r"(\d+(\.\d+)?)", val_str)
    if not numbers: return 0
    vals = [float(n[0]) for n in numbers]
    return max(vals) * multiplier

# --- SCRAPER LOGIC ---
def scrape_with_selenium(url):
    if not SELENIUM_AVAILABLE: return None
    add_log("Starting Selenium (Local Mode)...")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    driver = None
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.set_page_load_timeout(40)
        driver.get(url)
        
        # Wait specifically for cards or timeout
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".TRA_con, .TRA_brickContainer, #root .TRA_row"))
            )
            add_log("Detected cards in Selenium.")
        except:
            add_log("Timeout waiting for cards in Selenium.")
            
        time.sleep(2) # Final settling
        html = driver.page_source
        add_log(f"Selenium Complete. Length: {len(html)}")
        return html
    except Exception as e:
        add_log(f"Selenium Fail: {str(e)[:50]}")
        return None
    finally:
        if driver: driver.quit()

@app.route('/')
def serve_index():
    return send_from_directory(BASE_DIR, 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    if os.path.exists(os.path.join(BASE_DIR, path)):
        return send_from_directory(BASE_DIR, path)
    return "Not found", 404


@app.route('/api/debug')
def debug_info():
    ua = r.get("user_agent") if r else "No Redis"
    url = REDIS_URL or "None"
    return jsonify({
        "redis_url_start": url[:30] + "...",
        "redis_url_len": len(url),
        "is_mock": not REDIS_URL,
        "user_agent": ua
    })

@app.route('/api/status', methods=['GET'])
def get_status():
    try:
        is_running = r.get("monitor_status") == "true"
        last_check = r.get("last_check_time") or "Never"
        logs = r.lrange("monitor_logs", 0, -1)
        ntfy_topic = r.get("ntfy_topic") or DEFAULT_NTFY_TOPIC
        # Persist the default so it stays stable across calls
        if not r.get("ntfy_topic"):
            r.set("ntfy_topic", ntfy_topic)
        config = {
            "minValue": int(r.get("config_min_value") or 1000),
            "minQtyKg": int(r.get("config_min_qty_kg") or 300),
            "searchQuery": r.get("config_search_query") or "cocopeat block",
            "ntfyTopic": ntfy_topic,
            "userAgent": r.get("user_agent") or ""
        }
    except Exception as e: return jsonify({"error": str(e)}), 500
    return jsonify({"isRunning": is_running, "lastStatus": f"Last checked: {last_check}", "logs": logs, "config": config})

@app.route('/api/config', methods=['POST'])
def update_config():
    data = request.json
    if 'minValue' in data: r.set("config_min_value", str(data['minValue']))
    if 'minQtyKg' in data: r.set("config_min_qty_kg", str(data['minQtyKg']))
    if 'searchQuery' in data: r.set("config_search_query", str(data['searchQuery']))
    if 'ntfyTopic' in data: r.set("ntfy_topic", str(data['ntfyTopic']))
    if 'imCookie' in data and data['imCookie']: r.set("im_cookie", str(data['imCookie']))
    if 'userAgent' in data and data['userAgent']: r.set("user_agent", str(data['userAgent']))
    add_log("Configuration updated.")
    return jsonify({"success": True})

@app.route('/api/toggle', methods=['POST'])
def toggle_monitor():
    try:
        data = request.json
        enable = data.get('enable', False)
        r.set("monitor_status", "true" if enable else "false")
        add_log(f"Monitor {'started' if enable else 'stopped'}.")
        return jsonify({"isRunning": enable})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/cron', methods=['GET'])
def run_cron():
    is_manual = request.args.get("manual") == "true"
    
    # Security check for external cron services (only require secret for automated cron)
    secret = os.environ.get('CRON_SECRET')
    if secret and not is_manual and request.args.get('secret') != secret:
        return "Unauthorized", 401

    try:
        if not is_manual and r.get("monitor_status") != "true": return "Monitor is disabled", 200

        query = r.get("config_search_query") or "cocopeat block"
        min_val = int(r.get("config_min_value") or 1000)
        min_qty = int(r.get("config_min_qty_kg") or 300)
        ntfy_topic = r.get("ntfy_topic") or DEFAULT_NTFY_TOPIC

        add_log(f"Scanning for: {query}")
    except Exception as e:
        return jsonify({"error": f"Redis/Config Error: {e}"}), 500
    
    url = "https://trade.indiamart.com/tradereact/searchpage"
    
    try:
        browser_ua = r.get("user_agent") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        headers = {
            "User-Agent": browser_ua,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": "https://trade.indiamart.com/buyersearch.mp?ss=cocopeat+block"
        }
        cookie = r.get("im_cookie") or os.environ.get("INDIAMART_COOKIE")
        if cookie: headers["Cookie"] = cookie

        payload = {
            "source": "eto.search.lead",
            "q": query,
            "options.start": 0,
            "options.results": 20
        }

        response = requests.post(url, data=payload, headers=headers, timeout=15)
        if response.status_code != 200:
            add_log(f"HTTP Error: {response.status_code}")
            return "Fail", 500
            
        data = response.json()
        results = data.get("results", [])
        
        found_leads = []
        for lead in results:
            fields = lead.get("fields", {})
            display_id = fields.get("displayid")
            
            if not display_id or r.sismember("seen_leads", display_id): continue
            
            title = fields.get("title", "Unknown Product")
            city = fields.get("city", "Unknown")
            isq = fields.get("isqdetails", [])
            
            total_qty = 0
            max_value = 0
            
            for detail in isq:
                if "quantity" in detail.lower(): total_qty = parse_quantity(detail)
                if "value" in detail.lower(): max_value = parse_value(detail)

            if total_qty >= min_qty or max_value >= min_val:
                found_leads.append(display_id)
                href = f"https://trade.indiamart.com/details.mp?offer={display_id}"
                msg = f"📦 {title}\n📍 {city}\n⚖️ {total_qty} KG\n💰 Rs. {max_value}\n🔗 {href}"
                requests.post(f"https://ntfy.sh/{ntfy_topic}", data=msg.encode('utf-8'), headers={"Title": "Lead Match!", "Priority": "5"}, timeout=5)
                r.sadd("seen_leads", display_id)
                
        add_log(f"API Scan OK. Found {len(found_leads)} matches out of {len(results)} leads.")
        return jsonify({"leads_found": len(found_leads), "valid": len(results)})

    except Exception as e:
        add_log(f"Request Exception: {str(e)[:100]}")
        return "Fail", 500

if __name__ == '__main__':
    app.run(debug=True)
