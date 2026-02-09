#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                    PAYROLL REGULATORY AUDIT AGENT v7.0                       ║
║                          "The Ultimate Scanner"                               ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Features:                                                                    ║
║  • Async parallel processing (10x faster)                                    ║
║  • Intelligent document detection with ML-like scoring                       ║
║  • Smart rate limiting per domain                                            ║
║  • PDF text extraction + OCR fallback indicator                              ║
║  • Gemini batch analysis with retry logic                                    ║
║  • Rich Telegram reports with categorization                                 ║
║  • SQLite with full audit trail                                              ║
║  • Comprehensive government repository coverage                              ║
╚══════════════════════════════════════════════════════════════════════════════╝

GitHub Actions Compatible - Uses payroll_audit.db for backward compatibility
"""

import asyncio
import aiohttp
import sqlite3
import os
import re
import io
import json
import hashlib
import sys
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Set
from enum import Enum
from collections import defaultdict
import time
import logging

# Sync imports for specific operations
import requests
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Optional PDF support
try:
    from pypdf import PdfReader
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False
    logger.warning("pypdf not installed. PDF content extraction disabled.")

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

class Config:
    """Central configuration"""
    # API Keys (from environment) - Updated to match GitHub secrets
    GEMINI_API_KEY = os.environ.get("GEMINI_KEY", "")
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")  # Changed from AUDIT_BOT_TOKEN
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

    # Database - using old name for backward compatibility
    DB_PATH = "payroll_audit.db"

    # Scanning
    MAX_CONCURRENT_REQUESTS = 8  # Reduced for stability on GitHub Actions
    REQUEST_TIMEOUT = 45  # Increased for slow government sites
    RATE_LIMIT_PER_DOMAIN = 1.5  # seconds between requests to same domain
    MAX_DOCUMENTS_PER_REPO = 30
    LOOKBACK_DAYS = 180  # 6 months

    # Content
    MAX_PDF_PAGES = 3
    MAX_PDF_SIZE_MB = 10
    MAX_CONTENT_LENGTH = 4000

    # Gemini
    GEMINI_MODEL = "gemini-1.5-flash"
    GEMINI_BATCH_SIZE = 3  # Smaller batches for stability
    GEMINI_RETRY_ATTEMPTS = 3
    GEMINI_RETRY_DELAY = 3

    @classmethod
    def validate(cls) -> bool:
        """Validate required environment variables"""
        missing = []
        if not cls.GEMINI_API_KEY:
            missing.append("GEMINI_KEY")
        if not cls.TELEGRAM_TOKEN:
            missing.append("TELEGRAM_TOKEN")
        if not cls.TELEGRAM_CHAT_ID:
            missing.append("TELEGRAM_CHAT_ID")

        if missing:
            logger.error(f"Missing environment variables: {', '.join(missing)}")
            return False

        logger.info("✅ All environment variables configured")
        return True


# ═══════════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class DocumentCategory(Enum):
    TAX = "tax"
    LABOR = "labor"
    PENSION = "pension"
    SOCIAL_SECURITY = "social_security"
    COMPLIANCE = "compliance"
    OTHER = "other"


@dataclass
class Repository:
    """Government repository configuration"""
    url: str
    country: str
    agency: str
    doc_type: str = "general"


@dataclass
class Document:
    """Extracted document"""
    url: str
    title: str
    country: str
    agency: str
    date_found: Optional[str] = None
    date_published: Optional[str] = None
    doc_id: Optional[str] = None
    content_snippet: Optional[str] = None
    is_pdf: bool = False
    relevance_score: float = 0.0
    category: DocumentCategory = DocumentCategory.OTHER
    ai_summary: Optional[str] = None
    is_relevant: bool = False

    def __hash__(self):
        return hash(self.url)

    def __eq__(self, other):
        return self.url == other.url


# ═══════════════════════════════════════════════════════════════════════════════
# PATTERN MATCHING (Pre-compiled for performance)
# ═══════════════════════════════════════════════════════════════════════════════

class Patterns:
    """Pre-compiled regex patterns"""

    DATES = [
        re.compile(r'(\d{1,2})[./-](\d{1,2})[./-](20\d{2})'),
        re.compile(r'(20\d{2})[./-](\d{1,2})[./-](\d{1,2})'),
        re.compile(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(20\d{2})', re.I),
        re.compile(r'(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December),?\s+(20\d{2})', re.I),
        re.compile(r'dated?\s*:?\s*(\d{1,2}[./-]\d{1,2}[./-]20\d{2})', re.I),
    ]

    DOC_IDS = [
        re.compile(r'(?:circular|notification|order|memo|advisory|resolution)\s*(?:no\.?|number|#)\s*([\w./-]+)', re.I),
        re.compile(r'(?:RMC|RMO|RR|DA|DO|LA)\s*(?:No\.?)?\s*([\d-]+)', re.I),
        re.compile(r'F\.?\s*No\.?\s*([\d/.-]+)', re.I),
        re.compile(r'(?:No\.?|Number)\s*([\d]+[/-][\d]+(?:[/-][\d]+)?)', re.I),
        re.compile(r'(?:S\.?O\.?|G\.?S\.?R\.?)\s*(\d+)', re.I),
        re.compile(r'\b(\d{1,4}[/-]20\d{2})\b'),
        re.compile(r'\b([A-Z]{2,5}[-/]?\d{2,5}[-/]?20\d{2})\b'),
    ]

    YEAR = re.compile(r'\b(202[3-6])\b')
    FILE_EXT = re.compile(r'\.(pdf|doc|docx|xls|xlsx|rtf)(?:\?|$)', re.I)


# ═══════════════════════════════════════════════════════════════════════════════
# FILTERS
# ═══════════════════════════════════════════════════════════════════════════════

class Filters:
    """Document filtering logic"""

    # Navigation/UI elements to BLOCK (comprehensive list)
    NAVIGATION_BLOCKLIST = frozenset([
        # Generic navigation
        'home', 'about', 'about us', 'contact', 'contact us', 'search', 'login',
        'logout', 'register', 'sign in', 'sign up', 'sign out', 'privacy',
        'privacy policy', 'terms', 'terms of service', 'terms and conditions',
        'sitemap', 'site map', 'cookie', 'cookies', 'disclaimer', 'legal',
        'help', 'faq', 'faqs', 'support', 'feedback',

        # UI elements
        'skip to content', 'skip to main content', 'skip navigation', 'main content',
        'screen reader', 'accessible mode', 'accessibility', 'text size', 'font size',
        'high contrast', 'print', 'share', 'email', 'tweet', 'facebook', 'twitter',
        'linkedin', 'instagram', 'youtube', 'social media', 'follow us', 'subscribe',
        'newsletter', 'logo', 'banner', 'header', 'footer', 'menu', 'navigation',
        'breadcrumb', 'back to top', 'scroll to top', 'read more', 'learn more',
        'click here', 'view all', 'see all', 'show more', 'load more', 'next',
        'previous', 'first', 'last', 'page', 'pagination', 'turn on more accessible mode',

        # Language selectors
        'english', 'hindi', 'हिंदी', 'arabic', 'العربية', 'français', 'español',
        'select language', 'change language', 'translate', 'नराकास',

        # Generic sections
        'services', 'all services', 'our services', 'products', 'solutions',
        'resources', 'downloads', 'documents', 'publications', 'media', 'news',
        'events', 'calendar', 'gallery', 'photos', 'videos', 'blog', 'articles',
        'press room', 'media center', 'media centre', 'newsroom', 'careers', 'jobs',
        'vacancies', 'recruitment', 'opportunities', 'work with us', 'tenders',
        'bids', 'procurement', 'auction', 'rfp', 'rfq', 'eoi',

        # Organization structure
        'who we are', 'leadership', 'management', 'board', 'directors', 'team',
        'staff', 'employees', 'departments', 'divisions', 'branches', 'offices',
        'locations', 'locate', 'find us', 'visit us', 'address', 'map', 'directions',
        'mission', 'vision', 'values', 'goals', 'objectives', 'history', 'milestones',
        'who\'s who', 'whos who', 'organogram', 'organization chart',

        # User types/portals
        'for employers', 'for employees', 'for individuals', 'for businesses',
        'for companies', 'for citizens', 'for taxpayers', 'for members',
        'employer services', 'employee services', 'citizen services',
        'for international workers', 'for office use', 'domestic worker',
        'domestic workers', 'partner services', 'international agreements',

        # Specific junk from your examples
        'help desk', 'toll free', 'tollfree', 'call center', 'customer care',
        'photo albums', 'awareness workshops', 'previous awareness workshops',
        'service centres', 'taxpayer service', 'approved services centers',
        'training institutes', 'capacity building', 'chief executive officer',
        'central govt. industrial tribunal', 'epf training institutes',
        'list of exempted establishment', 'perfor. evaluation of exempted estt',
        'cancellation/grant notification', 'exemption manuals and sops',
        'locate an epfo office', 'publications & media kit', 'labour market magazine',
        'info & services', 'by heads of income/subject', 'by status (individual/ huf etc.)',

        # Portal navigation
        'about pin', 'pin registration', 'pin dormancy', 'pin cancellation',
        'how to register', 'how to file', 'how to pay', 'how to print',
        'how to print your pin certificate', 'how to register for a kra pin',
        'how to deregister your pin', 'requirements for registration',
        'procedures for', 'guidelines for', 'procedures for motor vehicle',
        'tax registration', 'tax obligations', 'tax types', 'tax rates',
        'taxpayer segments', 'e-commerce', 'e-filing', 'e-payment', 'online services',
        'large taxpayer office', 'area offices', 'all gra offices', 'regional offices',
        'taxpayer service centres', 'links to register, file and pay taxes',
        'modified taxation scheme', 'e-commerce filing',

        # Tax type pages (category pages, not documents)
        'individual income tax', 'corporate income tax', 'value added tax (vat)',
        'pay as you earn (paye)', 'personal income tax (pit)', 'vehicle income tax (vit)',
        'withholding tax', 'tax compliance certificate (tcc)', 'installment tax',
        'advance tax', 'rental income tax', 'capital gains tax', 'turnover tax (tot)',
        'domestic tax', 'individual',

        # Category pages (not actual documents)
        'circulars', 'notifications', 'orders', 'resolutions', 'regulations',
        'acts', 'laws', 'rules', 'guidelines', 'manuals', 'handbooks',
        'forms', 'templates', 'formats', 'public notices', 'press releases',
        'resolutions & circulars', 'laws & regulations', 'guidance',
    ])

    DOCUMENT_KEYWORDS = frozenset([
        'circular', 'notification', 'order', 'resolution', 'memo', 'memorandum',
        'advisory', 'guideline', 'directive', 'gazette', 'notice', 'announcement',
        'amendment', 'act', 'bill', 'rule', 'regulation', 'ordinance', 'decree',
        'press release', 'public notice', 'practice note', 'interpretation note',
        'revenue memorandum', 'labor advisory', 'tax advisory',
    ])

    REGULATORY_KEYWORDS = frozenset([
        'income tax', 'corporate tax', 'vat', 'gst', 'customs', 'excise',
        'withholding tax', 'tds', 'tcs', 'capital gains', 'tax rate', 'tax slab',
        'tax exemption', 'tax deduction', 'tax credit', 'tax relief', 'tax rebate',
        'filing', 'return', 'assessment', 'levy', 'duty', 'cess', 'surcharge',
        'minimum wage', 'wage revision', 'wage rate', 'salary', 'remuneration',
        'overtime', 'working hours', 'leave', 'holiday', 'bonus', 'gratuity',
        'termination', 'retrenchment', 'layoff', 'severance', 'notice period',
        'employment', 'labor code', 'labour law', 'industrial relations',
        'provident fund', 'pension', 'superannuation', 'retirement', 'epf', 'ppf',
        'social security', 'insurance', 'esi', 'esic', 'health insurance',
        'contribution', 'employer contribution', 'employee contribution',
        'compliance', 'statutory', 'mandatory', 'deadline', 'due date',
        'penalty', 'interest', 'fine', 'prosecution', 'enforcement',
        'registration', 'license', 'permit', 'approval',
    ])

    @classmethod
    def is_navigation_junk(cls, text: str, url: str) -> bool:
        """Check if text/url is navigation junk"""
        normalized = ' '.join(text.lower().strip().split())
        url_lower = url.lower()

        # Too short = probably navigation
        if len(normalized) < 8:
            return True

        # Exact match to blocklist
        if normalized in cls.NAVIGATION_BLOCKLIST:
            return True

        # Partial match at start/end for short text
        for blocked in cls.NAVIGATION_BLOCKLIST:
            if len(normalized) < 50:
                if normalized.startswith(blocked) or normalized.endswith(blocked):
                    return True

        # Multiple blocklist words = junk
        blocklist_count = sum(1 for b in cls.NAVIGATION_BLOCKLIST if b in normalized)
        if blocklist_count >= 2 and len(normalized) < 60:
            return True

        # URL patterns that indicate navigation
        nav_url_patterns = [
            '/about', '/contact', '/login', '/register', '/search', '/help',
            '/faq', '/privacy', '/terms', '/sitemap', '/careers', '/jobs',
            '/services', '/products', '?lang=', '&lang=', '#',
            'javascript:', 'mailto:', 'tel:',
        ]
        for pattern in nav_url_patterns:
            if pattern in url_lower and '.pdf' not in url_lower:
                return True

        return False

    @classmethod
    def calculate_relevance_score(cls, text: str, url: str) -> float:
        """Calculate relevance score (0.0 to 1.0)"""
        score = 0.0
        text_lower = text.lower()
        url_lower = url.lower()

        # Is it a file? (+0.3)
        if Patterns.FILE_EXT.search(url_lower):
            score += 0.3

        # Has document ID? (+0.25)
        for pattern in Patterns.DOC_IDS:
            if pattern.search(text):
                score += 0.25
                break

        # Has date? (+0.15)
        for pattern in Patterns.DATES:
            if pattern.search(text):
                score += 0.15
                break

        # Has recent year? (+0.1)
        if Patterns.YEAR.search(text):
            score += 0.1

        # Document type keywords (+0.1 each, max 0.2)
        doc_keyword_count = sum(1 for kw in cls.DOCUMENT_KEYWORDS if kw in text_lower)
        score += min(doc_keyword_count * 0.1, 0.2)

        # Regulatory keywords (+0.05 each, max 0.15)
        reg_keyword_count = sum(1 for kw in cls.REGULATORY_KEYWORDS if kw in text_lower)
        score += min(reg_keyword_count * 0.05, 0.15)

        # URL contains document indicators (+0.1)
        url_indicators = ['/circular', '/notification', '/order', '/gazette', '/notice', 'download', 'attachment']
        if any(ind in url_lower for ind in url_indicators):
            score += 0.1

        # Length bonus
        if len(text) > 30:
            score += 0.05
        if len(text) > 60:
            score += 0.05

        return min(score, 1.0)

    @classmethod
    def passes_filter(cls, text: str, url: str, min_score: float = 0.25) -> Tuple[bool, float]:
        """Main filter function"""
        if cls.is_navigation_junk(text, url):
            return False, 0.0

        score = cls.calculate_relevance_score(text, url)
        return score >= min_score, score


# ═══════════════════════════════════════════════════════════════════════════════
# REPOSITORIES
# ═══════════════════════════════════════════════════════════════════════════════

REPOSITORIES: List[Repository] = [
    # INDIA
    Repository("https://incometaxindia.gov.in/pages/communications/circulars.aspx", "India", "Income Tax", "circular"),
    Repository("https://incometaxindia.gov.in/pages/communications/notifications.aspx", "India", "Income Tax", "notification"),
    Repository("https://www.epfindia.gov.in/site_en/Circulars.php", "India", "EPFO", "circular"),
    Repository("https://www.esic.gov.in/circulars", "India", "ESIC", "circular"),

    # UAE
    Repository("https://www.mohre.gov.ae/en/laws-and-regulations/resolutions-and-circulars.aspx", "UAE", "MOHRE", "resolution"),
    Repository("https://tax.gov.ae/en/content/guides.references.aspx", "UAE", "FTA", "guide"),

    # PHILIPPINES
    Repository("https://www.bir.gov.ph/index.php/revenue-issuances/revenue-memorandum-circulars.html", "Philippines", "BIR", "circular"),
    Repository("https://www.bir.gov.ph/index.php/revenue-issuances/revenue-memorandum-orders.html", "Philippines", "BIR", "order"),
    Repository("https://www.dole.gov.ph/issuances/labor-advisories/", "Philippines", "DOLE", "advisory"),
    Repository("https://www.philhealth.gov.ph/circulars/", "Philippines", "PhilHealth", "circular"),
    Repository("https://www.pagibigfund.gov.ph/circulars.html", "Philippines", "Pag-IBIG", "circular"),

    # KENYA
    Repository("https://www.kra.go.ke/news-center/public-notices", "Kenya", "KRA", "notice"),

    # NIGERIA
    Repository("https://www.firs.gov.ng/press-release/", "Nigeria", "FIRS", "press_release"),
    Repository("https://pencom.gov.ng/category/circulars/", "Nigeria", "PenCom", "circular"),

    # GHANA
    Repository("https://gra.gov.gh/practice-notes/", "Ghana", "GRA", "practice_note"),
    Repository("https://www.ssnit.org.gh/news-events/", "Ghana", "SSNIT", "news"),

    # SOUTH AFRICA
    Repository("https://www.sars.gov.za/legal-counsel/secondary-legislation/public-notices/", "South Africa", "SARS", "notice"),
]


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

class Database:
    """SQLite database handler"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
        self._init_db()

    def _init_db(self):
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS documents (
                url TEXT PRIMARY KEY,
                url_hash TEXT UNIQUE,
                country TEXT NOT NULL,
                agency TEXT,
                title TEXT,
                doc_id TEXT,
                date_found TEXT,
                date_published TEXT,
                relevance_score REAL,
                category TEXT,
                ai_summary TEXT,
                is_relevant INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_country ON documents(country);
            CREATE INDEX IF NOT EXISTS idx_relevant ON documents(is_relevant);
            CREATE INDEX IF NOT EXISTS idx_url_hash ON documents(url_hash);

            CREATE TABLE IF NOT EXISTS scan_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_date TEXT,
                docs_found INTEGER,
                docs_relevant INTEGER,
                duration_seconds REAL
            );
        ''')
        self.conn.commit()

    def is_new_url(self, url: str) -> bool:
        url_hash = hashlib.md5(url.encode()).hexdigest()
        cursor = self.conn.execute('SELECT 1 FROM documents WHERE url_hash = ?', (url_hash,))
        return cursor.fetchone() is None

    def bulk_check_urls(self, urls: List[str]) -> Set[str]:
        if not urls:
            return set()

        url_hashes = {hashlib.md5(url.encode()).hexdigest(): url for url in urls}
        placeholders = ','.join('?' * len(url_hashes))

        cursor = self.conn.execute(
            f'SELECT url_hash FROM documents WHERE url_hash IN ({placeholders})',
            list(url_hashes.keys())
        )

        existing = {row[0] for row in cursor.fetchall()}
        return {url for hash_, url in url_hashes.items() if hash_ not in existing}

    def save_document(self, doc: Document):
        url_hash = hashlib.md5(doc.url.encode()).hexdigest()

        self.conn.execute('''
            INSERT OR REPLACE INTO documents
            (url, url_hash, country, agency, title, doc_id, date_found, date_published,
             relevance_score, category, ai_summary, is_relevant)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            doc.url, url_hash, doc.country, doc.agency, doc.title, doc.doc_id,
            doc.date_found, doc.date_published, doc.relevance_score,
            doc.category.value if doc.category else None, doc.ai_summary,
            1 if doc.is_relevant else 0
        ))
        self.conn.commit()

    def save_scan_log(self, found: int, relevant: int, duration: float):
        self.conn.execute('''
            INSERT INTO scan_log (scan_date, docs_found, docs_relevant, duration_seconds)
            VALUES (?, ?, ?, ?)
        ''', (datetime.now().isoformat(), found, relevant, duration))
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# CONTENT EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

