import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
import os
import time
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- 0. SUPPRESS SSL WARNINGS ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 1. CONFIGURATION ---
try:
    GENAI_API_KEY = os.environ["GEMINI_KEY"]
    TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
    TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
    # Detect if this is a Manual Run ("workflow_dispatch") or Automatic ("schedule")
    RUN_MODE = os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch") 
except KeyError:
    print("❌ ERROR: Keys not found! Check GitHub Secrets.")
    exit(1)

genai.configure(api_key=GENAI_API_KEY)

# --- 2. DYNAMIC BRAIN (Self-Healing) ---
try:
    print("🧠 Finding best AI model...")
    found_model = "gemini-pro"
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            if 'gemini-1.5-flash' in m.name:
                found_model = m.name
                break
            elif 'gemini-pro' in m.name:
                found_model = m.name
    print(f"   Selected Model: {found_model}")
    model = genai.GenerativeModel(found_model)
except Exception:
    model = genai.GenerativeModel('gemini-pro')

# --- 3. ROBUST NETWORK (Anti-Block) ---
def create_session():
    session = requests.Session()
    retry = Retry(connect=3, backoff_factor=1)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

# --- 4. TELEGRAM MESSENGER ---
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=30)
    except:
        pass

# --- 5. THE DUAL TARGET LISTS ---

# LIST A: OFFICIAL GOVERNMENT SOURCES (Raw Laws)
GOVT_TARGETS = [
    # 🇮🇳 INDIA
    {"c": "India", "auth": "Income Tax (CBDT)", "url": "https://incometaxindia.gov.in/pages/communications/circulars.aspx", "base": "https://incometaxindia.gov.in", "kw": ["tds", "salary", "192", "form 16", "80c", "exemption"]},
    {"c": "India", "auth": "EPFO", "url": "https://www.epfindia.gov.in/site_en/Circulars.php", "base": "https://www.epfindia.gov.in", "kw": ["interest", "rate", "wage", "ceiling", "contribution", "aadhaar"]},
    {"c": "India", "auth": "Labour Ministry", "url": "https://labour.gov.in/circulars", "base": "https://labour.gov.in", "kw": ["minimum wage", "vda", "bonus", "gratuity"]},
    
    # 🇦🇪 UAE
    {"c": "UAE", "auth": "MOHRE", "url": "https://www.mohre.gov.ae/en/laws-and-regulations/resolutions-and-circulars.aspx", "base": "https://www.mohre.gov.ae", "kw": ["emiratisation", "quota", "wps", "work permit"]},
    {"c": "UAE", "auth": "FTA", "url": "https://tax.gov.ae/en/taxes/Vat.aspx", "base": "https://tax.gov.ae", "kw": ["corporate tax", "employment", "income", "salary"]},

    # 🇳🇬 NIGERIA
    {"c": "Nigeria", "auth": "FIRS", "url": "https://www.firs.gov.ng/press-release/", "base": "https://www.firs.gov.ng", "kw": ["paye", "wht", "relief", "personal income"]},
    {"c": "Nigeria", "auth": "PenCom", "url": "https://www.pencom.gov.ng/category/regulations-guidelines-circulars-frameworks/circulars/", "base": "https://www.pencom.gov.ng", "kw": ["pension", "contribution", "rate", "voluntary"]},

    # 🇵🇭 PHILIPPINES
    {"c": "Philippines", "auth": "BIR", "url": "https://www.bir.gov.ph/index.php/revenue-issuances/revenue-memorandum-circulars.html", "base": "https://www.bir.gov.ph", "kw": ["withholding", "tax", "alphalist", "13th month"]},
    
    # 🇰🇪 KENYA
    {"c": "Kenya", "auth": "KRA", "url": "https://www.kra.go.ke/news-center/public-notices", "base": "https://www.kra.go.ke", "kw": ["housing levy", "ahl", "paye", "tax", "shif"]},
    
    # 🇿🇼 ZIMBABWE
    {"c": "Zimbabwe", "auth": "ZIMRA", "url": "https://www.zimra.co.zw/public-notices", "base": "https://www.zimra.co.zw", "kw": ["paye", "tax table", "zig", "usd"]},
    
    # 🇿🇦 SOUTH AFRICA
    {"c": "South Africa", "auth": "SARS", "url": "https://www.sars.gov.za/legal-counsel/interpretation-rulings/interpretation-notes/", "base": "https://www.sars.gov.za", "kw": ["paye", "uif", "sdl", "eti"]},
    
    # 🇺🇬 UGANDA
    {"c": "Uganda", "auth": "URA", "url": "https://ura.go.ug/en/publications/public-notices/", "base": "https://ura.go.ug", "kw": ["paye", "amnesty", "tax ledger"]},

    # 🇿🇲 ZAMBIA
    {"c": "Zambia", "auth": "ZRA", "url": "https://www.zra.org.zm/category/media-room/", "base": "https://www.zra.org.zm", "kw": ["practice note", "paye threshold", "tax credit"]}
]

