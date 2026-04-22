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

# Use absolute path for static folder inside the api directory for Vercel bundling
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, 'static')

app = Flask(__name__, static_folder=STATIC_DIR)
CORS(app)

@app.before_request
def log_request_info():
    # This will show up in Vercel logs
    print(f"Request: {request.method} {request.path}")

# --- CONFIGURATION & REDIS ---
DEFAULT_NTFY_TOPIC = os.environ.get('NTFY_TOPIC', 'indiamart_leads')
REDIS_URL = os.environ.get('REDIS_URL')

r = None
if REDIS_URL and REDIS_URL.strip():
    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        # Test connection
        r.ping()
    except Exception as e:
        print(f"Redis Connection Error: {e}. Falling back to MockRedis.")
        r = None

if not r:
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
        def ping(self): return True
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
    if not SELENIUM_AVAILABLE or os.environ.get('VERCEL'):
        add_log("Selenium not available in this environment.")
        return None
    
    add_log("Starting Selenium...")
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
    index_path = os.path.join(app.static_folder, 'index.html')
    if not os.path.exists(index_path):
        return f"Error: index.html not found at {index_path}. Static folder is {app.static_folder}", 404
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/<path:path>')
def serve_static(path): 
    return send_from_directory(app.static_folder, path)

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
            "ntfyTopic": ntfy_topic
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
    add_log("Configuration updated.")
    return jsonify({"success": True})

@app.route('/api/toggle', methods=['POST'])
def toggle_monitor():
    data = request.json
    enable = data.get('enable', False)
    r.set("monitor_status", "true" if enable else "false")
    add_log(f"Monitor {'started' if enable else 'stopped'}.")
    return jsonify({"isRunning": enable})

@app.route('/api/cron', methods=['GET'])
def run_cron():
    is_manual = request.args.get("manual") == "true"
    cron_secret = os.environ.get('CRON_SECRET', '')
    provided_secret = request.args.get("secret", "")
    
    if not is_manual and cron_secret and provided_secret != cron_secret:
        return "Unauthorized", 401
    if not is_manual and r.get("monitor_status") != "true": return "Disabled", 200

    query = r.get("config_search_query") or "cocopeat block"
    min_val = int(r.get("config_min_value") or 1000)
    min_qty = int(r.get("config_min_qty_kg") or 300)
    ntfy_topic = r.get("ntfy_topic") or DEFAULT_NTFY_TOPIC

    add_log(f"Scanning for: {query}")
    r.set("last_check_time", datetime.now().strftime('%H:%M:%S'))
    
    # The JSON API is much harder for them to block than the HTML page
    url = f"https://miscreact.indiamart.com/buyersearch/buyersearchlist?ss={query.replace(' ', '+')}&start=0&limit=40"
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://trade.indiamart.com/",
            "Origin": "https://trade.indiamart.com"
        }
        cookie = r.get("im_cookie") or os.environ.get("INDIAMART_COOKIE")
        if cookie: headers["Cookie"] = cookie

        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code != 200:
            add_log(f"❌ IndiaMart API Error {resp.status_code}. Refresh Cookie.")
            return jsonify({"status": "error"}), 200

        data = resp.json()
        leads = data.get('searchlist', []) or data.get('buyleads', [])
        
        if not leads:
            add_log("Scan complete. No leads found in API response.")
            return jsonify({"status": "ok", "matches": 0})

        found = 0
        for item in leads:
            id = item.get('displayId') or item.get('offerId')
            if not id or r.sismember("seen_leads", id): continue
            
            qty_t = item.get('qtyText', "0")
            val_t = item.get('orderValueText', "0")
            
            if parse_quantity(qty_t) >= min_qty or parse_value(val_t) >= min_val:
                found += 1
                title = item.get('mcatName', "New Lead")
                href = f"https://trade.indiamart.com/details.mp?offer={id}"
                msg = f"📦 {title}\n⚖️ {qty_t}\n💰 {val_t}\n🔗 {href}"
                requests.post(f"https://ntfy.sh/{ntfy_topic}", data=msg.encode('utf-8'), headers={"Title": "Lead Match!"})
                r.sadd("seen_leads", id)

        add_log(f"Scan complete. Found {found} new matching leads.")
        return jsonify({"status": "ok", "matches": found})

    except Exception as e:
        add_log(f"Scan Error: {str(e)[:50]}")
        return jsonify({"status": "error"}), 200

if __name__ == '__main__':
    app.run(debug=True)