class ContentExtractor:
    """Extract content from PDFs and web pages"""

    @staticmethod
    async def extract_pdf(session: aiohttp.ClientSession, url: str) -> Tuple[str, bool]:
        if not PDF_SUPPORT:
            return "[PDF extraction not available]", False

        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=Config.REQUEST_TIMEOUT), ssl=False) as response:
                if response.status != 200:
                    return f"[HTTP {response.status}]", False

                content_length = response.headers.get('content-length')
                if content_length and int(content_length) > Config.MAX_PDF_SIZE_MB * 1024 * 1024:
                    return "[PDF too large]", False

                content = await response.read()
                pdf_file = io.BytesIO(content)
                reader = PdfReader(pdf_file)

                text = ""
                for page in reader.pages[:Config.MAX_PDF_PAGES]:
                    extracted = page.extract_text()
                    if extracted:
                        text += extracted + "\n"

                if len(text.strip()) < 50:
                    return "[Scanned PDF - no text]", False

                return text[:Config.MAX_CONTENT_LENGTH], True

        except Exception as e:
            return f"[PDF Error: {str(e)[:50]}]", False

    @staticmethod
    async def extract_webpage(session: aiohttp.ClientSession, url: str) -> Tuple[str, bool]:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=Config.REQUEST_TIMEOUT), ssl=False) as response:
                if response.status != 200:
                    return f"[HTTP {response.status}]", False

                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')

                for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'menu', 'noscript']):
                    tag.decompose()

                text = soup.get_text(separator=' ', strip=True)
                clean_text = ' '.join(text.split())

                return clean_text[:Config.MAX_CONTENT_LENGTH], True

        except Exception as e:
            return f"[Page Error: {str(e)[:50]}]", False


