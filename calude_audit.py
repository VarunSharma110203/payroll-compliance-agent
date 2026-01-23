import requests
from bs4 import BeautifulSoup
import os
import time
import urllib3
import sqlite3
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urljoin
import re

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    TELEGRAM_TOKEN = os.environ["AUDIT_BOT_TOKEN"]
    TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
except KeyError:
    print("❌ ERROR: Keys not found!")
    exit(1)

# --- DATABASE ---
def init_database():
    conn = sqlite3.connect('payroll_notifications.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY,
        country TEXT,
        title TEXT,
        url TEXT UNIQUE,
        doc_date TEXT,
        found_at TEXT,
        sent_to_telegram BOOLEAN DEFAULT 0,
        source_type TEXT,
        change_type TEXT,
        keywords_matched TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS last_run (
        country TEXT PRIMARY KEY,
        last_run_time TEXT
    )''')
    conn.commit()
    return conn

def get_last_run(conn, country):
    c = conn.cursor()
    c.execute('SELECT last_run_time FROM last_run WHERE country = ?', (country,))
    result = c.fetchone()
    if result:
        return datetime.fromisoformat(result[0])
    return datetime.now() - timedelta(days=30)

def update_last_run(conn, country):
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO last_run (country, last_run_time) VALUES (?, ?)',
              (country, datetime.now().isoformat()))
    conn.commit()

def is_already_sent(conn, url):
    c = conn.cursor()
    c.execute('SELECT id FROM notifications WHERE url = ? AND sent_to_telegram = 1', (url,))
    return c.fetchone() is not None

def save_notification(conn, country, title, url, doc_date, source_type, change_type, keywords):
    c = conn.cursor()
    try:
        c.execute('''INSERT INTO notifications 
                     (country, title, url, doc_date, found_at, source_type, change_type, keywords_matched)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                  (country, title, url, doc_date, datetime.now().isoformat(), source_type, change_type, keywords))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def mark_as_sent(conn, url):
    c = conn.cursor()
    c.execute('UPDATE notifications SET sent_to_telegram = 1 WHERE url = ?', (url,))
    conn.commit()

# --- TELEGRAM ---
def send_telegram(message):
    if len(message) > 4000:
        message = message[:4000] + "\n\n_[truncated]_"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": False}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"⚠️ Telegram: {e}")

# --- SESSION ---
def create_session():
    session = requests.Session()
    retry = Retry(connect=2, backoff_factor=0.5, total=2)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session

# --- DATE EXTRACTION (ENHANCED FOR FILENAMES) ---
def extract_date(text):
    """Extract date from text or filename - looks for patterns like 'January 22, 2026' or '22012026' or '22-01-2026'"""
    patterns = [
        r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})',  # DD/MM/YYYY or DD-MM-YYYY
        r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})',
        r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})',  # YYYY/MM/DD
        r'_(\d{8})_',  # _DDMMYYYY_
        r'_(\d{8})',   # _DDMMYYYY
        r'(\d{8})',    # DDMMYYYY (8 digits)
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            date_str = match.group(1) if match.lastindex >= 1 else match.group(0)
            
            # Convert 8-digit format to readable date
            if len(date_str) == 8 and date_str.isdigit():
                try:
                    dd = date_str[:2]
                    mm = date_str[2:4]
                    yyyy = date_str[4:8]
                    return f"{dd}-{mm}-{yyyy}"
                except:
                    pass
            
            return date_str
    
    return "UNKNOWN"

