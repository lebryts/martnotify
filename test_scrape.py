import requests
import json

def test_scrape():
    url = "https://trade.indiamart.com/tradereact/getproductlisting"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Referer": "https://trade.indiamart.com/",
        "Origin": "https://trade.indiamart.com",
        "X-Requested-With": "XMLHttpRequest"
    }
    
    # Payload that mimics the browser's search request
    payload = {
        "ss": "cocopeat block",
        "start": 0,
        "limit": 10,
        "source": "desktop"
    }
    
    # Use the cookie from the user's previous message for testing
    cookie = "gcl_au=1.1.1892100071.1776506843; _ga=GA1.1.1659177014.1776506860; _ym_uid=177650686069449348; _ym_d=1776506860; iploc=gcniso%3DIN%7Cgcnnm%3DIndia%7Cgctnm%3DTirupur%7Cgctid%3D%7Cgacrcy%3D200%7Cgip%3D223.237.184.250%7Cgstnm%3DTamil%20Nadu; pop_mthd=FL%3D0%7CDTy%3D1; LGNSTR=0%2C2%2C0%2C1%2C1%2C1%2C1%2C0%2C1; im_iss=t%3DeyJhbGciOiJzaGEyNTYiLCJ0eXAiOiJKV1QifQ.eyJhdWQiOiI4KjgqNyo0KjAqIiwiY2R0IjoiMjEtMDQtMjAyNiIsImV4cCI6MTc3Njg2MTQzNywiaWF0IjoxNzc2Nzc1MDM3LCJpc3MiOiJVU0VSIiwic3ViIjoiMTA4NzMwMjgzIn0.kJpYGuMnaYhuvTHgxY_rz1psq5lJeDfBH73j2HwtSu0; ImeshVisitor=SubUser%3D%7Cadmln%3D0%7Cadmsales%3D0%7Ccd%3D21%2FAPR%2F2026%7Ccmid%3D2%7Cctid%3D73798%7Cem%3Dl%2A%2A%2A%2A%2A%2A%2A%2A%2A%2A%2A%2A%2A%2A%2A%2A%2A%40gmail.com%7Ceotp%3D%7Cev%3DV%7Cfn%3DSiranjeevi%7Cglid%3D108730283%7Ciso%3DIN%7Cmb1%3D8883774409%7Cphcc%3D91%7Custs%3D%7Cutyp%3DP%7Cuv%3DV;"
    headers["Cookie"] = cookie
    
    try:
        print(f"Testing POST request to {url}...")
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"Full JSON Response: {json.dumps(data, indent=2)[:1000]}...") # Print first 1000 chars
            leads = data.get('searchlist', []) or data.get('buyleads', [])
            print(f"Success! Found {len(leads)} leads in JSON response.")
            
            for item in leads[:3]:
                title = item.get('mcatName', "N/A")
                qty = item.get('qtyText', "N/A")
                val = item.get('orderValueText', "N/A")
                print(f"--- Lead: {title} ---")
                print(f"  Qty: {qty}, Value: {val}")
        else:
            print(f"Failed. Snippet: {response.text[:500]}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_scrape()