# ═══════════════════════════════════════════════════════════════════════════════
# AI ANALYSIS (Gemini)
# ═══════════════════════════════════════════════════════════════════════════════

class GeminiAnalyzer:
    """Analyze documents using Gemini AI"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = f"https://generativelanguage.googleapis.com/v1beta/models/{Config.GEMINI_MODEL}:generateContent"

    async def analyze(self, doc: Document, session: aiohttp.ClientSession) -> Tuple[bool, str, DocumentCategory]:
        """Analyze a document"""
        has_content = doc.content_snippet and not doc.content_snippet.startswith('[')

        if has_content:
            prompt = f"""You are a Payroll Compliance Auditor for {doc.country}.

DOCUMENT:
Title: "{doc.title}"
Agency: {doc.agency}
Content:
---
{doc.content_snippet[:2000]}
---

Is this a relevant regulatory update for Payroll, Tax, or Labor Law?

RELEVANT: Tax rates, social security, pension, wages, compliance deadlines
NOT RELEVANT: News, tenders, jobs, events, organizational updates

RESPOND EXACTLY AS:
RELEVANT: [YES/NO]
CATEGORY: [TAX/LABOR/PENSION/SOCIAL_SECURITY/COMPLIANCE/OTHER]
SUMMARY: [One sentence summary]"""
        else:
            prompt = f"""You are a Payroll Compliance Auditor for {doc.country}.

