import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
import os
import time
import io
import urllib3
import json
import sqlite3
from datetime import datetime, timedelta
from pypdf import PdfReader
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urljoin, urlparse

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

# --- DATABASE SETUP ---
def init_database():
    conn = sqlite3.connect('payroll_notifications.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY,
        country TEXT,
        title TEXT,
        url TEXT UNIQUE,
        content_date TEXT,
        found_at TEXT,
        sent_to_telegram BOOLEAN DEFAULT 0,
        source_type TEXT
    )''')
    conn.commit()
    return conn

def is_already_sent(conn, url):
    c = conn.cursor()
    c.execute('SELECT id FROM notifications WHERE url = ? AND sent_to_telegram = 1', (url,))
    return c.fetchone() is not None

def save_notification(conn, country, title, url, content_date, source_type):
    c = conn.cursor()
    try:
        c.execute('''INSERT INTO notifications (country, title, url, content_date, found_at, source_type)
                     VALUES (?, ?, ?, ?, ?, ?)''',
                  (country, title, url, content_date, datetime.now().isoformat(), source_type))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def mark_as_sent(conn, url):
    c = conn.cursor()
    c.execute('UPDATE notifications SET sent_to_telegram = 1 WHERE url = ?', (url,))
    conn.commit()

# --- DATE PARSING ---
def extract_date_from_text(text):
    """Extract date from text using AI"""
    try:
        prompt = f"""Extract the DATE from this text. Return ONLY the date in YYYY-MM-DD format or 'UNKNOWN' if not found.
        Text: {text[:500]}"""
        res = model.generate_content(prompt)
        date_str = res.text.strip()
        if date_str != 'UNKNOWN' and len(date_str) == 10:
            return date_str
    except:
        pass
    return None

def is_within_last_6_months(date_str):
    """Check if date is within last 6 months"""
    if not date_str:
        return True
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        cutoff = datetime.now() - timedelta(days=180)
        return date_obj >= cutoff
    except:
        return True

# --- TELEGRAM ---
def send_telegram(message):
    if len(message) > 4000:
        message = message[:4000] + "\n\n_[truncated]_"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        requests.post(url, json=payload, timeout=20)
    except:
        pass

# --- SESSION MANAGEMENT ---
def create_session():
    session = requests.Session()
    retry = Retry(connect=3, backoff_factor=1)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session

# --- CONTENT EXTRACTION ---
def get_content_from_url(session, url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        r = session.get(url, headers=headers, timeout=25, verify=False)
        content_type = r.headers.get('Content-Type', '').lower()
        
        if 'pdf' in content_type or url.lower().endswith('.pdf'):
            try:
                f = io.BytesIO(r.content)
                reader = PdfReader(f)
                text = ""
                for page in reader.pages[:4]:
                    text += page.extract_text() + "\n"
                return f"PDF_TEXT: {text[:4000]}"
            except:
                return "ERROR_READING_PDF"
        else:
            soup = BeautifulSoup(r.text, 'html.parser')
            for s in soup(["script", "style"]):
                s.extract()
            return f"WEB_TEXT: {soup.get_text()[:4000]}"
    except Exception as e:
        return f"DOWNLOAD_ERROR: {str(e)}"

# --- LINK EXTRACTION WITH PAGINATION ---
def extract_links_from_page(session, url, headers):
    try:
        r = session.get(url, headers=headers, timeout=60, verify=False)
        soup = BeautifulSoup(r.text, 'html.parser')
        links = soup.find_all('a', href=True)
        
        candidates = []
        for link in links:
            text = link.get_text(" ", strip=True)
            href = link['href']
            
            if len(text) > 3 and "javascript" not in href.lower():
                full_url = urljoin(url, href)
                candidates.append({"title": text, "url": full_url})
        
        return candidates
    except Exception as e:
        print(f"Error extracting links: {e}")
        return []

# --- PAGINATION HANDLER ---
def get_all_pages(session, base_url, headers, max_pages=3):
    """Try common pagination patterns"""
    all_links = []
    
    for page_num in range(1, max_pages + 1):
        variants = [
            f"{base_url}?page={page_num}",
            f"{base_url}?p={page_num}",
            f"{base_url}&page={page_num}",
            f"{base_url.rstrip('/')}/page/{page_num}/",
        ]
        
        for variant_url in variants:
            try:
                links = extract_links_from_page(session, variant_url, headers)
                if links:
                    all_links.extend(links)
                    time.sleep(1)
                    break
            except:
                continue
    
    main_links = extract_links_from_page(session, base_url, headers)
    all_links = main_links + all_links
    
    seen = set()
    unique_links = []
    for link in all_links:
        if link['url'] not in seen:
            seen.add(link['url'])
            unique_links.append(link)
    
    return unique_links[:30]

# --- AI FILTERING ---
def analyze_with_ai(title, content, country):
    """Use AI to check if relevant to payroll"""
    try:
        prompt = f"""
You are a Senior Payroll Compliance Auditor.

Document Title: "{title}"
Country: {country}
Content Snippet: {content}

TASK:
1. Is this document related to PAYROLL, SALARY, TAX, PENSIONS, CONTRIBUTIONS, EMPLOYEE BENEFITS, LABOR LAW, DEDUCTIONS, or BENEFITS UPDATES? YES or NO?
2. Extract the document DATE (format: YYYY-MM-DD or 'UNKNOWN').
3. If YES: Provide a 1-line summary of what changed/updated.
4. If NO: Reply 'SKIP'

OUTPUT FORMAT:
RELEVANT: [YES/NO]
DATE: [YYYY-MM-DD or UNKNOWN]
SUMMARY: [brief summary or SKIP]
"""
        res = model.generate_content(prompt)
        ans = res.text.strip()
        
        if "RELEVANT: YES" in ans or "RELEVANT:YES" in ans:
            lines = ans.split('\n')
            date_line = next((l.split(':', 1)[1].strip() for l in lines if 'DATE:' in l), 'UNKNOWN')
            summary_line = next((l.split(':', 1)[1].strip() for l in lines if 'SUMMARY:' in l), 'No summary')
            return {
                'relevant': True,
                'date': date_line if date_line != 'UNKNOWN' else extract_date_from_text(content),
                'summary': summary_line
            }
        return {'relevant': False}
    except:
        return {'relevant': False}

# --- TARGET SOURCES ---
TARGETS = [
    {"c": "India", "auth": "Income Tax", "url": "https://incometaxindia.gov.in/pages/communications/circulars.aspx"},
    {"c": "India", "auth": "EPFO", "url": "https://www.epfindia.gov.in/site_en/Circulars.php"},
    {"c": "UAE", "auth": "MOHRE", "url": "https://www.mohre.gov.ae/en/laws-and-regulations/resolutions-and-circulars.aspx"},
    {"c": "Philippines", "auth": "BIR", "url": "https://www.bir.gov.ph/index.php/revenue-issuances/revenue-memorandum-circulars.html"},
    {"c": "Kenya", "auth": "KRA", "url": "https://www.kra.go.ke/news-center/public-notices"},
    {"c": "Nigeria", "auth": "FIRS", "url": "https://www.firs.gov.ng/press-release/"},
    {"c": "Ghana", "auth": "GRA", "url": "https://www.gra.gov.gh/index.php/news/"},
    {"c": "Uganda", "auth": "URA", "url": "https://ura.go.ug/en/publications/public-notices/"},
    {"c": "Zambia", "auth": "ZRA", "url": "https://www.zra.org.zm/"},
    {"c": "Zimbabwe", "auth": "ZIMRA News", "url": "https://www.zimra.co.zw/news"},
    {"c": "Zimbabwe", "auth": "ZIMRA Notices", "url": "https://www.zimra.co.zw/public-notices"},
    {"c": "South Africa", "auth": "SARS", "url": "https://www.sars.gov.za/legal-counsel/interpretation-rulings/interpretation-notes/"},
]

# --- MAIN SCAN ---
def run_full_scan():
    print("🚀 DRAGNET 6-MONTH SCAN STARTING...\n")
    send_telegram("🚀 *DRAGNET 6-Month Scan Initiated*\n_Scanning all 10 countries for payroll notifications (last 6 months)..._")
    
    conn = init_database()
    session = create_session()
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    total_found = 0
    total_sent = 0
    
    for target in TARGETS:
        country = target['c']
        url = target['url']
        print(f"\n📍 Scanning {country} ({target['auth']})...")
        
        try:
            candidates = get_all_pages(session, url, headers, max_pages=4)
            print(f"   Found {len(candidates)} links to analyze...")
            
            if not candidates:
                print(f"⚠️ No links found for {country}")
                continue
            
            country_findings = []
            
            for idx, item in enumerate(candidates):
                if is_already_sent(conn, item['url']):
                    print(f"   [{idx+1}] ✓ Already sent: {item['title'][:50]}")
                    continue
                
                print(f"   [{idx+1}] Analyzing: {item['title'][:60]}...")
                
                content = get_content_from_url(session, item['url'])
                if "ERROR" in content or "DOWNLOAD" in content:
                    print(f"      ~ Failed to fetch content")
                    continue
                
                analysis = analyze_with_ai(item['title'], content, country)
                
                if analysis['relevant']:
                    date = analysis['date']
                    
                    if is_within_last_6_months(date):
                        summary = analysis['summary']
                        
                        if save_notification(conn, country, item['title'], item['url'], date, target['auth']):
                            mark_as_sent(conn, item['url'])
                            country_findings.append({
                                'title': item['title'],
                                'url': item['url'],
                                'date': date,
                                'summary': summary
                            })
                            total_found += 1
                            print(f"      ✅ FOUND: {summary[:50]}")
                            time.sleep(2)
                        else:
                            print(f"      ~ Already in DB")
            
            if country_findings:
                report = f"🌍 *{country.upper()}* - {len(country_findings)} Update(s)\n"
                report += f"_Source: {target['auth']}_\n\n"
                for finding in country_findings:
                    report += f"📅 *{finding['date']}*\n"
                    report += f"[{finding['title']}]({finding['url']})\n"
                    report += f"_{finding['summary']}_\n\n"
                send_telegram(report)
                total_sent += len(country_findings)
        
        except Exception as e:
            print(f"❌ Error scanning {country}: {e}")
    
    summary = f"""
✅ *DRAGNET SCAN COMPLETE*

📊 *Summary*:
• Total Notifications Found: *{total_found}*
• New Notifications Sent: *{total_sent}*
• Database Updated: *{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
• Lookback Period: *Last 6 Months*

🔔 Next scan scheduled daily at 6 AM UTC
"""
    send_telegram(summary)
    print("\n" + summary)
    
    conn.close()

if __name__ == "__main__":
    run_full_scan()
