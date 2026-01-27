import requests
from bs4 import BeautifulSoup
import os
import time
import sqlite3
import urllib3
import re
import io
from datetime import datetime, timedelta
from urllib.parse import urljoin
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pypdf import PdfReader

# --- 0. CONFIGURATION ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

GEMINI_API_KEY = os.environ.get("GEMINI_KEY")
TELEGRAM_TOKEN = os.environ.get("AUDIT_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not all([GEMINI_API_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
    print("❌ ERROR: Missing Keys! Check GitHub Secrets.")
    exit(1)

# --- 1. REPOSITORIES ---
REPOSITORIES = {
    "India": [
        "https://incometaxindia.gov.in/pages/communications/circulars.aspx",
        "https://incometaxindia.gov.in/pages/communications/notifications.aspx",
        "https://www.epfindia.gov.in/site_en/Circulars.php",
        "https://www.epfindia.gov.in/site_en/Office_Use_Circulars.php",
        "https://www.esic.gov.in/circulars"
    ],
    "UAE": [
        "https://www.mohre.gov.ae/en/laws-and-regulations/resolutions-and-circulars.aspx",
        "https://tax.gov.ae/en/content/guides.references.aspx"
    ],
    "Philippines": [
        "https://www.bir.gov.ph/index.php/revenue-issuances/revenue-memorandum-circulars.html",
        "https://www.dole.gov.ph/issuances/labor-advisories/",
        "https://www.sss.gov.ph/sss/appmanager/viewArticle.jsp?page=circulars",
        "https://www.philhealth.gov.ph/circulars/",
        "https://www.pagibigfund.gov.ph/circulars.html"
    ],
    "Kenya": [
        "https://www.kra.go.ke/news-center/public-notices",
        "https://www.nssf.or.ke/tenders-and-notices",
        "https://sha.go.ke/resources/circulars"
    ],
    "Nigeria": [
        "https://www.nrs.gov.ng/media--updates/press-releases",
        "https://www.firs.gov.ng/press-release/",
        "https://pencom.gov.ng/category/circulars/",
        "https://nsitf.gov.ng/news/"
    ],
    "Ghana": [
        "https://gra.gov.gh/practice-notes/",
        "https://www.ssnit.org.gh/news-events/"
    ],
    "Uganda": [
        "https://www.ura.go.ug/",
        "https://www.nssfug.org/media/news-and-notices"
    ],
    "Zambia": [
        "https://www.zra.org.zm/tax-information/tax-information-details/",
        "https://www.napsa.co.zm/press-releases/"
    ],
    "Zimbabwe": [
        "https://www.zimra.co.zw/public-notices",
        "https://www.nssa.org.zw/media-centre/press-releases/"
    ],
    "South Africa": [
        "https://www.sars.gov.za/legal-counsel/secondary-legislation/public-notices/",
        "https://www.labour.gov.za/DocumentCenter/Pages/Acts.aspx"
    ]
}

# --- 2. KEYWORDS (Pre-Filter) ---
KEYWORDS = {
    "India": ["tds", "form 16", "section 192", "epfo", "cbdt", "finance act", "circular", "notification", "da", "80c", "standard deduction", "pf rate", "esi", "esic", "gratuity", "arrears", "bill"],
    "UAE": ["mohre", "wps", "corporate tax", "fta", "iloe", "gpssa", "decree", "resolution", "emiratisation", "nafis", "gratuity", "pension"],
    "Philippines": ["bir", "revenue", "labor advisory", "dole", "13th month", "holiday", "philhealth", "pag-ibig", "sss", "contribution", "premium"],
    "Kenya": ["paye", "kra", "public notice", "finance act", "housing levy", "shif", "nssf", "fringe benefit", "etims", "tier"],
    "Nigeria": ["paye", "firs", "nrs", "finance act", "tax slab", "relief", "pencom", "nsitf", "levy", "wht", "pension"],
    "Ghana": ["gra", "paye", "practice note", "ssnit", "tier", "tax relief", "overtime", "act"],
    "Uganda": ["ura", "paye", "public notice", "lst", "nssf", "efris", "exempt", "cap"],
    "Zambia": ["zra", "paye", "practice note", "tax band", "napsa", "nhima", "skills", "sdl", "smart invoice"],
    "Zimbabwe": ["zimra", "public notice", "paye", "tax table", "nssa", "zig", "tarms", "finance act", "aids levy"],
    "South Africa": ["sars", "paye", "gazette", "interpretation note", "tax tables", "uif", "sdl", "eti", "two-pot"]
}

# --- 3. DATABASE (V3 - NEW MEMORY) ---
def init_db():
    # 🔴 CHANGED TO V3 TO FORCE A FRESH SCAN 🔴
    conn = sqlite3.connect('payroll_audit_v3.db')
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

# --- 4. UNIVERSAL DOWNLOADER (Safe for Images) ---
def get_universal_content(session, url):
    try:
        r = session.get(url, timeout=20, verify=False, stream=True)
        content_type = r.headers.get('Content-Type', '').lower()
        
        if 'application/pdf' in content_type or url.lower().endswith('.pdf'):
            try:
                f = io.BytesIO(r.content)
                reader = PdfReader(f)
                text = ""
                for page in reader.pages[:2]: 
                    extracted = page.extract_text()
                    if extracted: text += extracted + "\n"
                
                # 🔴 CRITICAL CHECK: If PDF is an image, text will be empty
                if len(text.strip()) < 50:
                    return "EMPTY_PDF_IMAGE"
                return f"PDF CONTENT: {text[:3500]}"
            except: return "PDF_READ_ERROR"

        else:
            soup = BeautifulSoup(r.content, 'html.parser')
            for junk in soup(["script", "style", "nav", "footer", "header", "aside"]): 
                junk.extract()
            text = soup.get_text(separator=' ')
            clean_text = ' '.join(text.split())
            return f"WEB CONTENT: {clean_text[:3500]}"

    except Exception as e: return f"DOWNLOAD_ERROR: {str(e)}"

# --- 5. GEMINI JUDGE (With Title Fallback) ---
def ask_gemini(text, title, country):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    
    # 🔴 FALLBACK: If we couldn't read the file, ask about the TITLE
    if text == "EMPTY_PDF_IMAGE" or "ERROR" in text:
        prompt = f"""
        Role: Payroll Auditor for {country}.
        I cannot read the document content (it might be a scanned image), but here is the TITLE:
        "{title}"
        
        Based on the TITLE ALONE, is this likely a policy update about:
        - Tax / TDS / Rates?
        - Social Security / Pension?
        - Wages / Labor Law?
        - Deadlines?
        
        If YES, reply: "YES (Title Scan): [1 sentence summary]"
        If NO, reply: "SKIP"
        """
    else:
        # Standard Full Content Check
        prompt = f"""
        Role: Payroll Auditor for {country}.
        Title: "{title}"
        Content:
        ---
        {text}
        ---
        Task: Does this contain policy updates on Tax, Payroll, Social Security, or Labor Law?
        If YES: Reply with a 1-sentence summary.
        If NO (tender, meeting, transfer, general news): Reply "SKIP".
        """
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        if response.status_code == 200:
            result = response.json()
            return result['candidates'][0]['content']['parts'][0]['text'].strip()
        return "SKIP"
    except: return "SKIP"

# --- 6. EXECUTION LOGIC ---
def is_valid_doc(text, url, country):
    text_lower = text.lower()
    garbage = ["about us", "contact", "search", "login", "register", "privacy", "sitemap", 
        "home", "read more", "click here", "terms", "policy", "board", "ethics", 
        "career", "tender", "auction", "job", "vacancy", "opportunity",
        "staff", "vision", "mission", "faqs", "help", "manual", "citizen"]
    
    if text_lower in ["notifications", "public notices", "circulars", "practice notes"]: return False
    if any(g in text_lower for g in garbage): return False
    
    is_file = any(ext in url.lower() for ext in ['.pdf', '.doc', '.docx'])
    is_official = any(w in text_lower for w in ['circular', 'notification', 'order', 'act', 'bill', 'gazette', 'amendment', 'rules', 'public notice', 'press release', 'practice note', 'advisory', 'memo'])
    if not (is_file or is_official): return False
    
    country_kws = KEYWORDS.get(country, [])
    has_kw = any(kw in text_lower for kw in country_kws)
    is_recent = "2025" in text_lower or "2026" in text_lower
    
    return has_kw or is_recent

def extract_date(text):
    patterns = [r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})', r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', r'(\d{8})']
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m: return m.group(0)
    return "UNKNOWN"

