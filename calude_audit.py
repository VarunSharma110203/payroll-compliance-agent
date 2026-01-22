import requests
from bs4 import BeautifulSoup
import google.genai
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
from urllib.parse import urljoin

# --- CONFIGURATION ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    GENAI_API_KEY = os.environ["GEMINI_KEY"]
    TELEGRAM_TOKEN = os.environ["AUDIT_BOT_TOKEN"]
    TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
except KeyError:
    print("❌ ERROR: Keys not found!")
    exit(1)

client = google.genai.Client(api_key=GENAI_API_KEY)

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
        source_type TEXT,
        impact_data TEXT
    )''')
    conn.commit()
    return conn

def is_already_sent(conn, url):
    c = conn.cursor()
    c.execute('SELECT id FROM notifications WHERE url = ? AND sent_to_telegram = 1', (url,))
    return c.fetchone() is not None

def save_notification(conn, country, title, url, content_date, source_type, impact_data=None):
    c = conn.cursor()
    try:
        impact_json = json.dumps(impact_data) if impact_data else '{}'
        c.execute('''INSERT INTO notifications (country, title, url, content_date, found_at, source_type, impact_data)
                     VALUES (?, ?, ?, ?, ?, ?, ?)''',
                  (country, title, url, content_date, datetime.now().isoformat(), source_type, impact_json))
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
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        requests.post(url, json=payload, timeout=10)
        print("✅ Message sent to Telegram")
    except Exception as e:
        print(f"⚠️ Failed to send Telegram: {e}")

# --- SESSION MANAGEMENT ---
def create_session():
    session = requests.Session()
    retry = Retry(connect=2, backoff_factor=0.5, total=2)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session

# --- CONTENT EXTRACTION ---
def get_content_from_url(session, url, timeout=10):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        r = session.get(url, headers=headers, timeout=timeout, verify=False)
        content_type = r.headers.get('Content-Type', '').lower()
        
        if 'pdf' in content_type or url.lower().endswith('.pdf'):
            try:
                f = io.BytesIO(r.content)
                reader = PdfReader(f)
                text = ""
                for page in reader.pages[:2]:
                    text += page.extract_text() + "\n"
                return f"PDF_TEXT: {text[:2000]}"
            except:
                return None
        else:
            soup = BeautifulSoup(r.text, 'html.parser')
            for s in soup(["script", "style"]):
                s.extract()
            text = soup.get_text()[:2000]
            return f"WEB_TEXT: {text}" if text.strip() else None
    except:
        return None

# --- LINK EXTRACTION ---
def extract_links_from_page(session, url, headers, timeout=8):
    try:
        r = session.get(url, headers=headers, timeout=timeout, verify=False)
        soup = BeautifulSoup(r.text, 'html.parser')
        links = soup.find_all('a', href=True)
        
        candidates = []
        for link in links:
            text = link.get_text(" ", strip=True)
            href = link['href']
            
            if len(text) > 3 and "javascript" not in href.lower():
                full_url = urljoin(url, href)
                candidates.append({"title": text, "url": full_url})
                if len(candidates) >= 15:
                    break
        
        return candidates
    except:
        return []

# --- PAGINATION HANDLER ---
def get_all_pages(session, base_url, headers, max_pages=2):
    all_links = []
    
    try:
        main_links = extract_links_from_page(session, base_url, headers, timeout=8)
        all_links.extend(main_links)
    except:
        pass
    
    for page_num in [2]:
        variants = [
            f"{base_url}?page={page_num}",
            f"{base_url}&page={page_num}",
        ]
        
        for variant_url in variants:
            try:
                links = extract_links_from_page(session, variant_url, headers, timeout=6)
                if links:
                    all_links.extend(links)
                    break
            except:
                continue
    
    seen = set()
    unique_links = []
    for link in all_links:
        if link['url'] not in seen:
            seen.add(link['url'])
            unique_links.append(link)
    
    return unique_links[:20]

# --- AI FILTERING - PAYROLL IMPACT ANALYSIS ---
def analyze_with_ai(title, content, country):
    """Deep AI analysis for employer payroll impact"""
    try:
        prompt = f"""You are a Payroll Compliance Expert. Analyze if this notification affects EMPLOYER PAYROLL OBLIGATIONS.

COUNTRY: {country}
TITLE: {title}
CONTENT: {content[:1000]}

CRITICAL QUESTION:
Does this document require EMPLOYERS to change their:
1. Tax withholding amounts? (e.g., tax slab changes, rate changes)
2. Employee deductions? (e.g., pension contributions, insurance)
3. Salary calculations? (e.g., minimum wage, allowances, gratuity)
4. Statutory contributions? (e.g., PF, ESI, social security rates)
5. Payment dates/methods? (e.g., new filing deadlines)
6. Compliance requirements? (e.g., new forms, reports, validations)

RESPOND EXACTLY IN THIS FORMAT (no extra text):
CRUCIAL: [YES/NO]
IMPACT_TYPE: [Tax/Deduction/Salary/Contribution/Compliance/Other or NONE]
CHANGE_DETAILS: [Specific change that affects employers]
EFFECTIVE_DATE: [Date when this takes effect or UNKNOWN]
ACTION_REQUIRED: [What employers must do]"""
        
        response = google.genai.Client().models.generate_content(
            model="models/gemini-2.0-flash",
            contents=prompt,
            config={"max_output_tokens": 300}
        )
        ans = response.text.strip()
        
        if "CRUCIAL: YES" in ans:
            lines = ans.split('\n')
            
            impact_type = next((l.split(':', 1)[1].strip() for l in lines if 'IMPACT_TYPE:' in l), 'Payroll')
            change_details = next((l.split(':', 1)[1].strip() for l in lines if 'CHANGE_DETAILS:' in l), 'Update needed')
            effective_date = next((l.split(':', 1)[1].strip() for l in lines if 'EFFECTIVE_DATE:' in l), 'UNKNOWN')
            action_required = next((l.split(':', 1)[1].strip() for l in lines if 'ACTION_REQUIRED:' in l), 'Review document')
            
            return {
                'relevant': True,
                'impact_type': impact_type,
                'change_details': change_details,
                'effective_date': effective_date,
                'action_required': action_required,
                'title': title
            }
        return {'relevant': False}
    except Exception as e:
        print(f"AI Error: {e}")
        return {'relevant': False}

# --- TARGET SOURCES ---
TARGETS = [
    {"c": "India", "auth": "EPFO", "url": "https://www.epfindia.gov.in/site_en/circulars.php"},
    {"c": "India", "auth": "Income Tax", "url": "https://incometaxindia.gov.in/pages/communications/circulars.aspx"},
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
    print("🚀 DRAGNET SCAN STARTING...\n")
    send_telegram("🚀 *DRAGNET Scan Started*\n_Scanning all countries for payroll notifications..._")
    
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
            candidates = get_all_pages(session, url, headers, max_pages=2)
            print(f"   Found {len(candidates)} links")
            
            if not candidates:
                continue
            
            country_findings = []
            
            for idx, item in enumerate(candidates[:10]):
                if is_already_sent(conn, item['url']):
                    continue
                
                print(f"   [{idx+1}] Checking: {item['title'][:40]}...")
                
                content = get_content_from_url(session, item['url'], timeout=8)
                if not content:
                    continue
                
                analysis = analyze_with_ai(item['title'], content, country)
                
                if analysis['relevant']:
                    if save_notification(conn, country, item['title'], item['url'], analysis['effective_date'], target['auth'], impact_data=analysis):
                        mark_as_sent(conn, item['url'])
                        country_findings.append({
                            'title': item['title'],
                            'url': item['url'],
                            'impact_type': analysis.get('impact_type', 'Payroll'),
                            'change_details': analysis.get('change_details', 'N/A'),
                            'effective_date': analysis.get('effective_date', 'UNKNOWN'),
                            'action_required': analysis.get('action_required', 'Review document')
                        })
                        total_found += 1
                        print(f"      ✅ FOUND: {analysis.get('impact_type', 'Update')}")
                        time.sleep(1)
            
            if country_findings:
                report = f"🌍 *{country}* - {len(country_findings)} CRITICAL Update(s)\n\n"
                for finding in country_findings:
                    report += f"*[{finding.get('impact_type', 'Payroll')}]* {finding['title']}\n"
                    report += f"📋 *Change Details:*\n{finding.get('change_details', 'N/A')}\n\n"
                    report += f"📅 *Effective Date:* {finding.get('effective_date', 'UNKNOWN')}\n"
                    report += f"⚡ *Action Required:*\n{finding.get('action_required', 'Review document')}\n"
                    report += f"🔗 [View Document]({finding['url']})\n"
                    report += "---\n"
                send_telegram(report)
                total_sent += len(country_findings)
        
        except Exception as e:
            print(f"❌ Error {country}: {e}")
    
    summary = f"✅ *SCAN COMPLETE*\n📊 Found: *{total_found}*\n📤 Sent: *{total_sent}*"
    send_telegram(summary)
    print("\n" + summary)
    
    conn.close()

if __name__ == "__main__":
    run_full_scan()
