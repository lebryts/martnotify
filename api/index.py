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
import sys
from os.path import dirname, abspath, join

# Add root to path so we can import scanner
sys.path.append(dirname(dirname(abspath(__file__))))
try:
    from scanner import run_scan
except ImportError:
    run_scan = None

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
        def ping(self): return True
        def get(self, k): return self.data.get(k)
        def set(self, k, v): self.data[k] = str(v)
        def delete(self, k): 
            if k in self.data: del self.data[k]
        def lpush(self, k, v): 
            if k not in self.data: self.data[k] = []
            if not isinstance(self.data[k], list): self.data[k] = []
            self.data[k].insert(0, v)
        def lrange(self, k, s, e): 
            vals = self.data.get(k, [])
            if not isinstance(vals, list): return []
            return vals[s:e+1 if e != -1 else None]
        def ltrim(self, k, m, n):
            vals = self.data.get(k, [])
            if isinstance(vals, list): self.data[k] = vals[m:n+1 if n != -1 else None]
        def sismember(self, k, v): 
            seen = self.data.get(k, set())
            if not isinstance(seen, set): return False
            return str(v) in seen
        def sadd(self, k, v):
            if k not in self.data: self.data[k] = set()
            if not isinstance(self.data[k], set): self.data[k] = {self.data[k]} if self.data[k] else set()
            self.data[k].add(str(v))
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
        def safe_int(val, default):
            if val is None or str(val).lower() == 'none' or not str(val).strip():
                return default
            try:
                return int(float(str(val).replace(",", "")))
            except:
                return default

        config = {
            "minValue": safe_int(r.get("config_min_value"), 1000),
            "minQtyKg": safe_int(r.get("config_min_qty_kg"), 300),
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

@app.route('/api/clear-logs', methods=['POST'])
def clear_logs():
    try:
        # Re-check connection
        try: r.ping()
        except: pass
        
        # LTRIM 1 0 is the Redis way to empty a list
        r.ltrim("monitor_logs", 1, 0)
        return jsonify({"status": "success"})
    except Exception as e:
        add_log(f"ERROR: Could not clear logs: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/cron', methods=['GET'])
@app.route('/api/scan', methods=['GET'])
def run_cron():
    is_manual = request.args.get('manual') == 'true'
    secret = os.environ.get('CRON_SECRET')
    if secret and not is_manual and request.args.get('secret') != secret:
        return "Unauthorized", 401

    try:
        if r.get("monitor_status") != "true" and not is_manual: 
            return "Monitor is disabled", 200
        
        if is_manual or (secret and request.args.get('secret') == secret):
            # Trigger GitHub Action
            pat = os.environ.get('GH_PAT')
            if not pat:
                if run_scan:
                    add_log("GH_PAT not configured. Running scan locally...")
                    success = run_scan(r_client=r)
                    if success:
                        return jsonify({"status": "completed", "message": "Scan completed locally."})
                    else:
                        return jsonify({"status": "error", "message": "Local scan failed."}), 500
                else:
                    add_log("Trigger Failed: GH_PAT not configured and scanner module not found.")
                    return jsonify({"status": "error", "message": "GH_PAT missing and no local scanner"}), 500
            
            github_url = "https://api.github.com/repos/lebryts/martnotify/actions/workflows/scan.yml/dispatches"
            headers = {
                "Authorization": f"Bearer {pat}",
                "Accept": "application/vnd.github+json"
            }
            res = requests.post(github_url, headers=headers, json={"ref": "main"}, timeout=10)
            if res.status_code == 204:
                add_log(f"Triggered Scan: GitHub Action started via {'Manual' if is_manual else 'External Cron'}.")
                return jsonify({"status": "triggered", "message": "Scan started on GitHub."})
            else:
                add_log(f"Trigger Failed: {res.status_code}")
                return jsonify({"status": "error", "error": res.text}), 500

        return jsonify({"status": "skipped", "message": "No valid trigger."})

    except Exception as e:
        return jsonify({"error": f"Error: {e}"}), 500

@app.route('/api/clear-history', methods=['POST'])
def clear_history():
    try:
        # Re-check connection
        try: r.ping()
        except: pass
        
        r.delete("seen_leads")
        add_log("Matched lead history cleared (all leads are new again).")
        return jsonify({"status": "success"})
    except Exception as e:
        add_log(f"ERROR: Could not clear history: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/processed-leads', methods=['GET'])
def get_processed_leads():
    try:
        # Move up one level from 'api/' to find root
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        fpath = os.path.join(root, "processed_leads.txt")
        if not os.path.exists(fpath):
            return jsonify({"content": "No scan results available yet."})
        
        with open(fpath, "r") as f:
            content = f.read()
        return jsonify({"content": content})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)

@app.route('/api/test-notify', methods=['POST'])
def test_notify():
    try:
        topic = r.get("ntfy_topic") or DEFAULT_NTFY_TOPIC
        requests.post(
            f"https://ntfy.sh/{topic}",
            data="🚀 Test notification from MartNotify!".encode("utf-8"),
            headers={"Title": "MartNotify Test", "Priority": "high"},
            timeout=10
        )
        add_log("Test notification sent.")
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