def is_6months(date_str):
    if date_str == "UNKNOWN": return True 
    try:
        for fmt in ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%B %d, %Y", "%d%m%Y"]:
            try:
                d = datetime.strptime(date_str, fmt)
                cutoff = datetime.now() - timedelta(days=180)
                return d >= cutoff
            except: continue
        return True 
    except: return True

def send_telegram(msg):
    if len(msg) > 4000: msg = msg[:4000] + "..."
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown", "disable_web_page_preview": True}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

def create_session():
    s = requests.Session()
    r = Retry(connect=3, backoff_factor=1, total=3)
    a = HTTPAdapter(max_retries=r)
    s.mount('https://', a)
    return s

def run_audit():
    print("🚀 PLATINUM AI PAYROLL AUDIT STARTED (V3)")
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
                                if is_6months(doc_date):
                                    
                                    print(f"   📥 Checking: {text[:40]}...")
                                    content = get_universal_content(session, full_url)
                                    
                                    # 🔴 DEBUG PRINT
                                    print(f"      Status: {content[:15]}...") 
                                    
                                    ai_analysis = ask_gemini(content, text, country)
                                    
                                    # 🔴 DEBUG PRINT
                                    print(f"      AI Says: {ai_analysis[:50]}...")
                                    
                                    if "SKIP" not in ai_analysis:
                                        save(conn, country, text, full_url, doc_date)
                                        findings.append({
                                            'title': text, 
                                            'url': full_url, 
                                            'date': doc_date,
                                            'insight': ai_analysis
                                        })
                                        total_new += 1
                                    else:
                                        # It was skipped, but save it so we don't check again
                                        save(conn, country, text, full_url, doc_date)
                                    
                                    time.sleep(1)

            except Exception as e:
                print(f"   ⚠️ Error scanning {url}: {str(e)[:40]}")
            
            time.sleep(1)
        
        if findings: all_reports[country] = findings
        time.sleep(1)
    
    print("\n📤 Sending reports...")
    for country, items in all_reports.items():
        msg = f"🚨 **{country.upper()} UPDATES**\n\n"
        for item in items[:6]:
            safe_title = item['title'].replace('[', '(').replace(']', ')')
            msg += f"📄 {safe_title}\n"
            msg += f"💡 {item['insight']}\n"
            msg += f"📅 {item['date']}\n[🔗 OPEN]({item['url']})\n\n"
        send_telegram(msg)
        time.sleep(2)
    
    if total_new == 0:
        print("No new documents found.")
    else:
        send_telegram(f"✅ **AUDIT COMPLETE**\nFound: *{total_new}* updates.")
    
    conn.close()

if __name__ == "__main__":
    run_audit()