# LIST B: SIMPLIANCE AGGREGATOR (Detailed State Tracking)
SIMPLIANCE_TARGETS = [
    # 1. THE DAILY FEED (For specific rate changes)
    {
        "c": "India", "auth": "Simpliance (Gazettes)", 
        "url": "https://icm.simpliance.in/gazette-notifications", 
        "base": "https://icm.simpliance.in", 
        "kw": ["pt", "professional tax", "lwf", "welfare fund", "holiday", "wage", "bonus"]
    },
    # 2. THE HUB PAGES (To detect new states/structural changes)
    {
        "c": "India", "auth": "Simpliance (PT Hub)", 
        "url": "https://www.simpliance.in/India/LEI/professional_tax", 
        "base": "https://www.simpliance.in", 
        "kw": ["act", "rule", "slab", "state"] 
    },
    {
        "c": "India", "auth": "Simpliance (LWF Hub)", 
        "url": "https://www.simpliance.in/India/LEI/labour_welfare_fund", 
        "base": "https://www.simpliance.in", 
        "kw": ["act", "contribution", "state", "deduction"]
    },
    {
        "c": "India", "auth": "Simpliance (Holiday Hub)", 
        "url": "https://www.simpliance.in/India/LEI/nfh", 
        "base": "https://www.simpliance.in", 
        "kw": ["national", "festival", "holiday", "leave"]
    }
]

# --- 6. THE INTELLIGENT SCOUT (DUAL ENGINE) ---
def run_scout():
    print("🕵️ Dual-Engine Scout Started...")
    
    # --- LOGIC: ONLY SEND "SYSTEM ONLINE" MSG ON MANUAL RUN ---
    if RUN_MODE == "workflow_dispatch":
        intro_msg = (
            "✅ **SYSTEM STATUS: ONLINE**\n\n"
            "🤖 **Dual-Engine Active:**\n"
            "1. **Govt Watchdog:** Tracking 9 Countries (India, UAE, Nigeria, etc.)\n"
            "2. **Simpliance Intel:** Tracking PT, LWF, & NFH State Changes.\n\n"
            "🔄 *Running initial scan now...*"
        )
        send_telegram(intro_msg)
    # -----------------------------------------------------------
    
    session = create_session()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}
    
    total_checked = 0
    alerts_found = 0

    # === ENGINE 1: SIMPLIANCE SCAN ===
    for t in SIMPLIANCE_TARGETS:
        try:
            print(f"🔎 Scanning {t['auth']}...")
            total_checked += 1
            r = session.get(t['url'], headers=headers, timeout=60, verify=False)
            soup = BeautifulSoup(r.text, 'html.parser')
            
            # Grab all links from the page
            links = soup.find_all('a', href=True)[:15] 
            
            for link in links:
                text = link.get_text(" ", strip=True)
                url = link['href']
                if not url.startswith("http"):
                    base = t['base'].rstrip("/")
                    url = base + url if url.startswith("/") else base + "/" + url

                # Broad Keyword Check
                if len(text) > 5 and any(k in text.lower() for k in t['kw']):
                    
                    # SIMPLIANCE PROMPT
                    prompt = f"""
                    Source: Simpliance Aggregator
                    Page Category: {t['auth']}
                    Link Text: "{text}"
                    URL: {url}
                    
                    Task: Is this a Regulatory Update or a New State being added?
                    - If it's just a generic menu item (e.g. "About Us", "Contact"), reply SKIP.
                    - If it indicates a NEW State or a CHANGE in Tax/LWF/Holidays, reply ALERT.
                    
                    Reply EXACTLY:
                    📢 *SIMPLIANCE INTEL*
                    *Category:* {t['auth']}
                    *Update:* {text}
                    *Action:* Check Simpliance Dashboard
                    *Link:* {url}
                    """
                    
                    try:
                        res = model.generate_content(prompt)
                        ans = res.text.strip()
                        if "SKIP" not in ans:
                            print(f"   📢 Simpliance Found: {text[:30]}")
                            send_telegram(ans)
                            alerts_found += 1
                    except:
                        pass
        except Exception as e:
            print(f"   ⚠️ Simpliance Error: {e}")

    # === ENGINE 2: GOVT SCAN ===
    for t in GOVT_TARGETS:
        try:
            print(f"🛡️ Checking {t['c']} - {t['auth']}...")
            total_checked += 1
            r = session.get(t['url'], headers=headers, timeout=90, verify=False)
            soup = BeautifulSoup(r.text, 'html.parser')
            links = soup.find_all('a', href=True)[:8]
            
            for link in links:
                text = link.get_text(" ", strip=True)
                url = link['href']
                if not url.startswith("http"):
                    base = t['base'].rstrip("/")
                    url = base + url if url.startswith("/") else base + "/" + url

                if len(text) > 10 and any(k in text.lower() for k in t['kw']):
                    # GOVT PROMPT
                    prompt = f"""
                    Source: Official Govt Website
                    Authority: {t['auth']} ({t['c']})
                    Title: "{text}"
                    Link: {url}
                    Task: Critical Payroll Regulatory Change?
                    Reply "SKIP" if irrelevant.
                    Reply EXACTLY:
                    🚨 *OFFICIAL GOVT ALERT: {t['c'].upper()}*
                    *Authority:* {t['auth']}
                    *Update:* {text}
                    *Action:* Update System Configuration
                    *Link:* {url}
                    """
                    try:
                        res = model.generate_content(prompt)
                        ans = res.text.strip()
                        if "SKIP" not in ans:
                            print(f"   🚨 Govt Alert: {t['c']}")
                            send_telegram(ans)
                            alerts_found += 1
                    except:
                        pass
        except Exception:
            pass

    # REPORT (Only send "All Clear" on Manual Run, keep quiet on Auto run to avoid spam)
    if RUN_MODE == "workflow_dispatch":
        if alerts_found == 0:
            send_telegram(f"✅ *Manual Scan Complete.*\nChecked {total_checked} sources.\nNo new critical updates.")
        else:
            send_telegram(f"✅ *Manual Scan Complete.*\nFound {alerts_found} alerts.")

if __name__ == "__main__":
    run_scout()
