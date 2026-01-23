import requests
from bs4 import BeautifulSoup
import os
import time
import urllib3
import sqlite3
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urljoin

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

def save_notification(conn, country, title, url, source_type):
    c = conn.cursor()
    try:
        c.execute('''INSERT INTO notifications (country, title, url, found_at, source_type)
                     VALUES (?, ?, ?, ?, ?)''',
                  (country, title, url, datetime.now().isoformat(), source_type))
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
    except Exception as e:
        print(f"⚠️ Telegram error: {e}")

# --- SESSION ---
def create_session():
    session = requests.Session()
    retry = Retry(connect=2, backoff_factor=0.5, total=2)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session

# --- EXTRACT LINKS WITH RETRIES ---
def extract_links_from_page(session, url, headers, timeout=15, retries=3):
    for attempt in range(retries):
        try:
            print(f"   Attempting fetch (try {attempt+1}/{retries})...")
            r = session.get(url, headers=headers, timeout=timeout, verify=False)
            soup = BeautifulSoup(r.text, 'html.parser')
            links = soup.find_all('a', href=True)
            
            candidates = []
            for link in links:
                text = link.get_text(" ", strip=True)
                href = link['href']
                
                if len(text) > 2 and "javascript" not in href.lower():
                    full_url = urljoin(url, href)
                    candidates.append({"title": text, "url": full_url})
            
            if candidates:
                print(f"   ✓ Successfully fetched {len(candidates)} links")
                return candidates
        except Exception as e:
            print(f"   ⚠️ Attempt {attempt+1} failed: {str(e)[:50]}")
            if attempt < retries - 1:
                wait_time = 5 * (attempt + 1)
                print(f"   ⏳ Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
    
    print(f"   ❌ Failed to fetch after {retries} attempts")
    return []

# --- COUNTRY-SPECIFIC KEYWORDS ---
KEYWORDS_BY_COUNTRY = {
    "India": [
        "TDS", "Form 16", "Section 192", "EPFO", "CBDT", "EPS", "EDLI", "ESI",
        "PAN", "TAN", "Form 24Q", "Income Tax Slab", "HRA", "LTA", "Gratuity",
        "Professional Tax", "Leave Encashment", "Cost to Company", "CTC",
        "Dearness Allowance", "DA", "Special Allowance", "Flexible Benefit",
        "Form 12BB", "Section 80C", "Rebate u/s 87A", "Notification", "Circular",
        "Office Memorandum", "Press Release", "Finance Act", "TRACES", "EPFO Portal",
        "Monthly Return", "Quarterly Return", "Form 26AS", "Aadhaar-PAN"
    ],
    
    "UAE": [
        "Corporate Tax", "Withholding Tax", "Tax Residency", "TRN", "VAT",
        "MOHRE", "FTA", "Emiratisation", "Wage Protection System", "WPS",
        "Housing Allowance", "Transport Allowance", "ILOE", "GPSSA", "End of Service",
        "Gratuity", "Ministerial Resolution", "Cabinet Decision", "Federal Decree",
        "Nafis", "SIF", "Tawteen", "EmaraTax", "UAEPASS", "Circular",
        "Notice", "Public Clarification", "Labour Card", "Visa"
    ],
    
    "Philippines": [
        "13th Month Pay", "COLA", "Withholding Tax", "BIR Form", "Compensation Income",
        "Holiday Pay", "Regular Holiday", "Special Non-Working Day", "Rest Day Premium",
        "Night Shift Differential", "OT", "Overtime", "Service Incentive Leave",
        "SSS", "PhilHealth", "Pag-IBIG", "DOLE", "Labor Advisory", "Wage Order",
        "Revenue Memorandum Circular", "RMC", "eFPS", "Substituted Filing",
        "Alphalist", "Establishment Report", "De Minimis", "FBT"
    ],
    
    "Kenya": [
        "PAYE", "P9 Form", "P10 Return", "NSSF", "SHIF", "Affordable Housing Levy",
        "AHL", "Fringe Benefit Tax", "Personal Relief", "KRA", "Public Notice",
        "iTax", "eTIMS", "Housing Benefit", "Car Benefit", "Tax Deduction Card",
        "Deemed Interest Rate", "Non-Cash Benefits", "HELB", "RBA", "Legal Notice",
        "Gazette Notice", "Finance Act", "TCC", "Tax Compliance Certificate",
        "Industrial Training Levy"
    ],
    
    "Nigeria": [
        "PAYE", "NRS", "FIRS", "Personal Income Tax", "PITA", "Development Levy",
        "Withholding Tax", "TIN", "CRA", "Benefits in Kind", "Consolidated Relief",
        "NHF", "NHIA", "Pension Scheme", "CPS", "RSA", "PFA", "PENCOM",
        "Information Circular", "Public Notice", "Executive Order", "Finance Act",
        "Form H1", "Remita", "E-TCC", "LIRS", "Gratuity", "13th Month"
    ],
    
    "Ghana": [
        "PAYE", "TIN", "SSNIT", "Tier 1", "Tier 2", "GRA", "Tax Relief",
        "Overtime Tax", "Benefits in Kind", "Chargeable Income", "Emoluments",
        "Housing Allowance", "Transport Allowance", "COLA", "13th Month",
        "Practice Note", "Administrative Guideline", "NPRA", "Pension Scheme",
        "First Schedule", "Withholding Tax", "Portal", "Gazette", "Income Tax Act"
    ],
    
    "Uganda": [
        "PAYE", "TIN", "NSSF", "LST", "Local Service Tax", "URA", "EFRIS",
        "Electronic Fiscal Receipting", "Chargeable Income", "Housing Allowance",
        "Transport Allowance", "Gratuity", "Overtime", "URBRA", "Public Notice",
        "General Notice", "Practice Note", "Act of Parliament", "Statutory Instrument",
        "e-Tax", "PRN", "UKSB", "Withholding Tax", "Provisional Return"
    ],
    
    "Zambia": [
        "PAYE", "NAPSA", "NHIMA", "ZRA", "Tax Credit", "Emoluments", "Skills Development Levy",
        "SDL", "Tax Free Threshold", "Tax Bands", "Housing Allowance", "Transport Allowance",
        "Commutation of Leave", "Gratuity", "Practice Note", "Gazette Notice",
        "Statutory Instrument", "Budget Speech", "TaxOnline", "Smart Invoice",
        "TPIN", "Turnover Tax", "Personal Levy", "Occupational Pension"
    ],
    
    "Zimbabwe": [
        "PAYE", "ZIMRA", "NSSA", "AIDS Levy", "ZiG", "TaRMS", "Final Deduction System",
        "FDS", "Business Partner Number", "BP Number", "NEC Minimum Wage", "COLA",
        "WCIF", "ZIMDEF", "Public Notice", "Statutory Instrument", "Finance Act",
        "ITF", "P2 Return", "Rev 5", "Collective Bargaining Agreement", "e-Services",
        "Paynow", "Remittance", "Intermediated Money Transfer Tax"
    ],
    
    "South Africa": [
        "PAYE", "SARS", "UIF", "SDL", "IRP5", "EMP201", "Tax Directive",
        "Fringe Benefits", "Travel Allowance", "Subsistence Allowance",
        "Medical Scheme", "Retirement Annuity", "Provident Fund", "GEPF",
        "Two-Pot System", "Sectoral Determination", "Regulation", "Interpretation Note",
        "Government Gazette", "DEL", "CCMA", "Compensation Fund", "COIDA",
        "National Minimum Wage", "Bargaining Council", "eFiling", "Garnishree"
    ]
}

# --- OFFICIAL REPOSITORIES ---
REPOSITORIES = {
    "India": [
        {"type": "Income Tax Circulars", "url": "https://incometaxindia.gov.in/pages/communications/circulars.aspx"},
        {"type": "Income Tax Notifications", "url": "https://incometaxindia.gov.in/pages/communications/index.aspx"},
        {"type": "EPFO Circulars", "url": "https://www.epfindia.gov.in/site_en/circulars.php"},
        {"type": "EPFO Updates", "url": "https://www.epfindia.gov.in/site_en/Updates.php"},
    ],
    "UAE": [
        {"type": "MOHRE Resolutions", "url": "https://www.mohre.gov.ae/en/laws-and-regulations/resolutions-and-circulars.aspx"},
        {"type": "FTA Guides", "url": "https://tax.gov.ae/en/content/guides.references.aspx"},
    ],
    "Philippines": [
        {"type": "DOLE Labor Advisories", "url": "https://www.dole.gov.ph/issuances/labor-advisories/"},
        {"type": "BIR Revenue Issuances", "url": "https://www.bir.gov.ph/revenue-issuances-details"},
    ],
    "Kenya": [
        {"type": "KRA Public Notices", "url": "https://www.kra.go.ke/news-center/public-notices"},
        {"type": "KRA Publications", "url": "https://www.kra.go.ke/publications"},
    ],
    "Nigeria": [
        {"type": "NRS Notices", "url": "https://www.nrs.gov.ng/"},
    ],
    "Ghana": [
        {"type": "GRA Practice Notes", "url": "https://gra.gov.gh/practice-notes/"},
        {"type": "GRA Acts & Laws", "url": "https://gra.gov.gh/acts-and-practice-notes-2/"},
    ],
    "Uganda": [
        {"type": "URA Public Notices", "url": "https://www.ura.go.ug/"},
    ],
    "Zambia": [
        {"type": "ZRA Practice Notes", "url": "https://www.zra.org.zm/tax-information/tax-information-details/"},
    ],
    "Zimbabwe": [
        {"type": "ZIMRA Public Notices", "url": "https://www.zimra.co.zw/public-notices"},
        {"type": "ZIMRA Downloads", "url": "https://www.zimra.co.zw/downloads/category/39-public-notices"},
    ],
    "South Africa": [
        {"type": "SARS Public Notices", "url": "https://www.sars.gov.za/legal-counsel/secondary-legislation/public-notices/"},
        {"type": "SARS Interpretation Notes", "url": "https://www.sars.gov.za/legal-counsel/legal-documents/interpretation-notes/"},
        {"type": "Dept Labour Acts", "url": "https://www.labour.gov.za/DocumentCenter/Pages/Acts.aspx"},
    ],
}

def is_payroll_relevant(country, title, content=""):
    """Check if document is relevant using country-specific keywords"""
    combined = (title + " " + content).lower()
    keywords = KEYWORDS_BY_COUNTRY.get(country, [])
    match_count = sum(1 for kw in keywords if kw.lower() in combined)
    return match_count >= 1

# --- MAIN SCAN ---
def run_full_scan():
    print("🚀 DRAGNET SCAN STARTING...\n")
    print("⏱️  This scan will take 5-10 minutes due to international site delays\n")
    send_telegram("🚀 *DRAGNET Scan Started*\n_Using country-specific payroll keywords..._\n⏱️ Estimated time: 5-10 minutes")
    
    scan_start = datetime.now()
    conn = init_database()
    session = create_session()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    total_found = 0
    country_reports = {}
    
    try:
        for idx, (country, sources) in enumerate(REPOSITORIES.items(), 1):
            print(f"\n[{idx}/10] 📍 {country}...")
            country_findings = []
            
            for source in sources:
                source_type = source["type"]
                url = source["url"]
                
                print(f"   📄 {source_type}...")
                
                try:
                    links = extract_links_from_page(session, url, headers, timeout=15, retries=3)
                    print(f"      Extracted {len(links)} documents")
                    
                    for item in links[:20]:
                        if is_already_sent(conn, item['url']):
                            continue
                        
                        title = item['title']
                        
                        if is_payroll_relevant(country, title):
                            if save_notification(conn, country, title, item['url'], source_type):
                                mark_as_sent(conn, item['url'])
                                country_findings.append({
                                    'title': title,
                                    'url': item['url'],
                                    'source': source_type
                                })
                                total_found += 1
                                print(f"      ✅ PAYROLL: {title[:50]}")
                                time.sleep(1)
                    
                except Exception as e:
                    print(f"      ⚠️ Error: {str(e)[:60]}")
                    continue
                
                time.sleep(2)
            
            if country_findings:
                country_reports[country] = country_findings
            
            time.sleep(3)
    
    except Exception as e:
        print(f"\n❌ Scan error: {e}")
        send_telegram(f"❌ *Scan Error*: {str(e)[:100]}")
    
    finally:
        # ALWAYS send results even if incomplete
        print("\n📤 Sending results to Telegram...\n")
        
        if country_reports:
            for country, findings in country_reports.items():
                report = f"🌍 *{country.upper()}* - {len(findings)} Document(s)\n\n"
                for finding in findings:
                    report += f"📋 *{finding['source']}*\n"
                    report += f"{finding['title'][:70]}\n"
                    report += f"[Open]({finding['url']})\n\n"
                send_telegram(report)
                time.sleep(1)
        else:
            send_telegram("⚠️ *No payroll documents found this scan*\n(Common for newly released documents)")
        
        scan_time = (datetime.now() - scan_start).total_seconds() / 60
        summary = f"""✅ *DRAGNET SCAN COMPLETE*

📊 *Results*:
• Documents Found: *{total_found}*
• Countries Scanned: *{len(REPOSITORIES)}*
• Scan Duration: *{scan_time:.1f} minutes*
• Completed: *{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*

🔔 Next scan: Daily at 6 AM UTC
"""
        send_telegram(summary)
        print(summary)
        
        conn.close()

if __name__ == "__main__":
    run_full_scan()