TITLE: "{doc.title}"
Agency: {doc.agency}
(Content unavailable)

Based on title only, is this likely a relevant regulatory update?

RESPOND EXACTLY AS:
RELEVANT: [YES/NO]
CATEGORY: [TAX/LABOR/PENSION/SOCIAL_SECURITY/COMPLIANCE/OTHER]
SUMMARY: [One sentence about what this likely covers]"""

        for attempt in range(Config.GEMINI_RETRY_ATTEMPTS):
            try:
                url = f"{self.base_url}?key={self.api_key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 150}
                }

                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status == 200:
                        result = await response.json()
                        answer = result['candidates'][0]['content']['parts'][0]['text'].strip()
                        return self._parse(answer)
                    elif response.status == 429:
                        await asyncio.sleep(Config.GEMINI_RETRY_DELAY * (attempt + 1))
                    else:
                        return False, f"API Error: {response.status}", DocumentCategory.OTHER

            except Exception as e:
                if attempt < Config.GEMINI_RETRY_ATTEMPTS - 1:
                    await asyncio.sleep(Config.GEMINI_RETRY_DELAY)
                else:
                    return False, f"Error: {str(e)[:50]}", DocumentCategory.OTHER

        return False, "Max retries", DocumentCategory.OTHER

    def _parse(self, answer: str) -> Tuple[bool, str, DocumentCategory]:
        lines = answer.strip().split('\n')

        is_relevant = False
        category = DocumentCategory.OTHER
        summary = "Analysis unavailable"

        for line in lines:
            line = line.strip()
            if line.upper().startswith('RELEVANT:'):
                is_relevant = 'YES' in line.upper()
            elif line.upper().startswith('CATEGORY:'):
                cat_str = line.split(':', 1)[1].strip().upper()
                category_map = {
                    'TAX': DocumentCategory.TAX,
                    'LABOR': DocumentCategory.LABOR,
                    'LABOUR': DocumentCategory.LABOR,
                    'PENSION': DocumentCategory.PENSION,
                    'SOCIAL_SECURITY': DocumentCategory.SOCIAL_SECURITY,
                    'COMPLIANCE': DocumentCategory.COMPLIANCE,
                }
                category = category_map.get(cat_str, DocumentCategory.OTHER)
            elif line.upper().startswith('SUMMARY:'):
                summary = line.split(':', 1)[1].strip()

        return is_relevant, summary, category


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════════

class TelegramReporter:
    """Send reports to Telegram"""

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    async def send(self, message: str) -> bool:
        if len(message) > 4000:
            message = message[:3900] + "\n\n_(truncated)_"

        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    return response.status == 200
        except:
            return False

    async def send_country_report(self, country: str, docs: List[Document]) -> bool:
        if not docs:
            return True

        category_emojis = {
            DocumentCategory.TAX: "💰",
            DocumentCategory.LABOR: "👷",
            DocumentCategory.PENSION: "🏦",
            DocumentCategory.SOCIAL_SECURITY: "🛡️",
            DocumentCategory.COMPLIANCE: "📋",
            DocumentCategory.OTHER: "📄",
        }

        by_category = defaultdict(list)
        for doc in docs:
            by_category[doc.category].append(doc)

        msg = f"🚨 *{country.upper()} - REGULATORY UPDATES*\n"
        msg += f"_{len(docs)} new document(s)_\n\n"

        for category, cat_docs in by_category.items():
            emoji = category_emojis.get(category, "📄")
            msg += f"{emoji} *{category.value.replace('_', ' ').title()}*\n"

            for doc in cat_docs[:5]:
                safe_title = doc.title[:80].replace('*', '').replace('_', '').replace('[', '(').replace(']', ')')
                safe_summary = (doc.ai_summary or "")[:100].replace('*', '').replace('_', '')

                msg += f"\n• *{safe_title}*\n"
                if doc.doc_id:
                    msg += f"  📝 {doc.doc_id}\n"
                if safe_summary:
                    msg += f"  💡 {safe_summary}\n"
                if doc.date_published:
                    msg += f"  📅 {doc.date_published}\n"
                msg += f"  [🔗 Open]({doc.url})\n"

            msg += "\n"

        return await self.send(msg)

    async def send_summary(self, total: int, relevant: int, countries: int, duration: float):
        msg = f"""✅ *AUDIT COMPLETE*

