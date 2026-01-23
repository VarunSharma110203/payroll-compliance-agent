#!/usr/bin/env python3
import requests
from bs4 import BeautifulSoup
import google.genai
import os
import time
import urllib3
import sqlite3
import re
import io
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urljoin
from pypdf import PdfReader

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

GEMINI_API_KEY = os.environ.get("GEMINI_KEY")
TELEGRAM_TOKEN = os.environ.get("AUDIT_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not all([GEMINI_API_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
    print("ERROR: Missing environment variables")
    exit(1)

client = google.genai.Client(api_key=GEMINI_API_KEY)

def init_db():
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
        source_type TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS last_run (
        country TEXT PRIMARY KEY,
        last_run_time TEXT
    )''')
    conn.commit()
    return conn

def send_telegram(msg):
    if len(msg) > 4000:
        msg = msg[:4000] + "\n\n_[truncated]_"
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                     json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
                     timeout=10)
    except:
        pass

def create_session():
    s = requests.Session()
    a = HTTPAdapter(max_retries=Retry(connect=2, backoff_factor=0.5))
    s.mount('https://', a)
    s.mount('http://', a)
    return s

def extract_date(text):
    patterns = [
        r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})',
        r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})',
        r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})',
        r'_(\d{8})',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            date_str = m.group(1) if m.lastindex >= 1 else m.group(0)
            if len(date_str) == 8 and date_str.isdigit():
                try:
                    return f"{date_str[:2]}-{date_str[2:4]}-{date_str[4:8]}"
                except:
                    pass
            return date_str
    return "UNKNOWN"

def is_within_6months(date_str):
    if date_str == "UNKNOWN":
        return True
    try:
        for fmt in ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%B %d, %Y"]:
            try:
                d = datetime.strptime(date_str, fmt)
                cutoff = datetime.now() - timedelta(days=180)
                return d >= cutoff
            except:
                continue
        return True
    except:
        return True

def get_pdf_content(session, url):
    try:
        r = session.get(url, timeout=15, verify=False)
        pdf = PdfReader(io.BytesIO(r.content))
        text = ""
        for page in pdf.pages[:3]:
            text += page.extract_text() + "\n"
        return text[:2000]
    except:
        return ""

def get_page_content(session, url):
    try:
        r = session.get(url, timeout=10, verify=False)
        soup = BeautifulSoup(r.text, 'html.parser')
        for s in soup(["script", "style"]):
            s.extract()
        return soup.get_text()[:2000]
    except:
        return ""

def detect_change(country, title, content=""):
    keywords = {
        "India": ["circular", "notification", "tds", "pf", "epfo", "tax slab", "amended", "effective"],
        "UAE": ["resolution", "circular", "emiratisation", "corporate tax", "gratuity", "amended"],
        "Philippines": ["labor advisory", "rmc", "13th month", "minimum wage", "holiday pay", "amended"],
        "Kenya": ["public notice", "paye", "nssf", "tax", "amended", "effective"],
        "Nigeria": ["circular", "notice", "paye", "tax slab", "minimum wage", "amended"],
        "Ghana": ["practice note", "paye", "ssnit", "amended", "effective"],
        "Uganda": ["notice", "paye", "nssf", "amended", "effective"],
        "Zambia": ["practice note", "paye", "napsa", "amended", "effective"],
        "Zimbabwe": ["notice", "paye", "nssa", "amended", "effective"],
        "South Africa": ["interpretation note", "paye", "uif", "amended", "effective"],
    }
    
    combined = (title + " " + content).lower()
    kws = keywords.get(country, [])
    
    if not any(k in combined for k in kws):
        return None, []
    
    if len(content) > 100:
        try:
            prompt = f"""Is this a payroll POLICY CHANGE (rate, tax, wage, contribution change)? YES or NO only.
Title: {title}
Content: {content[:500]}"""
            resp = client.models.generate_content(model="models/gemini-2.0-flash", contents=prompt)
            if "YES" in resp.text.upper():
                return "CHANGE", [k for k in kws if k in combined]
        except:
            pass
    
    matched = [k for k in kws if k in combined]
    return ("CHANGE" if matched else None, matched)

def fetch_links(session, url, headers):
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
        return candidates
    except:
        return []

REPOS = {
    "India": [
        {"type": "Income Tax Circulars", "url": "https://incometaxindia.gov.in/pages/communications/circulars.aspx"},
        {"type": "EPFO Circulars", "url": "https://www.epfindia.gov.in/site_en/circulars.php"},
    ],
    "UAE": [
        {"type": "MOHRE", "url": "https://www.mohre.gov.ae/en/laws-and-regulations/resolutions-and-circulars.aspx"},
    ],
    "Philippines": [
        {"type": "DOLE", "url": "https://www.dole.gov.ph/issuances/labor-advisories/"},
        {"type": "BIR", "url": "https://www.bir.gov.ph/revenue-issuances-details"},
    ],
    "Kenya": [
        {"type": "KRA", "url": "https://www.kra.go.ke/news-center/public-notices"},
    ],
    "Nigeria": [
        {"type": "NRS", "url": "https://www.nrs.gov.ng/"},
    ],
    "Ghana": [
        {"type": "GRA", "url": "https://gra.gov.gh/practice-notes/"},
    ],
    "Uganda": [
        {"type": "URA", "url": "https://www.ura.go.ug/"},
    ],
    "Zambia": [
        {"type": "ZRA", "url": "https://www.zra.org.zm/tax-information/tax-information-details/"},
    ],
    "Zimbabwe": [
        {"type": "ZIMRA", "url": "https://www.zimra.co.zw/public-notices"},
    ],
    "South Africa": [
        {"type": "SARS", "url": "https://www.sars.gov.za/legal-counsel/legal-documents/interpretation-notes/"},
    ],
}

def main():
    print("🚀 DRAGNET: Scanning last 6 months for policy changes\n")
    send_telegram("🚀 DRAGNET SCAN START\nScanning last 6 months...\n⏱️ 10-15 minutes")
    
    conn = init_db()
    session = create_session()
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    total = 0
    reports = {}
    
    for idx, (country, sources) in enumerate(REPOS.items(), 1):
        print(f"[{idx}/10] {country}...")
        findings = []
        
        for src in sources:
            print(f"  {src['type']}...")
            try:
                links = fetch_links(session, src['url'], headers)
                
                for item in links[:25]:
                    url = item['url']
                    title = item['title']
                    is_pdf = url.lower().endswith('.pdf')
                    
                    c = conn.cursor()
                    c.execute('SELECT id FROM notifications WHERE url = ? AND sent_to_telegram = 1', (url,))
                    if c.fetchone():
                        continue
                    
                    print(f"    Reading: {title[:40]}...")
                    content = get_pdf_content(session, url) if is_pdf else get_page_content(session, url)
                    
                    change_type, matched = detect_change(country, title, content)
                    
                    if change_type:
                        date = extract_date(url) if is_pdf else extract_date(title)
                        
                        if not is_within_6months(date):
                            continue
                        
                        kw_str = ", ".join(matched[:3]) if matched else "Policy Change"
                        
                        try:
                            c.execute('''INSERT INTO notifications 
                                        (country, title, url, doc_date, found_at, source_type, sent_to_telegram)
                                        VALUES (?, ?, ?, ?, ?, ?, 1)''',
                                     (country, title, url, date, datetime.now().isoformat(), src['type']))
                            conn.commit()
                            
                            findings.append({
                                'title': title,
                                'url': url,
                                'date': date,
                                'keywords': kw_str,
                                'is_pdf': is_pdf
                            })
                            total += 1
                            icon = "📄" if is_pdf else "📋"
                            print(f"    ✅ {icon} {title[:50]}")
                            time.sleep(1)
                        except:
                            pass
                
            except Exception as e:
                print(f"    Error: {str(e)[:40]}")
            
            time.sleep(1)
        
        if findings:
            reports[country] = findings
        
        time.sleep(2)
    
    print("\n📤 Sending results...\n")
    
    if reports:
        for country, finds in reports.items():
            msg = f"🚨 {country.upper()} - {len(finds)} CHANGES\n\n"
            for f in finds:
                icon = "📄" if f['is_pdf'] else "📋"
                msg += f"{icon} {f['title'][:70]}\n"
                msg += f"📅 {f['date']}\n"
                msg += f"[OPEN]({f['url']})\n\n"
            send_telegram(msg)
            time.sleep(1)
    else:
        send_telegram("✅ No policy changes found in last 6 months")
    
    summary = f"✅ COMPLETE\n📊 Found: {total} changes\n🌍 10 countries\n⏱️ {datetime.now().strftime('%H:%M:%S')}"
    send_telegram(summary)
    print(summary)
    
    conn.close()

if __name__ == "__main__":
    main()
