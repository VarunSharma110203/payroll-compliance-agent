import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
import os
import time
import io
import urllib3
from pypdf import PdfReader
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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

# --- TARGETS (Priority Sources) ---
TARGETS = [
    {"c": "India", "auth": "Simpliance Feed", "url": "https://icm.simpliance.in/gazette-notifications"},
    {"c": "India", "auth": "Income Tax", "url": "https://incometaxindia.gov.in/pages/communications/circulars.aspx"},
    {"c": "UAE", "auth": "MOHRE", "url": "https://www.mohre.gov.ae/en/laws-and-regulations/resolutions-and-circulars.aspx"},
    {"c": "Nigeria", "auth": "FIRS", "url": "https://www.firs.gov.ng/press-release/"},
    {"c": "Philippines", "auth": "BIR", "url": "https://www.bir.gov.ph/index.php/revenue-issuances/revenue-memorandum-circulars.html"},
    {"c": "Kenya", "auth": "KRA", "url": "https://www.kra.go.ke/news-center/public-notices"},
    {"c": "South Africa", "auth": "SARS", "url": "https://www.sars.gov.za/legal-counsel/interpretation-rulings/interpretation-notes/"},
    {"c": "Zimbabwe", "auth": "ZIMRA", "url": "https://www.zimra.co.zw/public-notices"},
    {"c": "Uganda", "auth": "URA", "url": "https://ura.go.ug/en/publications/public-notices/"}
]

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": True}
    try:
        requests.post(url, json=payload, timeout=20)
    except:
        pass

def create_session():
    session = requests.Session()
    retry = Retry(connect=3, backoff_factor=1)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)
    return session

# --- CONTENT EXTRACTOR ---
def get_content_from_url(session, url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}
        r = session.get(url, headers=headers, timeout=20, verify=False)
        content_type = r.headers.get('Content-Type', '').lower()
        
        # IF PDF -> READ IT
        if 'pdf' in content_type or url.lower().endswith('.pdf'):
            try:
                f = io.BytesIO(r.content)
                reader = PdfReader(f)
                text = ""
                # Read first 2 pages
                for page in reader.pages[:2]:
                    text += page.extract_text() + "\n"
                return f"PDF_TEXT: {text[:2000]}"
            except Exception as e:
                return f"ERROR_READING_PDF: {str(e)}"
        
        # IF WEBSITE -> READ IT
        else:
            soup = BeautifulSoup(r.text, 'html.parser')
            for script in soup(["script", "style", "nav", "footer"]):
                script.extract()
            text = soup.get_text()
            # Clean text
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = '\n'.join(chunk for chunk in chunks if chunk)
            return f"WEB_TEXT: {text[:2000]}"
    except Exception as e:
        return f"DOWNLOAD_ERROR: {str(e)}"

def run_audit():
    print("📜 Starting Deep Content Reader...")
    send_telegram("📜 **Deep Reader Activated**\n_Opening documents & reading text..._")
    
    session = create_session()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}

    for t in TARGETS:
        try:
            print(f"   Scanning {t['c']}...")
            try:
                r = session.get(t['url'], headers=headers, timeout=60, verify=False)
            except:
                send_telegram(f"⚠️ **{t['c']}**: Website Down/Blocked.")
                continue

            soup = BeautifulSoup(r.text, 'html.parser')
            links = soup.find_all('a', href=True)
            
            # GET TOP 5 VALID LINKS
            candidates = []
            for link in links:
                text = link.get_text(" ", strip=True)
                url = link['href']
                
                if len(text) > 10 and "javascript" not in url:
                    if not url.startswith("http"):
                        base = "/".join(t['url'].split("/")[:3]) if url.startswith("/") else t['url'] + "/"
                        url = base + url if url.startswith("/") else base + "/" + url # Messy url fix
                        # Cleaner URL fix
                        if not url.startswith("http"):
                             if url.startswith("/"):
                                base_domain = "/".join(t['url'].split("/")[:3])
                                url = base_domain + url
                             else:
                                url = t['url'] + "/" + url
                    
                    candidates.append({"title": text, "url": url})
                    if len(candidates) >= 5: # Stop at 5
                        break

            # ANALYZE CONTENT
            if not candidates:
                 send_telegram(f"⚠️ **{t['c']}**: No links found.")
                 continue

            findings = []
            for item in candidates:
                # 1. READ CONTENT
                content = get_content_from_url(session, item['url'])
                
                if "ERROR" in content:
                    continue # Skip broken files

                # 2. ASK AI
                prompt = f"""
                You are a Compliance Officer.
                Document: "{item['title']}"
                Content Snippet:
                {content}
                
                Task:
                1. Is this a **Critical Payroll/Tax Update**? 
                2. Ignore: Tenders, Holidays, Meetings, General News.
                3. If Critical, summarize in 1 sentence.
                4. If Junk, reply "SKIP".
                
                Output: [Date] [Summary]
                """
                
                try:
                    res = model.generate_content(prompt)
                    ans = res.text.strip()
                    if "SKIP" not in ans:
                        findings.append(f"📄 [{item['title']}]({item['url']})\n{ans}")
                        time.sleep(2)
                except Exception as e:
                    # Log the specific error if it happens again
                    print(f"AI Error: {e}")
                    pass

            if findings:
                report = f"🌍 **DEEP READ: {t['c'].upper()}**\n" + "\n\n".join(findings)
                send_telegram(report)
                time.sleep(4)

        except Exception as e:
            print(f"Error {t['c']}: {e}")

    send_telegram("✅ **Deep Read Complete.**")

if __name__ == "__main__":
    run_audit()
