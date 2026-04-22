import requests
from bs4 import BeautifulSoup

def test_mobile_scrape():
    url = "https://m.indiamart.com/bl/search.php?s=cocopeat+block"
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print(f"Content length: {len(response.text)}")
            with open("mobile_debug.html", "w", encoding="utf-8") as f:
                f.write(response.text)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Check for lead cards on mobile
            # Mobile leads often have 'buylead-card' or similar
            leads = soup.select('.buylead-card, .list-item, [class*="card"]')
            print(f"Found {len(leads)} potential leads")
            for t in soup.find_all(['h1', 'h2', 'h3'])[:5]:
                print(f"Title: {t.text.strip()}")
        else:
            print(f"Failed to fetch.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_mobile_scrape()
