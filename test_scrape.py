import requests
from bs4 import BeautifulSoup

def test_scrape():
    url = "https://trade.indiamart.com/buyersearch.mp?ss=cocopeat+block&src=as-popular%7Ckwd%3Dcocopeat+blo%7Cpos%3D1%7Ccat%3D-2%7Cmcat%3D-2%7Ckwd_len%3D12%7Ckwd_cnt%3D2"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print(f"Content length: {len(response.text)}")
            with open("debug.html", "w", encoding="utf-8") as f:
                f.write(response.text)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Print some text to see if we are blocked by captcha
            if "Please verify you are a human" in response.text or "Cloudflare" in response.text:
                print("Blocked by anti-bot!")
            
            titles = soup.find_all(['h1', 'h2', 'h3'])
            for t in titles[:5]:
                print(f"Title found: {t.text.strip()}")
            
            # Look for lead cards using correct .TRA_con selector
            leads = soup.select('.TRA_con')
            print(f"Found {len(leads)} leads with .TRA_con")
            
            leads2 = soup.select('a.TRA_link')
            print(f"Found {len(leads2)} links with a.TRA_link")

            for card in leads[:3]:
                style = card.get('style', '')
                if 'hidden' in style or 'height: 0' in style:
                    continue
                link = card.select_one('h3 a.TRA_link')
                title = link.text.strip() if link else "N/A"
                href = link.get('href', '') if link else ''
                
                qty_text = "0"
                val_text = "0"
                details = card.select('.TRA_details')
                if details:
                    qty_labels = details[0].select('span.TRA_qty')
                    values = details[0].select('span.TRA_clg6')
                    for i, lbl in enumerate(qty_labels):
                        label = lbl.get_text(strip=True).lower()
                        val = values[i].get_text(strip=True) if i < len(values) else ""
                        if 'quantity' == label:
                            qty_text = val
                        elif 'probable order value' in label or 'order value' in label:
                            val_text = val
                
                print(f"--- Lead: {title} ---")
                print(f"  Href: {href}")
                print(f"  Qty: {qty_text}, Value: {val_text}")

        else:
            print(f"Failed to fetch. Content snippet: {response.text[:500]}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_scrape()
