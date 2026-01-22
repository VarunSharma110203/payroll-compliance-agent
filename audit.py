import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
import os
import time
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- 0. CONFIGURATION ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    GENAI_API_KEY = os.environ["GEMINI_KEY"]
    # 👇 This uses the NEW token you just saved
    TELEGRAM_TOKEN = os.environ["AUDIT_BOT_TOKEN"]
    TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
except KeyError:
    print("❌ ERROR: Keys not found! Check GitHub Secrets.")
    exit(1)

genai.configure(api_key=GENAI_API_KEY)
model = genai.GenerativeModel('gemini-pro')

# --- 1. THE DEEP SCAN TARGET LIST (15+ Sources) ---
TARGETS = [
    # === 🇮🇳 INDIA (Simpliance + Govt) ===
    {"c": "India", "auth": "Simpliance Gazettes (Feed)", "url": "https://icm.simpliance.in/gazette-notifications"},
    {"c": "India", "auth": "Income Tax (CBDT)", "url": "https://incometaxindia.gov.in/pages/communications/circulars.aspx"},
    {"c": "India", "auth": "EPFO (Provident Fund)", "url": "https://www.epfindia.gov.in/site_en/Circulars.php"},
    {"c": "India", "auth": "Labour Ministry", "url": "https://labour.gov.in/circulars"},

    # === 🇦🇪 UAE ===
    {"c": "UAE", "auth": "MOHRE (Labour)", "url": "https://www.mohre.gov.ae/en/laws-and-regulations/resolutions-and-circulars.aspx"},
    {"c": "UAE", "auth": "FTA (Tax)", "url": "https://tax.gov.ae/en/taxes/Vat.aspx"},

    # === 🇳🇬 NIGERIA ===
    {"c": "Nigeria", "auth": "FIRS (Tax)", "url": "https://www.firs.gov.ng/press-release/"},
    {"c": "Nigeria", "auth": "PenCom (Pension)", "url": "https://www.pencom.gov.ng/category/regulations-guidelines-circulars-frameworks/circulars/"},

    # === 🇵🇭 PHILIPPINES ===
    {"c": "Philippines", "auth": "BIR (Tax)", "url": "https://www.bir.gov.ph/index.php/revenue-issuances/revenue-memorandum-circulars.html"},
    
    # === 🇰🇪 KENYA ===
    {"c": "Kenya", "auth": "KRA (Tax)", "url": "https://www.kra.go.ke/news-center/public-notices"},

    # === 🇿🇼 ZIMBABWE ===
    {"c": "Zimbabwe", "auth": "ZIMRA", "url": "https://www.zimra.co.zw/public-notices"},

    # === 🇿🇦 SOUTH AFRICA ===
    {"c": "South Africa", "auth": "SARS", "url": "https://www.sars.gov.za/legal-counsel/interpretation-rulings/interpretation-notes/"},
    
    # === 🇿🇲 ZAMBIA ===
    {"c": "Zambia", "auth": "ZRA", "url": "https://www.zra.org.zm/category/media-room/"},
    
    # === 🇺🇬 UGANDA ===
    {"c": "Uganda", "auth": "URA", "url": "https://ura.go.ug/en/publications/public-notices/"}
]

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": message, 
        "parse_mode": "Markdown", 
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=20)
    except:
        pass

def create_session():
    session = requests.Session()
    retry = Retry(connect=3, backoff_factor=1)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)
    return session

def run_audit():
    print("📜 Starting Deep Audit...")
    # Send intro message
    send_telegram("📜 **Deep Compliance Audit Started**\n_Scanning 15+ sources for 6-month history..._")
    
    session = create_session()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}

    for t in TARGETS:
        try:
            print(f"   Scanning {t['c']} - {t['auth']}...")
            
            # 1. FETCH LINKS
            try:
                r = session.get(t['url'], headers=headers, timeout=60, verify=False)
            except:
                print(f"   ⚠️ Skip {t['auth']}")
                continue

            soup = BeautifulSoup(r.text, 'html.parser')
            
            # DEEP SCAN: 40 Links (To cover 6 months)
            links = soup.find_all('a', href=True)[:40]
            data_pile = []

            for link in links:
                text = link.get_text(" ", strip=True)
                url = link['href']
                if len(text) > 10: 
                    data_pile.append(f"- {text} (Link: {url})")

            # 2. AI ANALYSIS
            if data_pile:
                prompt = f"""
                You are a Compliance Auditor.
                Source: {t['auth']} ({t['c']}).
                
                Task: Identify key regulatory changes from late 2025 to 2026.
                - Ignore irrelevant items (Tenders, Holidays, Transfers).
                - Summarize the top 3-5 changes.
                - If nothing major found, mention "No major updates".
                
                Raw Data:
                {str(data_pile[:50])} 

                Output Format:
                🌍 **AUDIT: {t['c'].upper()}** ({t['auth']})
                
                **Key Updates:**
                • [Date/Title] - [Summary]
                """
                
                try:
                    res = model.generate_content(prompt)
                    report = res.text.strip()
                    send_telegram(report)
                    
                    # 🔴 CRITICAL SAFETY PAUSE
                    # We wait 5 seconds between messages so Telegram doesn't block the bot
                    print(f"   ✅ Report sent for {t['c']}")
                    time.sleep(5) 
                    
                except Exception:
                    pass

        except Exception as e:
            print(f"Error {t['c']}: {e}")

    send_telegram("✅ **Audit Complete.**")

if __name__ == "__main__":
    run_audit()
