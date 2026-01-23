import requests
from bs4 import BeautifulSoup
import os
import time
import sqlite3
import urllib3
from datetime import datetime
from urllib.parse import urljoin
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- 0. CONFIGURATION & SAFETY ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    TELEGRAM_TOKEN = os.environ["AUDIT_BOT_TOKEN"]
    TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
except KeyError:
    print("❌ ERROR: Keys not found! Check GitHub Secrets.")
    exit(1)

# --- 1. THE "BRAIN": KEYWORDS & SOURCES ---
# Derived from "The Digital Sovereignty of Payroll (2025-2026)"

KEYWORDS = {
    "India": [
        "tds", "form 16", "section 192", "epfo", "cbdt", "form 24q", "finance act", 
        "circular no", "notification", "da", "dearness allowance", "section 80c", 
        "section 115bac", "rebate", "standard deduction"
    ],
    "UAE": [
        "mohre", "wps", "wage protection", "corporate tax", "fta", "iloe", "gpssa", 
        "involuntary loss", "decree-law", "cabinet decision", "ministerial resolution", 
        "emiratisation", "nafis", "tax residency"
    ],
    "Philippines": [
        "bir", "revenue memorandum", "rmc", "labor advisory", "dole", "13th month", 
        "holiday pay", "withholding tax", "philhealth", "pag-ibig", "sss", "night shift", 
        "department order", "wage order"
    ],
    "Kenya": [
        "paye", "kra", "public notice", "finance act", "housing levy", "shif", "nssf", 
        "fringe benefit", "tax deduction card", "etims", "p9 form", "legal notice"
    ],
    "Nigeria": [
        "paye", "firs", "nrs", "finance act", "tax slab", "consolidated relief", 
        "rent relief", "pencom", "nsitf", "information circular", "development levy", 
        "wht", "gross income"
    ],
    "Ghana": [
        "gra", "paye", "practice note", "ssnit", "tier 1", "tier 2", "tax relief", 
        "overtime tax", "bonus tax", "income tax amendment", "administrative guideline"
    ],
    "Uganda": [
        "ura", "paye", "public notice", "lst", "local service tax", "nssf", "efris", 
        "tax amendment", "return dt-2008", "exempt income"
    ],
    "Zambia": [
        "zra", "paye", "practice note", "tax band", "napsa", "nhima", "skills development", 
        "sdl", "statutory instrument", "smart invoice"
    ],
    "Zimbabwe": [
        "zimra", "public notice", "paye", "tax table", "nssa", "zig", "non-fds", 
        "final deduction", "tarms", "finance act", "aids levy", "fiscal"
    ],
    "South Africa": [
        "sars", "paye", "gazette", "interpretation note", "tax tables", "medical tax credit", 
        "uif", "sdl", "eti", "two-pot", "regulation 28", "budget speech"
    ]
}

REPOSITORIES = {
    "India": [
        "https://incometaxindia.gov.in/pages/communications/circulars.aspx",
        "https://www.epfindia.gov.in/site_en/Circulars.php"
    ],
    "UAE": [
        "https://www.mohre.gov.ae/en/laws-and-regulations/resolutions-and-circulars.aspx",
        "https://tax.gov.ae/en/content/guides.references.aspx"
    ],
    "Philippines": [
        "https://www.bir.gov.ph/index.php/revenue-issuances/revenue-memorandum-circulars.html",
        "https://www.dole.gov.ph/issuances/labor-advisories/"
    ],
    "Kenya": [
        "https://www.kra.go.ke/news-center/public-notices"
    ],
    "Nigeria": [
        "https://www.firs.gov.ng/press-release/"
        # NRS portal would be added here once stable URL is confirmed
    ],
    "Ghana": [
        "https://gra.gov.gh/practice-notes/"
    ],
    "Uganda": [
        "https://www.ura.go.ug/" # Main portal (scraper looks for notices)
    ],
    "Zambia": [
        "https://www.zra.org.zm/tax-information/tax-information-details/" # Practice Notes tab
    ],
    "Zimbabwe": [
        "https://www.zimra.co.zw/public-notices",
        "https://www.zimra.co.zw/news"
    ],
    "South Africa": [
        "https://www.sars.gov.za/legal-counsel/legal-documents/interpretation-notes/"
    ]
}