# --- CHANGE DETECTION ---
CHANGE_KEYWORDS = {
    "India": {
        "rate_change": ["tax slab", "pf rate", "esi rate", "tds", "deduction", "contribution rate", "amended", "revised"],
        "effective_date": ["effective from", "effective date", "w.e.f", "from", "implementation date", "1st", "january", "april"],
        "circular_type": ["circular", "notification", "office memorandum", "om", "finance act"]
    },
    "UAE": {
        "rate_change": ["corporate tax", "emiratisation", "wage protection", "withholding", "gratuity", "amended", "revised"],
        "effective_date": ["effective", "implementation", "from date", "applicable from"],
        "circular_type": ["resolution", "cabinet decision", "ministerial", "federal decree", "circular"]
    },
    "Philippines": {
        "rate_change": ["minimum wage", "13th month", "holiday pay", "premium", "ot rate", "ssa", "philhealth", "amended"],
        "effective_date": ["effective", "effective date", "applicable", "january", "may", "april"],
        "circular_type": ["labor advisory", "rmc", "wage order", "department order"]
    },
    "Kenya": {
        "rate_change": ["paye", "nssf", "fringe benefit", "affordable housing", "tax rate", "amended", "changed"],
        "effective_date": ["effective", "applicable", "from", "january", "quarter"],
        "circular_type": ["public notice", "legal notice", "practice note", "gazette"]
    },
    "Nigeria": {
        "rate_change": ["paye", "withholding tax", "minimum wage", "tax slab", "pensions", "nrs", "amended", "effective"],
        "effective_date": ["effective", "implementation", "from date", "january", "2026"],
        "circular_type": ["information circular", "public notice", "directive", "finance act"]
    },
    "Ghana": {
        "rate_change": ["paye", "ssnit", "tax rate", "overtime", "amended", "revised"],
        "effective_date": ["effective", "applicable", "from date"],
        "circular_type": ["practice note", "gazette notice", "administrative guideline"]
    },
    "Uganda": {
        "rate_change": ["paye", "nssf", "lst", "withholding", "amended", "effective"],
        "effective_date": ["effective", "implementation", "from"],
        "circular_type": ["public notice", "general notice", "practice note"]
    },
    "Zambia": {
        "rate_change": ["paye", "napsa", "tax band", "amended", "revised"],
        "effective_date": ["effective", "applicable", "from"],
        "circular_type": ["practice note", "gazette notice", "statutory instrument"]
    },
    "Zimbabwe": {
        "rate_change": ["paye", "nssa", "aids levy", "fds", "minimum wage", "amended"],
        "effective_date": ["effective", "implementation", "from"],
        "circular_type": ["public notice", "statutory instrument", "finance act"]
    },
    "South Africa": {
        "rate_change": ["paye", "uif", "sdl", "minimum wage", "sectoral determination", "amended"],
        "effective_date": ["effective", "applicable", "from date"],
        "circular_type": ["interpretation note", "regulation", "gazette"]
    }
}

def detect_change(country, title, content=""):
    """Detect if document is a POLICY CHANGE"""
    combined = (title + " " + content).lower()
    
    if country not in CHANGE_KEYWORDS:
        return None, []
    
    keywords = CHANGE_KEYWORDS[country]
    
    # Check if it's an official document type
    is_circular = any(kw in combined for kw in keywords["circular_type"])
    if not is_circular:
        return None, []
    
    # Check for rate/rule changes
    matched_changes = [kw for kw in keywords["rate_change"] if kw in combined]
    if not matched_changes:
        return None, []
    
    # Check for effective date
    has_date = any(kw in combined for kw in keywords["effective_date"]) or extract_date(combined)
    
    change_type = "POLICY_CHANGE" if matched_changes else None
    
    return change_type, matched_changes if has_date else []

# --- FETCH WITH RETRY ---
def fetch_links(session, url, headers, retries=3):
    for attempt in range(retries):
        try:
            r = session.get(url, headers=headers, timeout=15, verify=False)
            soup = BeautifulSoup(r.text, 'html.parser')
            links = soup.find_all('a', href=True)
            
            candidates = []
            for link in links:
                text = link.get_text(" ", strip=True)
                href = link['href']
                
                if len(text) > 3 and "javascript" not in href.lower():
                    full_url = urljoin(url, href)
                    candidates.append({"title": text, "url": full_url})
            
            if candidates:
                return candidates
            
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
    
    return []

