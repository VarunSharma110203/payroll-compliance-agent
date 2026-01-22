import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
import os
import time
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- CONFIGURATION ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    GENAI_API_KEY = os.environ["GEMINI_KEY"]
    TELEGRAM_TOKEN = os.environ["AUDIT_BOT_TOKEN"]
    TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
except KeyError:
    print("❌ ERROR: Keys not found!")
    exit(1)

genai.configure(api_key=GENAI_API_KEY)
model = genai.GenerativeModel('gemini-pro')

# --- INTELLIGENT JUNK FILTER ---
# If a link text contains these words, we delete it immediately.
JUNK_KEYWORDS = [
    "tender", "auction", "procurement", "holiday", "calendar", "meeting", "minutes",
    "transfer", "posting", "promotion", "seniority", "list of", "nomination",
    "corrigendum", "extension of date", "contact us", "feedback", "login", 
    "screen reader", "skip to main", "click here", "read more"
]

# --- THE FULL "HEAVY DUTY" TARGET LIST (14 Sources) ---
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
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": True}
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
    print("📜 Starting Full Smart Audit...")
    send_telegram("📜 **Full Smart Compliance Audit Started**\n_Scanning 14 sources & filtering junk..._")
    
    session = create_session()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}

    for t in TARGETS:
        try:
            print(f"   Scanning {t['c']} - {t['auth']}...")
            try:
                # 90s timeout for deep loading
                r = session.get(t['url'], headers=headers, timeout=90, verify=False)
            except:
                send_telegram(f"⚠️ **{t['c']}**: Connection Failed (Blocked).")
                continue

            soup = BeautifulSoup(r.text, 'html.parser')
            
            # 1. GRAB MANY LINKS (60+)
            raw_links = soup.find_all('a', href=True)[:60]
            clean_candidates = []

            for link in raw_links:
                text = link.get_text(" ", strip=True)
                url = link['href']
                text_lower = text.lower()
                
                # 2. APPLY SMART JUNK FILTER
                if len(text) > 10: 
                    is_junk = False
                    for junk in JUNK_KEYWORDS:
                        if junk in text_lower:
                            is_junk = True
                            break
                    
                    if not is_junk:
                        # Fix Relative URLs
                        if not url.startswith("http"):
                             if url.startswith("/"):
                                base_domain = "/".join(t['url'].split("/")[:3])
                                url = base_domain + url
                             else:
                                url = t['url'] + "/" + url
                        
                        clean_candidates.append(f"- {text} (Link: {url})")

            # 3. AI ANALYSIS (Only on the Clean List)
            if clean_candidates:
                # Limit to top 25 CLEAN links to avoid token limits
                final_list_str = "\n".join(clean_candidates[:25])
                
                prompt = f"""
                You are a Senior Compliance Auditor.
                Source: {t['auth']} ({t['c']}).
                
                Here is a filtered list of documents from the government website.
                
                Task:
                1. Identify the **Top 3-5 Most Critical Regulatory Changes** (Tax, Payroll, Wages).
                2. Look for key terms: "Amendment", "Act", "Circular", "Finance Bill".
                3. Summarize them professionally.
                4. **Ignore** anything older than 2024 unless it is a major Act.
                5. If links look like general navigation (e.g. "Sitemap", "Home"), ignore them.
                
                Filtered Data:
                {final_list_str} 

                Output Format (Markdown):
                🌍 **AUDIT: {t['c'].upper()}**
                
                **Critical Updates:**
                • [Date/ID] **[Title]**
                  - [1-sentence summary]
                """
                
                try:
                    res = model.generate_content(prompt)
                    report = res.text.strip()
                    send_telegram(report)
                    print(f"   ✅ Report sent for {t['c']}")
                    time.sleep(5) # Safety pause for Telegram limits
                except Exception as e:
                    send_telegram(f"⚠️ **{t['c']}**: AI Analysis Failed.")
            else:
                 send_telegram(f"⚠️ **{t['c']}**: No relevant documents found after junk filtering.")

        except Exception as e:
            print(f"Error {t['c']}: {e}")

    send_telegram("✅ **Smart Audit Complete.**")

if __name__ == "__main__":
    run_audit()