# --- 2. THE DATABASE (MEMORY) ---
def init_database():
    conn = sqlite3.connect('payroll_audit.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS audit_log (
        url TEXT PRIMARY KEY,
        country TEXT,
        title TEXT,
        found_at TEXT
    )''')
    conn.commit()
    return conn

def is_new_link(conn, url):
    c = conn.cursor()
    c.execute('SELECT 1 FROM audit_log WHERE url = ?', (url,))
    return c.fetchone() is None

def save_link(conn, country, title, url):
    c = conn.cursor()
    try:
        c.execute('INSERT INTO audit_log VALUES (?, ?, ?, ?)', 
                 (url, country, title, datetime.now().isoformat()))
        conn.commit()
    except: pass

# --- 3. THE "SMART FILTER" LOGIC ---
def is_valid_document(text, url, country):
    text_lower = text.lower()
    url_lower = url.lower()
    
    # GATE 1: THE TRASH CAN (Negative Filter)
    garbage = [
        "about us", "contact", "search", "login", "register", "privacy", "sitemap", 
        "home", "read more", "click here", "terms", "policy", "strategy", "board", 
        "ethics", "vision", "mission", "career", "tender", "auction", "faqs"
    ]
    if any(g in text_lower for g in garbage): return False

    # GATE 2: THE OFFICIAL CHECK (Positive Filter)
    # Must look like a document or an official announcement
    is_file = any(ext in url_lower for ext in ['.pdf', '.doc', '.docx', '.xlsx'])
    is_official = any(word in text_lower for word in ['circular', 'notification', 'order', 'act', 'bill', 'gazette', 'amendment', 'rules', 'regulation', 'public notice', 'press release', 'practice note', 'advisory'])
    
    if not (is_file or is_official):
        return False

    # GATE 3: THE RELEVANCE CHECK (Country Specific)
    # Must contain a keyword from the country's specific list
    # OR mention the current/next fiscal year
    country_specifics = KEYWORDS.get(country, [])
    
    has_keyword = any(kw in text_lower for kw in country_specifics)
    is_recent = "2025" in text_lower or "2026" in text_lower
    
    return has_keyword or is_recent

# --- 4. NETWORK & EXECUTION ---
def send_telegram(message):
    if len(message) > 4000: message = message[:4000] + "..."
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": True}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

def create_session():
    session = requests.Session()
    retry = Retry(connect=3, backoff_factor=1, total=3)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)
    return session

def run_audit():
    print("🚀 STARTING 'DIGITAL SOVEREIGNTY' AUDIT...")
    send_telegram("🚀 **Compliance Dragnet Started**\n_Scanning 10 Sovereign Repositories..._")
    
    conn = init_database()
    session = create_session()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    total_new_docs = 0

    for country, urls in REPOSITORIES.items():
        print(f"\n🔍 Scanning {country}...")
        findings = []
        
        for url in urls:
            try:
                # High timeout for slow govt sites
                r = session.get(url, headers=headers, timeout=30, verify=False)
                soup = BeautifulSoup(r.text, 'html.parser')
                links = soup.find_all('a', href=True)

                for link in links:
                    text = link.get_text(" ", strip=True)
                    href = link['href']
                    
                    # Fix Relative URLs
                    full_url = urljoin(url, href)

                    # Filter: Length > 5 chars to avoid "1", "2", "Next"
                    if len(text) > 5:
                        if is_valid_document(text, full_url, country):
                            if is_new_link(conn, full_url):
                                save_link(conn, country, text, full_url)
                                findings.append(f"📄 [{text}]({full_url})")
                                total_new_docs += 1
            
            except Exception as e:
                print(f"⚠️ Error accessing {url}: {e}")

        # Send Batch Report per Country (Only if new stuff found)
        if findings:
            # Limit to top 8 to avoid Telegram message size limits
            msg = f"🌍 **{country.upper()} UPDATES**\n" + "\n".join(findings[:8])
            send_telegram(msg)
            print(f"   ✅ Sent {len(findings)} updates.")
            time.sleep(2) # Pause to respect Telegram rate limits
        else:
            print(f"   ✓ No new updates.")

    conn.close()
    
    final_msg = f"✅ **Audit Complete.**\nFound {total_new_docs} new critical documents."
    if total_new_docs == 0:
        final_msg += "\n_Repositories checked. No changes since last scan._"
        
    send_telegram(final_msg)

if __name__ == "__main__":
    run_audit()
