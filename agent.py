import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
import os
import time
import urllib.parse
import urllib3

# --- 0. SUPPRESS SSL WARNINGS ---
# Government websites often have bad certificates. We ignore them to prevent crashes.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 1. CONFIGURATION ---
try:
    GENAI_API_KEY = os.environ["GEMINI_KEY"]
    TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
    TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
except KeyError:
    print("❌ ERROR: Keys not found! Check GitHub Secrets.")
    exit(1)

genai.configure(api_key=GENAI_API_KEY)
# Using 'gemini-pro' because it is stable and reliable for this task
model = genai.GenerativeModel('gemini-pro')

# --- 2. THE MASTER COMPLIANCE LIST (9 COUNTRIES) ---
# Includes Broad Keywords to catch "concepts" not just exact words.

TARGETS = [
    # === 🇮🇳 INDIA (The Big 5) ===
    {
        "c": "India", "auth": "Income Tax (CBDT)", 
        "url": "https://incometaxindia.gov.in/pages/communications/circulars.aspx", 
        "base": "https://incometaxindia.gov.in", 
        "kw": ["tds", "salary", "192", "form 16", "exemption", "rebate", "surcharge", "cess", "80c", "80d", "hra", "lta", "perquisite", "relief", "pan", "tan", "deduction", "income", "finance act"]
    },
    {
        "c": "India", "auth": "Income Tax (Notifications)", 
        "url": "https://incometaxindia.gov.in/pages/communications/notifications.aspx", 
        "base": "https://incometaxindia.gov.in", 
        "kw": ["amendment", "rule", "statutory order", "s.o.", "notification", "cost inflation", "index", "valuation", "taxability"]
    },
    {
        "c": "India", "auth": "EPFO (Provident Fund)", 
        "url": "https://www.epfindia.gov.in/site_en/Circulars.php", 
        "base": "https://www.epfindia.gov.in", 
        "kw": ["interest", "rate", "wage", "ceiling", "contribution", "aadhaar", "uan", "kyc", "damages", "penalty", "edli", "eps", "pension", "digital", "claim"]
    },
    {
        "c": "India", "auth": "Ministry of Labour", 
        "url": "https://labour.gov.in/circulars", 
        "base": "https://labour.gov.in", 
        "kw": ["minimum wage", "vda", "dearness", "allowance", "bonus", "gratuity", "maternity", "leave", "encashment", "overtime", "shift", "standing order", "code on wages", "osh"]
    },
    {
        "c": "India", "auth": "ESIC", 
        "url": "https://www.esic.gov.in/circulars", 
        "base": "https://www.esic.gov.in", 
        "kw": ["contribution", "rate", "threshold", "limit", "medical", "benefit", "sickness", "disablement", "challan", "abry", "scheme"]
    },

    # === 🇦🇪 UAE (Emiratisation & Tax) ===
    {
        "c": "UAE", "auth": "MOHRE (Labour)", 
        "url": "https://www.mohre.gov.ae/en/laws-and-regulations/resolutions-and-circulars.aspx", 
        "base": "https://www.mohre.gov.ae", 
        "kw": ["emiratisation", "nafis", "quota", "target", "fine", "penalty", "wps", "wage protection", "work permit", "contract", "gratuity", "end of service", "unemployment", "insurance", "iloel"]
    },
    {
        "c": "UAE", "auth": "FTA (Tax)", 
        "url": "https://tax.gov.ae/en/taxes/Vat.aspx", 
        "base": "https://tax.gov.ae", 
        "kw": ["corporate tax", "employment", "income", "salary", "director", "remuneration", "withholding", "exempt", "threshold", "registration", "deadline"]
    },

    # === 🇳🇬 NIGERIA (Federal & Pension) ===
    {
        "c": "Nigeria", "auth": "FIRS (Tax)", 
        "url": "https://www.firs.gov.ng/press-release/", 
        "base": "https://www.firs.gov.ng", 
        "kw": ["paye", "wht", "withholding", "relief", "personal income", "pita", "finance act", "deadline", "return", "tax clearance", "tcc", "penalty", "waiver", "interest"]
    },
    {
        "c": "Nigeria", "auth": "PenCom", 
        "url": "https://www.pencom.gov.ng/category/regulations-guidelines-circulars-frameworks/circulars/", 
        "base": "https://www.pencom.gov.ng", 
        "kw": ["pension", "contribution", "rate", "employer", "employee", "voluntary", "rsa", "recapture", "pfa", "compliance", "certificate"]
    },

    # === 🇵🇭 PHILIPPINES (Statutory) ===
    {
        "c": "Philippines", "auth": "BIR (Tax)", 
        "url": "https://www.bir.gov.ph/index.php/revenue-issuances/revenue-memorandum-circulars.html", 
        "base": "https://www.bir.gov.ph", 
        "kw": ["withholding", "tax", "compensation", "1601-c", "alphalist", "2316", "13th month", "bonus", "de minimis", "exemption", "table", "rate", "orus"]
    },
    {
        "c": "Philippines", "auth": "PhilHealth", 
        "url": "https://www.philhealth.gov.ph/circulars/", 
        "base": "https://www.philhealth.gov.ph", 
        "kw": ["premium", "rate", "contribution", "increase", "table", "income", "ceiling", "floor", "uhc", "universal"]
    },
    {
        "c": "Philippines", "auth": "SSS & Pag-IBIG", 
        "url": "https://www.sss.gov.ph/sss-circulars/", 
        "base": "https://www.sss.gov.ph", 
        "kw": ["contribution", "schedule", "msp", "wisp", "provident", "fund", "housing", "savings", "loan", "condonation", "penalty"]
    },

    # === 🇰🇪 KENYA (Levies) ===
    {
        "c": "Kenya", "auth": "KRA & NSSF", 
        "url": "https://www.kra.go.ke/news-center/public-notices", 
        "base": "https://www.kra.go.ke", 
        "kw": ["housing levy", "ahl", "relief", "insurance", "paye", "tax", "resident", "nssf", "tier", "earnings", "limit", "rate", "shif", "sha", "health"]
    },

    # === 🇿🇼 ZIMBABWE (Multi-Currency) ===
    {
        "c": "Zimbabwe", "auth": "ZIMRA", 
        "url": "https://www.zimra.co.zw/public-notices", 
        "base": "https://www.zimra.co.zw", 
        "kw": ["paye", "tax table", "currency", "usd", "zig", "rate", "threshold", "exempt", "bonus", "nssa", "insurable", "pobs", "apwcs"]
    },

    # === 🇿🇦 SOUTH AFRICA (Gazette) ===
    {
        "c": "South Africa", "auth": "SARS & Labour", 
        "url": "https://www.sars.gov.za/legal-counsel/interpretation-rulings/interpretation-notes/", 
        "base": "https://www.sars.gov.za", 
        "kw": ["paye", "uif", "sdl", "eti", "allowance", "fringe", "benefit", "subsistence", "travel", "reimbursement", "minimum wage", "nmw", "sectoral", "earnings"]
    },

    # === 🇿🇲 ZAMBIA (Practice Notes) ===
    {
        "c": "Zambia", "auth": "ZRA (Tax)", 
        "url": "https://www.zra.org.zm/category/media-room/", 
        "base": "https://www.zra.org.zm", 
        "kw": ["practice note", "paye threshold", "tax credit", "property transfer", "exemption", "relief"]
    },
    {
        "c": "Zambia", "auth": "NAPSA (Pension)", 
        "url": "https://www.napsa.co.zm/about/publications", 
        "base": "https://www.napsa.co.zm", 
        "kw": ["ceiling", "contributions", "penalty waiver", "earnings limit", "social security"]
    },

    # === 🇺🇬 UGANDA (Enforcement) ===
    {
        "c": "Uganda", "auth": "URA (Tax)", 
        "url": "https://ura.go.ug/en/publications/public-notices/", 
        "base": "https://ura.go.ug", 
        "kw": ["agency notice", "voluntary disclosure", "tax ledger", "paye", "amnesty"]
    },
    {
        "c": "Uganda", "auth": "NSSF (Social Security)", 
        "url": "https://www.nssfug.org/media-center/legal/", 
        "base": "https://www.nssfug.org", 
        "kw": ["amendment act", "midterm access", "15%", "voluntary", "contribution"]
    }
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
    
    # PASTE THIS LINE HERE 👇
    send_telegram("👋 System Check: Payroll Compliance Agent is ONLINE.")
    
    headers = {'User-Agent': 'Mozilla/5.0...'}
    
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
