import requests
from bs4 import BeautifulSoup
import os
import time
import sqlite3
import urllib3
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- 0. CONFIGURATION ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.environ.get("AUDIT_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
    print("❌ ERROR: Missing Telegram Keys in GitHub Secrets")
    exit(1)

# --- 1. THE "PLATINUM" REPOSITORY LIST ---
# Includes Tax (Revenue), Labor (Ministry), AND Social Security (Pension/Health)
REPOSITORIES = {
    "India": [
        "https://incometaxindia.gov.in/pages/communications/circulars.aspx",  # CBDT Circulars
        "https://incometaxindia.gov.in/pages/communications/notifications.aspx", # CBDT Notifications
        "https://www.epfindia.gov.in/site_en/Circulars.php", # EPFO (Provident Fund)
        "https://www.epfindia.gov.in/site_en/Office_Use_Circulars.php", # EPFO Internal
        "https://www.esic.gov.in/circulars" # ESIC (Social Insurance)
    ],
    "UAE": [
        "https://www.mohre.gov.ae/en/laws-and-regulations/resolutions-and-circulars.aspx", # MOHRE (Labor)
        "https://tax.gov.ae/en/content/guides.references.aspx" # FTA (Corporate Tax)
    ],
    "Philippines": [
        "https://www.bir.gov.ph/index.php/revenue-issuances/revenue-memorandum-circulars.html", # BIR (Tax)
        "https://www.dole.gov.ph/issuances/labor-advisories/", # DOLE (Labor)
        "https://www.sss.gov.ph/sss/appmanager/viewArticle.jsp?page=circulars", # SSS (Social Security)
        "https://www.philhealth.gov.ph/circulars/", # PhilHealth
        "https://www.pagibigfund.gov.ph/circulars.html" # Pag-IBIG
    ],
    "Kenya": [
        "https://www.kra.go.ke/news-center/public-notices", # KRA (Tax)
        "https://www.nssf.or.ke/tenders-and-notices", # NSSF (Pension)
        "https://sha.go.ke/resources/circulars" # SHA/NHIF (Health)
    ],
    "Nigeria": [
        "https://www.nrs.gov.ng/media--updates/press-releases", # NRS (New Tax)
        "https://www.firs.gov.ng/press-release/", # FIRS (Legacy Tax)
        "https://pencom.gov.ng/category/circulars/", # PENCOM (Pension)
        "https://nsitf.gov.ng/news/" # NSITF (Insurance)
    ],
    "Ghana": [
        "https://gra.gov.gh/practice-notes/", # GRA (Tax)
        "https://www.ssnit.org.gh/news-events/" # SSNIT (Pension)
    ],
    "Uganda": [
        "https://www.ura.go.ug/", # URA (Tax) - Scraped from home
        "https://www.nssfug.org/media/news-and-notices" # NSSF (Pension)
    ],
    "Zambia": [
        "https://www.zra.org.zm/tax-information/tax-information-details/", # ZRA (Tax)
        "https://www.napsa.co.zm/press-releases/" # NAPSA (Pension)
    ],
    "Zimbabwe": [
        "https://www.zimra.co.zw/public-notices", # ZIMRA (Tax)
        "https://www.nssa.org.zw/media-centre/press-releases/" # NSSA (Social Security) - ADDED
    ],
    "South Africa": [
        "https://www.sars.gov.za/legal-counsel/secondary-legislation/public-notices/", # SARS (Tax)
        "https://www.labour.gov.za/DocumentCenter/Pages/Acts.aspx" # Dept of Labour (UIF/Wages)
    ]
}

# --- 2. THE KEYWORDS (EXPANDED FOR SOCIAL SECURITY) ---
KEYWORDS = {
    "India": ["tds", "form 16", "section 192", "epfo", "cbdt", "finance act", "circular no", "notification", "da", "80c", "standard deduction", "pf rate", "esi", "esic", "gratuity", "arrears"],
    "UAE": ["mohre", "wps", "corporate tax", "fta", "iloe", "gpssa", "decree-law", "ministerial resolution", "emiratisation", "nafis", "gratuity", "pension", "contribution"],
    "Philippines": ["bir", "revenue memorandum", "rmc", "labor advisory", "dole", "13th month", "holiday pay", "philhealth", "pag-ibig", "sss", "contribution table", "premium", "msc"],
    "Kenya": ["paye", "kra", "public notice", "finance act", "housing levy", "shif", "nssf", "fringe benefit", "etims", "p9 form", "tier i", "tier ii"],
    "Nigeria": ["paye", "firs", "nrs", "finance act", "tax slab", "consolidated relief", "pencom", "nsitf", "development levy", "wht", "pension reform", "contribution rate"],
    "Ghana": ["gra", "paye", "practice note", "ssnit", "tier 1", "tier 2", "tax relief", "overtime tax", "act 896"],
    "Uganda": ["ura", "paye", "public notice", "lst", "local service tax", "nssf", "efris", "exempt income", "cap"],
    "Zambia": ["zra", "paye", "practice note", "tax band", "napsa", "nhima", "skills development", "sdl", "smart invoice", "ceiling"],
    "Zimbabwe": ["zimra", "public notice", "paye", "tax table", "nssa", "zig", "non-fds", "tarms", "finance act", "aids levy", "pobs", "insurable earnings"],
    "South Africa": ["sars", "paye", "gazette", "interpretation note", "tax tables", "uif", "sdl", "eti", "two-pot", "regulation 28"]
}

# --- 3. DATABASE ---
def init_db():
    conn = sqlite3.connect('payroll_audit_v2.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS audit_log (
        url TEXT PRIMARY KEY,
        country TEXT,
        title TEXT,
        found_at TEXT,
        doc_date TEXT
    )''')
    conn.commit()
    return conn

def is_new(conn, url):
    c = conn.cursor()
    c.execute('SELECT 1 FROM audit_log WHERE url = ?', (url,))
    return c.fetchone() is None

def save(conn, country, title, url, doc_date):
    c = conn.cursor()
    try:
        c.execute('INSERT INTO audit_log VALUES (?, ?, ?, ?, ?)', 
                 (url, country, title, datetime.now().isoformat(), doc_date))
        conn.commit()
    except: pass

# --- 4. SMART FILTER ---
def is_valid_doc(text, url, country):
    text_lower = text.lower()
    url_lower = url.lower()
    
    # TRASH CAN
    garbage = [
        "about us", "contact", "search", "login", "register", "privacy", "sitemap", 
        "home", "read more", "click here", "terms", "policy", "board", "ethics", 
        "career", "tender", "auction", "job", "vacancy", "opportunity",
        "staff", "vision", "mission", "faqs", "help", "manual", "citizen", "charter"
    ]
    
    # Generic headers to skip (too vague)
    if text_lower in ["notifications", "public notices", "circulars", "practice notes", "read more"]:
        return False
    
    if any(g in text_lower for g in garbage): return False
    
    # OFFICIAL CHECK
    # Added "guideline", "directive" for social security
    is_file = any(ext in url_lower for ext in ['.pdf', '.doc', '.docx', '.xlsx'])
    is_official = any(w in text_lower for w in ['circular', 'notification', 'order', 'act', 'bill', 'gazette', 'amendment', 'rules', 'regulation', 'public notice', 'press release', 'practice note', 'advisory', 'resolution', 'memo', 'guideline', 'directive'])
    
    if not (is_file or is_official): return False
    
    # RELEVANCE CHECK
    country_kws = KEYWORDS.get(country, [])
    has_kw = any(kw in text_lower for kw in country_kws)
    is_recent = "2025" in text_lower or "2026" in text_lower
    
    return has_kw or is_recent

# --- 5. UTILITIES (DATE SAFETY FIX) ---
def extract_date(text):
    patterns = [
        r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})',
        r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})',
        r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})',
        r'(\d{8})',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m: return m.group(0)
    return "UNKNOWN" # <--- Returns UNKNOWN instead of failing

def is_6months(date_str):
    if date_str == "UNKNOWN": return True # <--- SAFETY: If no date, KEEP IT.
    try:
        for fmt in ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%B %d, %Y", "%d%m%Y"]:
            try:
                d = datetime.strptime(date_str, fmt)
                cutoff = datetime.now() - timedelta(days=180)
                return d >= cutoff
            except: continue
        return True # Default to True if date parsing is weird but not empty
    except: return True

def send_telegram(msg):
    if len(msg) > 4000: msg = msg[:4000] + "..."
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": msg, 
        "parse_mode": "Markdown", 
        "disable_web_page_preview": True
    }
    try: requests.post(url, json=payload, timeout=10)
    except: pass

def create_session():
    s = requests.Session()
    r = Retry(connect=3, backoff_factor=1, total=3)
    a = HTTPAdapter(max_retries=r)
    s.mount('https://', a)
    return s

# --- 6. MAIN EXECUTION ---
def run_audit():
    print("🚀 PLATINUM PAYROLL AUDIT STARTED")
    conn = init_db()
    session = create_session()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    total_new = 0
    all_reports = {}
    
    for country, urls in REPOSITORIES.items():
        print(f"📍 {country}...")
        findings = []
        
        for url in urls:
            try:
                r = session.get(url, headers=headers, timeout=30, verify=False)
                soup = BeautifulSoup(r.text, 'html.parser')
                links = soup.find_all('a', href=True)
                
                for link in links:
                    text = link.get_text(" ", strip=True)
                    href = link['href']
                    
                    # INDIA JAVASCRIPT FIX
                    if "javascript:OpenWindow" in href:
                        try:
                            parts = href.split("'")
                            clean_path = [p for p in parts if "/" in p and "." in p]
                            if clean_path: href = clean_path[0]
                            else: continue
                        except: continue
                    
                    full_url = urljoin(url, href)
                    
                    if len(text) > 8: 
                        if is_valid_doc(text, full_url, country):
                            if is_new(conn, full_url):
                                doc_date = extract_date(text)
                                # Logic: If date is unknown, we keep it (is_6months returns True)
                                if is_6months(doc_date):
                                    save(conn, country, text, full_url, doc_date)
                                    findings.append({'title': text, 'url': full_url, 'date': doc_date})
                                    total_new += 1
                                    print(f"   ✅ {text[:50]}...")
            except Exception as e:
                print(f"   ⚠️ Error scanning {url}: {str(e)[:40]}")
            
            time.sleep(1)
        
        if findings: all_reports[country] = findings
        time.sleep(1)
    
    # SEND REPORTS
    print("\n📤 Sending reports...")
    for country, items in all_reports.items():
        msg = f"🚨 **{country.upper()} UPDATES**\n\n"
        for item in items[:8]:
            safe_title = item['title'].replace('[', '(').replace(']', ')')
            msg += f"📄 {safe_title}\n📅 {item['date']}\n[🔗 OPEN]({item['url']})\n\n"
        send_telegram(msg)
        time.sleep(2)
    
    if total_new == 0:
        print("No new documents found.")
    else:
        send_telegram(f"✅ **AUDIT COMPLETE**\nFound: *{total_new}* new documents.")
    
    conn.close()

if __name__ == "__main__":
    run_audit()
