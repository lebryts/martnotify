import requests
import re
import json

def test_session_scrape():
    s = requests.Session()
    # 1. Visit home page to get initial cookies
    home_url = "https://trade.indiamart.com/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    }
    
    print("Visiting home page...")
    r1 = s.get(home_url, headers=headers, timeout=15)
    print(f"Home page status: {r1.status_code}, Cookies: {s.cookies.get_dict()}")
    
    # 2. Visit search page
    search_url = "https://trade.indiamart.com/buyersearch.mp?ss=cocopeat+block"
    print("Visiting search page...")
    r2 = s.get(search_url, headers=headers, timeout=15)
    print(f"Search page status: {r2.status_code}, Length: {len(r2.text)}")
    
    if "window.__INITIAL_STATE__" in r2.text:
        print("Found __INITIAL_STATE__!")
        # Try to see if it has data
        if '"searchlist":[]' in r2.text:
            print("But searchlist is EMPTY. IndiaMart is withholding data.")
        else:
            print("Data is PRESENT in HTML!")
    else:
        print("__INITIAL_STATE__ NOT FOUND in HTML.")

if __name__ == "__main__":
    test_session_scrape()
