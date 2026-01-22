import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
import os
import time
import io
import urllib3
from pypdf import PdfReader
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

# --- TARGETS ---
TARGETS = [
    # 🇿🇼 ZIMBABWE (Explicit Focus on FDS/Fiscal)
    {"c": "Zimbabwe", "auth": "ZIMRA Public Notices", "url": "https://www.zimra.co.zw/public-notices"},
    
    # 🇳🇬 NIGERIA (Explicit Focus on Tax Slabs)
    {"c": "Nigeria", "auth": "FIRS Press & Circulars", "url": "https://www.firs.gov.ng/press-release/"},
    
    # 🌍 GLOBAL OTHERS
    {"c": "Philippines", "auth": "BIR", "url": "https://www.bir.gov.ph/index.php/revenue-issuances/revenue-memorandum-circulars.html"},
    {"c": "India", "auth": "Income Tax", "url": "https://incometaxindia.gov.in/pages/communications/circulars.aspx"},
    {"c": "India", "auth": "EPFO", "url": "https://www.epfindia.gov.in/site_en/Circulars.php"},
    {"c": "UAE", "auth": "MOHRE", "url": "https://www.mohre.gov.ae/en/laws-and-regulations/resolutions-and-circulars.aspx"},
    {"c": "Kenya", "auth": "KRA", "url": "https://www.kra.go.ke/news-center/public-notices"},
    {"c": "South Africa", "auth": "SARS", "url": "https://www.sars.gov.za/legal-counsel/interpretation-rulings/interpretation-notes/"},
    {"c": "Uganda", "auth": "URA", "url": "https://ura.go.ug/en/publications/public-notices/"}
]

# --- THE "KILLER" KEYWORDS ---
# The bot will prioritize ANY link containing these words
PRIORITY_KEYWORDS = [
    # Universal High Value
    "tax", "finance act", "amendment", "slab", "rate", "wage", "salary", 
    "circular", "regulation", "bill", "gazette", "compliance", "levy", "duty", 
    # Zimbabwe Specific
    "fds", "fiscal", "device", "non-fds", "currency", "ziq",
    # Nigeria Specific
    "finance", "exemption", "relief", "deduction"
]

def send_telegram(message):
    if len(message) > 4000: message = message[:4000] + "..."
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": True}
    try: requests.post(url, json=payload, timeout=20)
    except: pass

def create_session():
    session = requests.Session()
    retry = Retry(connect=3, backoff_factor=1)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)
    return session

def get_content_from_url(session, url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}
        r = session.get(url, headers=headers, timeout=25, verify=False) # Increased timeout
        content_type = r.headers.get('Content-Type', '').lower()
        
        if 'pdf' in content_type or url.lower().endswith('.pdf'):
            try:
                f = io.BytesIO(r.content)
                reader = PdfReader(f)
                text = ""
                # Read 3 pages to capture hidden details
                for page in reader.pages[:3]: 
                    text += page.extract_text() + "\n"
                return f"PDF_TEXT: {text[:2500]}"
            except: return "ERROR_READING_PDF"
        else:
            soup = BeautifulSoup(r.text, 'html.parser')
            for s in soup(["script", "style"]): s.extract()
            return f"WEB_TEXT: {soup.get_text()[:2500]}"
    except Exception as e: return f"DOWNLOAD_ERROR: {str(e)}"

def run_audit():
    print("📜 Starting HUNTER Scan...")
    send_telegram("🚨 **HUNTER MODE ACTIVATED**\n_Deep scanning 60 links/site. Hunting for 'Tax Slabs', 'FDS', 'Acts'..._")
    
    session = create_session()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}

    for t in TARGETS:
        try:
            print(f"   Scanning {t['c']}...")
            try: r = session.get(t['url'], headers=headers, timeout=60, verify=False)
            except: 
                send_telegram(f"⚠️ **{t['c']}**: Site Unreachable.")
                continue

            soup = BeautifulSoup(r.text, 'html.parser')
            links = soup.find_all('a', href=True)
            
            # 1. SCAN DEEP (Top 60 Links)
            candidates = []
            # We look at the first 60 links to catch updates buried deep
            for link in links[:60]: 
                text = link.get_text(" ", strip=True)
                url = link['href']
                
                # Filter out junk (short text)
                if len(text) > 4 and "javascript" not in url:
                    # Fix URL
                    if not url.startswith("http"):
                        if url.startswith("/"): url = "/".join(t['url'].split("/")[:3]) + url
                        else: url = t['url'].rsplit('/', 1)[0] + "/" + url
                    
                    # 2. INTELLIGENT SCORING
                    score = 0
                    text_lower = text.lower()
                    
                    # Bonus points for Keywords
                    for word in PRIORITY_KEYWORDS:
                        if word in text_lower:
                            score += 10 
                    
                    # Zimbabwe Special: If it mentions FDS, huge boost
                    if t['c'] == "Zimbabwe" and ("fds" in text_lower or "fiscal" in text_lower):
                        score += 50
                    
                    # Keep if it has keywords OR is very recent (top 5 on page)
                    if score > 0 or len(candidates) < 5:
                        candidates.append({"title": text, "url": url, "score": score})

            # Sort by Priority (Highest score first)
            candidates = sorted(candidates, key=lambda x: x['score'], reverse=True)
            
            # Read the Top 8 Highest Priority items
            top_targets = candidates[:8]
            
            if not top_targets:
                send_telegram(f"⚠️ **{t['c']}**: Scanned 60 links. No keywords found.")
                continue

            findings = []
            for item in top_targets:
                # 3. DEEP READ CONTENT
                content = get_content_from_url(session, item['url'])
                if "ERROR" in content: continue

                prompt = f"""
                Role: Senior Compliance Auditor.
                Document: "{item['title']}"
                Content Snippet: {content}
                
                Task:
                1. Does this contain updates on **Tax Rates**, **Slabs**, **Finance Act**, **Wages**, or **ZIMRA FDS/Fiscal Devices**?
                2. If YES, summarize the specific numbers/changes.
                3. If it is routine/junk, reply "SKIP".
                
                Output: [Date/Type] [Summary]
                """
                
                try:
                    res = model.generate_content(prompt)
                    ans = res.text.strip()
                    if "SKIP" not in ans:
                        findings.append(f"🔴 **PRIORITY UPDATE**\n[{item['title']}]({item['url']})\n{ans}")
                        time.sleep(2)
                except: pass

            if findings:
                report = f"🌍 **HUNTER RESULT: {t['c'].upper()}**\n" + "\n\n".join(findings)
                send_telegram(report)
                time.sleep(4)
            else:
                send_telegram(f"✅ **{t['c']}**: Scanned 60 links. Checked top {len(top_targets)} priority docs. No Critical Updates found.")

        except Exception as e:
            print(f"Error {t['c']}: {e}")

    send_telegram("✅ **Hunter Scan Complete.**")

if __name__ == "__main__":
    run_audit()
