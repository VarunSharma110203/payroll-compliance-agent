import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
import os
import time
import urllib.parse

# --- 1. CONFIGURATION ---
try:
    GENAI_API_KEY = os.environ["GEMINI_KEY"]
    TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
    TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
except KeyError:
    print("❌ ERROR: Keys not found! Check GitHub Secrets.")
    exit(1)

genai.configure(api_key=GENAI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# --- 2. THE COMPLETE TARGET LIST (ALL COUNTRIES + EXPANDED INDIA) ---
TARGETS = [
    # === INDIA SUITE ===
    # 1. Income Tax
    {"c": "India", "auth": "Income Tax", "url": "https://incometaxindia.gov.in/pages/communications/notifications.aspx", "base": "https://incometaxindia.gov.in", "kw": ["tax", "tds", "salary", "exemption"]},
    # 2. EPFO
    {"c": "India", "auth": "EPFO", "url": "https://www.epfindia.gov.in/site_en/Circulars.php", "base": "https://www.epfindia.gov.in", "kw": ["contribution", "wage", "interest"]},
    # 3. PFRDA (NPS)
    {"c": "India", "auth": "PFRDA", "url": "https://www.pfrda.org.in/index1.cshtml?lsid=167", "base": "https://www.pfrda.org.in", "kw": ["tier", "withdrawal", "kyc"]},
    # 4. Ministry of Labour
    {"c": "India", "auth": "Ministry of Labour", "url": "https://labour.gov.in/circulars", "base": "https://labour.gov.in", "kw": ["wage", "bonus", "gratuity"]},
    # 5. ESIC
    {"c": "India", "auth": "ESIC", "url": "https://www.esic.gov.in/circulars", "base": "https://www.esic.gov.in", "kw": ["contribution", "benefit", "limit"]},

    # === GLOBAL SUITE ===
    # UAE
    {"c": "UAE", "auth": "MOHRE", "url": "https://www.mohre.gov.ae/en/laws-and-regulations/resolutions-and-circulars.aspx", "base": "https://www.mohre.gov.ae", "kw": ["labour", "wage", "permit"]},
    # NIGERIA
    {"c": "Nigeria", "auth": "FIRS", "url": "https://www.firs.gov.ng/press-release/", "base": "https://www.firs.gov.ng", "kw": ["tax", "circular", "notice"]},
    # KENYA
    {"c": "Kenya", "auth": "KRA", "url": "https://www.kra.go.ke/news-center/public-notices", "base": "https://www.kra.go.ke", "kw": ["tax", "paye"]},
    # PHILIPPINES
    {"c": "Philippines", "auth": "BIR", "url": "https://www.bir.gov.ph/index.php/revenue-issuances/revenue-memorandum-circulars.html", "base": "https://www.bir.gov.ph", "kw": ["tax", "withholding", "bonus"]},
    # ZIMBABWE
    {"c": "Zimbabwe", "auth": "ZIMRA", "url": "https://www.zimra.co.zw/public-notices", "base": "https://www.zimra.co.zw", "kw": ["paye", "tax", "currency"]},
    # SOUTH AFRICA
    {"c": "South Africa", "auth": "SARS", "url": "https://www.sars.gov.za/media/media-releases/", "base": "https://www.sars.gov.za", "kw": ["tax", "tables", "paye"]},
    # UGANDA
    {"c": "Uganda", "auth": "URA", "url": "https://ura.go.ug/en/publications/public-notices/", "base": "https://ura.go.ug", "kw": ["tax", "paye"]}
]

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": False}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"⚠️ Telegram Error: {e}")

def run_scout():
    print("🕵️ Scout Started...")
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}
    
    for t in TARGETS:
        try:
            print(f"Checking {t['c']} ({t['auth']})...")
            r = requests.get(t['url'], headers=headers, timeout=30, verify=False)
            soup = BeautifulSoup(r.text, 'html.parser')
            links = soup.find_all('a', href=True)[:5]
            
            for link in links:
                text = link.get_text(" ", strip=True)
                url = link['href']
                
                if len(text) > 10 and any(k in text.lower() for k in t['kw']):
                    if not url.startswith("http"):
                        base = t['base'].rstrip("/")
                        url = base + url if url.startswith("/") else base + "/" + url

                    news_query = urllib.parse.quote(f"{t['c']} {t['auth']} {text[:30]}")
                    
                    prompt = f"""
                    Role: Payroll Auditor.
                    Item: "{text}" from {t['auth']} ({t['c']})
                    Link: {url}
                    
                    Task:
                    1. IGNORE tenders, transfers, holidays, meetings.
                    2. ALERT ONLY IF it impacts Tax, PF, Wage, Gratuity, or Compliance.
                    
                    If IRRELEVANT -> Reply "SKIP"
                    If RELEVANT -> Reply EXACTLY:
                    🚨 *PAYROLL UPDATE: {t['c'].upper()}*
                    *Subject:* {text}
                    *Impact:* [One sentence summary]
                    *Link:* {url}
                    *News Check:* https://news.google.com/search?q={news_query}
                    """
                    
                    try:
                        res = model.generate_content(prompt)
                        ans = res.text.strip()
                        if "SKIP" not in ans:
                            print(f"   ✅ Alerting {t['c']}")
                            send_telegram(ans)
                            time.sleep(2)
                    except:
                        pass
        except Exception as e:
            print(f"❌ Error {t['c']}: {e}")

if __name__ == "__main__":
    run_scout()
