import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
import os
import time
import urllib.parse
import re

# --- 1. CONFIGURATION ---
try:
    GENAI_API_KEY = os.environ["GEMINI_KEY"]
    TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
    TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
except KeyError:
    print("❌ ERROR: Keys not found! Check GitHub Secrets.")
    exit(1)

genai.configure(api_key=GENAI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# --- 2. THE MASTER COMPLIANCE LIST (BASED ON YOUR RESEARCH) ---
TARGETS = [
    # === 🇮🇳 INDIA (The Big 5) ===
    {"c": "India", "auth": "Income Tax (CBDT)", "url": "https://incometaxindia.gov.in/pages/communications/circulars.aspx", "base": "https://incometaxindia.gov.in", "kw": ["tds", "salary", "section 192", "exemption", "form 16"]},
    {"c": "India", "auth": "Income Tax (Notifications)", "url": "https://incometaxindia.gov.in/pages/communications/notifications.aspx", "base": "https://incometaxindia.gov.in", "kw": ["statutory order", "amendment", "cost inflation"]},
    {"c": "India", "auth": "EPFO (Provident Fund)", "url": "https://www.epfindia.gov.in/site_en/Circulars.php", "base": "https://www.epfindia.gov.in", "kw": ["interest rate", "wage ceiling", "contribution", "aadhaar"]},
    {"c": "India", "auth": "PFRDA (NPS)", "url": "https://www.pfrda.org.in/index1.cshtml?lsid=167", "base": "https://www.pfrda.org.in", "kw": ["corporate", "tier", "withdrawal", "kyc"]},
    {"c": "India", "auth": "Ministry of Labour", "url": "https://labour.gov.in/circulars", "base": "https://labour.gov.in", "kw": ["minimum wage", "bonus", "gratuity", "maternity", "vda"]},
    {"c": "India", "auth": "ESIC (Insurance)", "url": "https://www.esic.gov.in/circulars", "base": "https://www.esic.gov.in", "kw": ["contribution rate", "wage ceiling", "medical benefit"]},

    # === 🇦🇪 UAE (Emiratisation & Tax) ===
    {"c": "UAE", "auth": "MOHRE (Labour)", "url": "https://www.mohre.gov.ae/en/laws-and-regulations/resolutions-and-circulars.aspx", "base": "https://www.mohre.gov.ae", "kw": ["emiratisation", "wps", "work permit", "fine", "quota"]},
    {"c": "UAE", "auth": "FTA (Tax)", "url": "https://tax.gov.ae/en/taxes/Vat.aspx", "base": "https://tax.gov.ae", "kw": ["corporate tax", "employment income", "registration threshold"]},
    {"c": "UAE", "auth": "GPSSA (Pension)", "url": "https://gpssa.gov.ae/pages/en/laws-and-regulations", "base": "https://gpssa.gov.ae", "kw": ["contribution rate", "pension cap", "decree-law 57"]},

    # === 🇳🇬 NIGERIA (The Federal Suite) ===
    {"c": "Nigeria", "auth": "FIRS (Tax)", "url": "https://www.firs.gov.ng/press-release/", "base": "https://www.firs.gov.ng", "kw": ["public notice", "wht", "paye", "tax clearance", "relief"]},
    {"c": "Nigeria", "auth": "PenCom (Pension)", "url": "https://www.pencom.gov.ng/category/regulations-guidelines-circulars-frameworks/circulars/", "base": "https://www.pencom.gov.ng", "kw": ["voluntary contribution", "rsa", "investment", "recapture"]},
    {"c": "Nigeria", "auth": "NHIA (Health)", "url": "https://www.nhia.gov.ng/operational-guideline/", "base": "https://www.nhia.gov.ng", "kw": ["operational guideline", "gifship", "mandatory", "capitation"]},
    {"c": "Nigeria", "auth": "ITF (Training)", "url": "http://www.itf.gov.ng/", "base": "http://www.itf.gov.ng/", "kw": ["training contribution", "reimbursement", "compliance certificate"]},

    # === 🇵🇭 PHILIPPINES (The Statutory 4) ===
    {"c": "Philippines", "auth": "BIR (Tax)", "url": "https://www.bir.gov.ph/index.php/revenue-issuances/revenue-memorandum-circulars.html", "base": "https://www.bir.gov.ph", "kw": ["withholding tax", "alphalist", "bonus", "tax table", "orus"]},
    {"c": "Philippines", "auth": "SSS (Social Security)", "url": "https://www.sss.gov.ph/sss-circulars/", "base": "https://www.sss.gov.ph", "kw": ["contribution schedule", "msp", "wisp", "acop"]},
    {"c": "Philippines", "auth": "PhilHealth", "url": "https://www.philhealth.gov.ph/circulars/", "base": "https://www.philhealth.gov.ph", "kw": ["premium rate", "income ceiling", "uhc"]},
    {"c": "Philippines", "auth": "Pag-IBIG (Housing)", "url": "https://www.pagibigfundservices.com/", "base": "https://www.pagibigfundservices.com", "kw": ["membership savings", "mp2", "calamity loan"]},

    # === 🇿🇼 ZIMBABWE (Multi-Currency) ===
    {"c": "Zimbabwe", "auth": "ZIMRA (Tax)", "url": "https://www.zimra.co.zw/public-notices", "base": "https://www.zimra.co.zw", "kw": ["paye", "tax tables", "currency", "zig", "qpd"]},
    {"c": "Zimbabwe", "auth": "NSSA (Social Security)", "url": "https://www.nssa.org.zw/document-library/", "base": "https://www.nssa.org.zw", "kw": ["insurable earnings", "ceiling", "pobs", "self-service"]},

    # === 🇰🇪 KENYA (The New Levies) ===
    {"c": "Kenya", "auth": "KRA (Tax)", "url": "https://www.kra.go.ke/news-center/public-notices", "base": "https://www.kra.go.ke", "kw": ["housing levy", "fringe benefit", "etims", "paye return"]},
    {"c": "Kenya", "auth": "NSSF (Social Security)", "url": "https://www.nssf.or.ke/public-notice", "base": "https://www.nssf.or.ke", "kw": ["tier i", "tier ii", "earnings limit", "act 2013"]},
    {"c": "Kenya", "auth": "SHA (Health)", "url": "https://sha.go.ke/resources/terms-conditions", "base": "https://sha.go.ke", "kw": ["shif", "2.75%", "empanelment", "household"]},

    # === 🇿🇲 ZAMBIA (The Practice Notes) ===
    {"c": "Zambia", "auth": "ZRA (Tax)", "url": "https://www.zra.org.zm/category/media-room/", "base": "https://www.zra.org.zm", "kw": ["practice note", "paye threshold", "tax credit", "property transfer"]},
    {"c": "Zambia", "auth": "NAPSA (Pension)", "url": "https://www.napsa.co.zm/about/publications", "base": "https://www.napsa.co.zm", "kw": ["ceiling", "contributions", "penalty waiver"]},
    {"c": "Zambia", "auth": "NHIMA (Health)", "url": "https://www.nhima.co.zm/publications/forms", "base": "https://www.nhima.co.zm", "kw": ["smartpay", "1%", "lapse rule"]},

    # === 🇺🇬 UGANDA (Enforcement) ===
    {"c": "Uganda", "auth": "URA (Tax)", "url": "https://ura.go.ug/en/publications/public-notices/", "base": "https://ura.go.ug", "kw": ["agency notice", "voluntary disclosure", "tax ledger"]},
    {"c": "Uganda", "auth": "NSSF (Social Security)", "url": "https://www.nssfug.org/media-center/legal/", "base": "https://www.nssfug.org", "kw": ["amendment act", "midterm access", "15%", "voluntary"]},

    # === 🇿🇦 SOUTH AFRICA (The Gazette) ===
    {"c": "South Africa", "auth": "SARS (Tax)", "url": "https://www.sars.gov.za/legal-counsel/interpretation-rulings/interpretation-notes/", "base": "https://www.sars.gov.za", "kw": ["interpretation note", "travel allowance", "fringe benefit"]},
    {"c": "South Africa", "auth": "Dept Employment", "url": "https://www.labour.gov.za/DocumentCenter/Pages/Government-Gazette.aspx", "base": "https://www.labour.gov.za", "kw": ["sectoral determination", "minimum wage", "coida", "earnings threshold"]}
]

# --- 3. TELEGRAM MESSENGER ---
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        # 60s timeout to handle slow networks
        requests.post(url, json=payload, timeout=60)
    except Exception as e:
        print(f"⚠️ Telegram Error: {e}")

# --- 4. THE INTELLIGENT SCOUT ---
def run_scout():
    print("🕵️ Global Compliance Scout Started...")
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}
    
    for t in TARGETS:
        try:
            print(f"Checking {t['c']} - {t['auth']}...")
            
            # 60s timeout for slow gov sites + verify=False for bad SSL
            try:
                r = requests.get(t['url'], headers=headers, timeout=60, verify=False)
            except requests.exceptions.RequestException as e:
                print(f"   ⚠️ Connection failed for {t['auth']}: {e}")
                continue

            soup = BeautifulSoup(r.text, 'html.parser')
            
            # Get first 8 links to be safe
            links = soup.find_all('a', href=True)[:8]
            
            for link in links:
                text = link.get_text(" ", strip=True)
                url = link['href']
                
                # CLEANUP URL
                if not url.startswith("http"):
                    base = t['base'].rstrip("/")
                    url = base + url if url.startswith("/") else base + "/" + url

                # KEYWORD FILTER (Crucial Step)
                # Must be >10 chars long AND contain a keyword from your list
                if len(text) > 10 and any(k in text.lower() for k in t['kw']):
                    
                    # GOOGLE NEWS CROSS-CHECK LINK
                    news_query = urllib.parse.quote(f"{t['c']} {t['auth']} {text[:40]}")
                    
                    # AI ANALYSIS
                    prompt = f"""
                    Role: Senior Payroll Compliance Architect.
                    Authority: {t['auth']} ({t['c']})
                    Document Title: "{text}"
                    Link: {url}
                    
                    Task: Analyze if this requires a CONFIGURATION CHANGE in an HRMS/Payroll System.
                    
                    Strict Rules:
                    1. IGNORE: Tenders, Transfers, Holidays, Meeting Minutes, General News.
                    2. ALERT ONLY IF: It impacts Tax Slabs, PF Rates, Minimum Wage, Housing Levy, Insurance, or Filing Deadlines.
                    
                    If IRRELEVANT -> Reply "SKIP"
                    If CRITICAL -> Reply EXACTLY:
                    🚨 *COMPLIANCE ALERT: {t['c'].upper()}*
                    *Authority:* {t['auth']}
                    *Update:* {text}
                    *Action:* [One sentence: e.g., "Update Tax Tables" or "Change PF Rate to 12%"]
                    *Link:* {url}
                    *Verify:* https://news.google.com/search?q={news_query}
                    """
                    
                    try:
                        res = model.generate_content(prompt)
                        ans = res.text.strip()
                        
                        if "SKIP" not in ans:
                            print(f"   ✅ Alerting {t['c']}!")
                            send_telegram(ans)
                            time.sleep(2) # Don't spam API
                        else:
                            print(f"   🗑️ AI Filtered: {text[:20]}...")
                            
                    except Exception as e:
                        print(f"   ⚠️ AI Error: {e}")
                        
        except Exception as e:
            print(f"❌ Critical Error {t['c']}: {e}")

if __name__ == "__main__":
    run_scout()
