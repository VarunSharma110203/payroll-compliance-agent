import requests
from bs4 import BeautifulSoup
import os
import time
import sqlite3
import urllib3
import re
import io
import json
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

# --- 2. DATABASE (V5 - FRESH SCAN) ---
def init_db():
    conn = sqlite3.connect('payroll_audit_v5.db')
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

# --- 3. UNIVERSAL DOWNLOADER ---
def get_universal_content(session, url):
    try:
        # Stream=True allows checking headers before downloading
        r = session.get(url, timeout=20, verify=False, stream=True)
        content_type = r.headers.get('Content-Type', '').lower()
        
        # CASE A: PDF Handling
        if 'application/pdf' in content_type or url.lower().endswith('.pdf'):
            try:
                f = io.BytesIO(r.content)
                reader = PdfReader(f)
                text = ""
                # Read first 2 pages
                for page in reader.pages[:2]: 
                    extracted = page.extract_text()
                    if extracted: text += extracted + "\n"
                
                # If text is extremely short (< 100 chars), treat as Image PDF
                if len(text.strip()) < 100:
                    return "EMPTY_PDF_IMAGE"
                    
                return f"PDF CONTENT: {text[:3500]}"
            except: return "PDF_READ_ERROR"

        # CASE B: Webpage Handling
        else:
            soup = BeautifulSoup(r.content, 'html.parser')
            # Clean up the HTML
            for junk in soup(["script", "style", "nav", "footer", "header", "aside"]): 
                junk.extract()
            text = soup.get_text(separator=' ')
            clean_text = ' '.join(text.split())
            return f"WEB CONTENT: {clean_text[:3500]}"

    except Exception as e: return f"DOWNLOAD_ERROR: {str(e)}"

# --- 4. DYNAMIC GEMINI JUDGE ---
def ask_gemini(text, title, country):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    
    # 🔴 DYNAMIC ROUTING 🔴
    # If the file is unreadable (Image PDF) or broken, we switch to "Title Analysis"
    if text == "EMPTY_PDF_IMAGE" or "ERROR" in text:
        prompt = f"""
        Role: Payroll Compliance Auditor for {country}.
        I cannot read the document content (it is a scanned image).
        
        Task: Analyze the TITLE below. Based on your knowledge of payroll and tax terminology, does this title suggest a regulatory update?
        
        Title: "{title}"
        
        Relevant Topics:
        - Income Tax / Corporate Tax / VAT / Levies
        - Social Security / Pension / Provident Fund
        - Labor Law / Wages / Allowances
        - Statutory Returns / Deadlines / Compliance
        
        If the title strongly implies ANY of these topics, reply YES.
        If it looks like a tender, job vacancy, or internal meeting, reply SKIP.
        
        Reply Format: "YES (Title Scan): [Short Summary]" OR "SKIP"
        """
    else:
        # If we have content, we check both Content AND Title
        prompt = f"""
        Role: Payroll Compliance Auditor for {country}.
        Title: "{title}"
        Content Snippet:
        ---
        {text}
        ---
        
        Task: Is this document a relevant regulatory update for Payroll, Tax, or Labor Law?
        
        Instructions:
        1. Analyze the Content Snippet for rules about taxes, wages, or compliance.
        2. CRITICAL: If the Content is vague but the TITLE is highly specific (e.g. "Finance Act", "Fringe Benefit Tax", "New Rates"), prioritize the Title and assume it is relevant.
        
        If YES: Reply with a 1-sentence summary.
        If NO (tender, meeting, transfer, general news): Reply "SKIP".
        """
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        return "SKIP"
    except: return "SKIP"

# --- 5. TRASH FILTER (Only removing obvious junk) ---
def is_valid_doc(text, url):
    text_lower = text.lower()
    
    # Only filter out pure administrative junk. Let AI decide the rest.
    garbage = ["about us", "contact", "search", "login", "register", "privacy", "sitemap", 
        "home", "read more", "click here", "terms", "policy", "board", "ethics", 
        "career", "tender", "auction", "job", "vacancy", "opportunity",
        "staff", "vision", "mission", "faqs", "help", "manual", "citizen"]
    
    if text_lower in ["notifications", "public notices", "circulars", "practice notes"]: return False
    if any(g in text_lower for g in garbage): return False
    
    # If it looks like a file OR an official update, we check it.
    is_file = any(ext in url.lower() for ext in ['.pdf', '.doc', '.docx', '.xlsx'])
    is_official = any(w in text_lower for w in ['circular', 'notification', 'order', 'act', 'bill', 'gazette', 'amendment', 'rules', 'regulation', 'public notice', 'press release', 'practice note', 'advisory', 'resolution', 'memo', 'guideline', 'directive'])
    
    return is_file or is_official

# --- 6. UTILITIES ---
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

# --- 7. MAIN EXECUTION ---
def run_audit():
    print("🚀 DYNAMIC AI PAYROLL AUDIT STARTED (V5)")
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
                        except: continue
                    
                    full_url = urljoin(url, href)
                    
                    if len(text) > 8: 
                        # 1. TRASH CHECK ONLY (No keywords)
                        if is_valid_doc(text, full_url):
                            if is_new(conn, full_url):
                                doc_date = extract_date(text)
                                if is_6months(doc_date):
                                    
                                    print(f"   📥 Checking: {text[:40]}...")
                                    content = get_universal_content(session, full_url)
                                    
                                    # 2. ASK GEMINI (Dynamic Judging)
                                    ai_analysis = ask_gemini(content, text, country)
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