📊 *Results:*
• Documents analyzed: *{total}*
• Relevant updates: *{relevant}*
• Countries: *{countries}*
• Duration: *{duration:.0f}s*

📅 {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}"""
        await self.send(msg)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN SCANNER
# ═══════════════════════════════════════════════════════════════════════════════

class Scanner:
    """Main scanner"""

    def __init__(self):
        self.db = Database(Config.DB_PATH)
        self.analyzer = GeminiAnalyzer(Config.GEMINI_API_KEY)
        self.reporter = TelegramReporter(Config.TELEGRAM_TOKEN, Config.TELEGRAM_CHAT_ID)
        self.rate_limiter = defaultdict(float)

    async def run(self):
        start_time = time.time()

        logger.info("=" * 60)
        logger.info("🚀 PAYROLL REGULATORY AUDIT v7.0")
        logger.info("=" * 60)

        connector = aiohttp.TCPConnector(ssl=False, limit=Config.MAX_CONCURRENT_REQUESTS)

        async with aiohttp.ClientSession(connector=connector) as session:
            # Scan repositories
            logger.info(f"📡 Scanning {len(REPOSITORIES)} repositories...")
            all_docs = await self._scan_repos(session)
            logger.info(f"📊 Found {len(all_docs)} potential documents")

            # Filter to new only
            all_urls = [d.url for d in all_docs]
            new_urls = self.db.bulk_check_urls(all_urls)
            new_docs = [d for d in all_docs if d.url in new_urls]
            logger.info(f"🆕 {len(new_docs)} are new")

            if not new_docs:
                logger.info("✅ No new documents")
                await self.reporter.send("✅ *AUDIT COMPLETE*\n\nNo new updates found.")
                return

            # Analyze
            logger.info(f"🤖 Analyzing with Gemini...")
            relevant = await self._analyze(session, new_docs)
            logger.info(f"✅ {len(relevant)} relevant")

            # Save all
            for doc in new_docs:
                self.db.save_document(doc)

            # Report
            if relevant:
                logger.info("📤 Sending reports...")
                await self._report(relevant)

            # Summary
            duration = time.time() - start_time
            countries = len(set(d.country for d in relevant))

            await self.reporter.send_summary(len(new_docs), len(relevant), countries, duration)
            self.db.save_scan_log(len(new_docs), len(relevant), duration)

            logger.info(f"✅ COMPLETE in {duration:.0f}s")

    async def _scan_repos(self, session: aiohttp.ClientSession) -> List[Document]:
        tasks = [self._scan_repo(session, repo) for repo in REPOSITORIES]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_docs = []
        for result in results:
            if isinstance(result, list):
                all_docs.extend(result)

        return all_docs

    async def _scan_repo(self, session: aiohttp.ClientSession, repo: Repository) -> List[Document]:
        domain = urlparse(repo.url).netloc
        await self._rate_limit(domain)

        try:
            async with session.get(repo.url, timeout=aiohttp.ClientTimeout(total=Config.REQUEST_TIMEOUT), ssl=False) as response:
                if response.status != 200:
                    logger.warning(f"   ⚠️ {repo.agency}: HTTP {response.status}")
                    return []

                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                docs = []

                for link in soup.find_all('a', href=True):
                    href = link.get('href', '').strip()
                    text = link.get_text(separator=' ', strip=True)

                    if not href or not text or len(text) < 5:
                        continue

                    full_url = urljoin(repo.url, href)

                    if href.startswith('#') or href.startswith('javascript:'):
                        continue

                    link_domain = urlparse(full_url).netloc
                    if link_domain != domain and not full_url.lower().endswith('.pdf'):
                        continue

                    passes, score = Filters.passes_filter(text, full_url)

                    if passes:
                        doc_id = None
                        for p in Patterns.DOC_IDS:
                            m = p.search(text)
                            if m:
                                doc_id = m.group(1) if m.groups() else m.group(0)
                                break

                        date_pub = None
                        for p in Patterns.DATES:
                            m = p.search(text)
                            if m:
                                date_pub = m.group(0)
                                break

                        docs.append(Document(
                            url=full_url,
                            title=text[:200],
                            country=repo.country,
                            agency=repo.agency,
                            date_found=datetime.now().isoformat(),
                            date_published=date_pub,
                            doc_id=doc_id,
                            is_pdf=full_url.lower().endswith('.pdf'),
                            relevance_score=score,
                        ))

                logger.info(f"   ✓ {repo.country}/{repo.agency}: {len(docs)} candidates")
                return docs[:Config.MAX_DOCUMENTS_PER_REPO]

        except Exception as e:
            logger.warning(f"   ⚠️ {repo.agency}: {str(e)[:40]}")
            return []

    async def _analyze(self, session: aiohttp.ClientSession, docs: List[Document]) -> List[Document]:
        relevant = []

        for i in range(0, len(docs), Config.GEMINI_BATCH_SIZE):
            batch = docs[i:i + Config.GEMINI_BATCH_SIZE]

            # Get content
            content_tasks = []
            for doc in batch:
                if doc.is_pdf:
                    content_tasks.append(ContentExtractor.extract_pdf(session, doc.url))
                else:
                    content_tasks.append(ContentExtractor.extract_webpage(session, doc.url))

            contents = await asyncio.gather(*content_tasks, return_exceptions=True)

            for doc, result in zip(batch, contents):
                if isinstance(result, tuple):
                    doc.content_snippet = result[0]

            # Analyze
            analysis_tasks = [self.analyzer.analyze(doc, session) for doc in batch]
            analyses = await asyncio.gather(*analysis_tasks, return_exceptions=True)

            for doc, result in zip(batch, analyses):
                if isinstance(result, tuple):
                    is_rel, summary, category = result
                    doc.is_relevant = is_rel
                    doc.ai_summary = summary
                    doc.category = category

                    if is_rel:
                        relevant.append(doc)
                        logger.info(f"      ✅ {doc.title[:50]}...")

            await asyncio.sleep(0.5)

        return relevant

    async def _report(self, docs: List[Document]):
        by_country = defaultdict(list)
        for doc in docs:
            by_country[doc.country].append(doc)

        for country, country_docs in by_country.items():
            await self.reporter.send_country_report(country, country_docs)
            await asyncio.sleep(1)

    async def _rate_limit(self, domain: str):
        last = self.rate_limiter[domain]
        elapsed = time.time() - last
        if elapsed < Config.RATE_LIMIT_PER_DOMAIN:
            await asyncio.sleep(Config.RATE_LIMIT_PER_DOMAIN - elapsed)
        self.rate_limiter[domain] = time.time()

    def close(self):
        self.db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

async def main():
    if not Config.validate():
        sys.exit(1)

    scanner = Scanner()
    try:
        await scanner.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        scanner.close()


if __name__ == "__main__":
    asyncio.run(main())