# --- REPOSITORIES (FOCUS ON CIRCULARS/NOTICES ONLY) ---
REPOSITORIES = {
    "India": [
        {"type": "Income Tax Circulars", "url": "https://incometaxindia.gov.in/pages/communications/circulars.aspx"},
        {"type": "EPFO Circulars", "url": "https://www.epfindia.gov.in/site_en/circulars.php"},
    ],
    "UAE": [
        {"type": "MOHRE Resolutions", "url": "https://www.mohre.gov.ae/en/laws-and-regulations/resolutions-and-circulars.aspx"},
    ],
    "Philippines": [
        {"type": "DOLE Labor Advisories", "url": "https://www.dole.gov.ph/issuances/labor-advisories/"},
        {"type": "BIR RMC", "url": "https://www.bir.gov.ph/revenue-issuances-details"},
    ],
    "Kenya": [
        {"type": "KRA Public Notices", "url": "https://www.kra.go.ke/news-center/public-notices"},
    ],
    "Nigeria": [
        {"type": "NRS Circulars", "url": "https://www.nrs.gov.ng/"},
    ],
    "Ghana": [
        {"type": "GRA Practice Notes", "url": "https://gra.gov.gh/practice-notes/"},
    ],
    "Uganda": [
        {"type": "URA Public Notices", "url": "https://www.ura.go.ug/"},
    ],
    "Zambia": [
        {"type": "ZRA Practice Notes", "url": "https://www.zra.org.zm/tax-information/tax-information-details/"},
    ],
    "Zimbabwe": [
        {"type": "ZIMRA Public Notices", "url": "https://www.zimra.co.zw/public-notices"},
    ],
    "South Africa": [
        {"type": "SARS Interpretation Notes", "url": "https://www.sars.gov.za/legal-counsel/legal-documents/interpretation-notes/"},
        {"type": "Dept Labour", "url": "https://www.labour.gov.za/DocumentCenter/Pages/Acts.aspx"},
    ],
}

# --- MAIN SCAN ---
def run_full_scan():
    print("🚀 DRAGNET SCAN STARTING...\n")
    send_telegram("🚀 *DRAGNET SCAN INITIATED*\n_Searching for policy changes since last run..._")
    
    conn = init_database()
    session = create_session()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    total_found = 0
    country_reports = {}
    
    for idx, (country, sources) in enumerate(REPOSITORIES.items(), 1):
        print(f"\n[{idx}/10] 📍 {country}...")
        country_findings = []
        last_run = get_last_run(conn, country)
        
        for source in sources:
            print(f"   📄 {source['type']}...")
            
            try:
                links = fetch_links(session, source['url'], headers)
                
                for item in links[:30]:
                    if is_already_sent(conn, item['url']):
                        continue
                    
                    title = item['title']
                    url = item['url']
                    
                    # PRIORITIZE PDFs - they usually contain actual policy documents
                    is_pdf = url.lower().endswith('.pdf')
                    
                    # Detect policy changes ONLY
                    change_type, matched_keywords = detect_change(country, title)
                    
                    if change_type:
                        # Extract date from filename first (for PDFs), then from title
                        doc_date = extract_date(url) if is_pdf else extract_date(title)
                        
                        keywords_str = ", ".join(matched_keywords[:5])
                        
                        if save_notification(conn, country, title, url, doc_date, source['type'], change_type, keywords_str):
                            mark_as_sent(conn, url)
                            country_findings.append({
                                'title': title,
                                'url': url,
                                'date': doc_date,
                                'keywords': keywords_str,
                                'source': source['type'],
                                'is_pdf': is_pdf
                            })
                            total_found += 1
                            pdf_icon = "📄" if is_pdf else "📋"
                            print(f"      ✅ {pdf_icon} CHANGE: {title[:60]}")
                            time.sleep(0.5)
                
            except Exception as e:
                print(f"      ⚠️ {str(e)[:50]}")
            
            time.sleep(1)
        
        if country_findings:
            country_reports[country] = country_findings
        
        update_last_run(conn, country)
        time.sleep(2)
    
    # SEND REPORTS
    print("\n📤 Sending to Telegram...\n")
    
    if country_reports:
        for country, findings in country_reports.items():
            report = f"🚨 *{country.upper()} - POLICY CHANGES DETECTED*\n\n"
            for finding in findings:
                pdf_label = "📄 PDF" if finding['is_pdf'] else "📋 Notice"
                report += f"{pdf_label}\n"
                report += f"*{finding['title'][:75]}*\n"
                report += f"📅 {finding['date']}\n"
                report += f"🔑 {finding['keywords']}\n"
                report += f"[👉 OPEN DOCUMENT]({finding['url']})\n\n"
            send_telegram(report)
            time.sleep(1)
    else:
        send_telegram("✅ No policy changes detected since last run\n(Check again tomorrow)")
    
    summary = f"""✅ *DRAGNET COMPLETE*

📊 *Policy Changes Found*: *{total_found}*
🌍 *Countries Scanned*: *10*
⏱️ *Scan Time*: *{datetime.now().strftime('%H:%M:%S')}*

📍 Next scan: Tomorrow 6 AM UTC

*Important*: Review all flagged changes in your payroll system immediately
"""
    send_telegram(summary)
    print(summary)
    
    conn.close()

if __name__ == "__main__":
    run_full_scan()
