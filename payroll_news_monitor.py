#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║              PAYROLL NEWS MONITOR - Google News RSS + Gemini                ║
║                    Daily Statutory Change Tracker                           ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Countries: India, UAE, Philippines, Nigeria, Zimbabwe                     ║
║  Source: Google News RSS (free) + Gemini Flash (summarization)             ║
║  Delivery: Telegram bot                                                    ║
║  Schedule: Daily 9 AM IST via GitHub Actions                               ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import aiohttp
import sqlite3
import os
import sys
import hashlib
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
from collections import defaultdict
from urllib.parse import quote_plus
from html import unescape
import re
import json

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

class Config:
    GEMINI_API_KEY = os.environ.get("GEMINI_KEY", "")
    TELEGRAM_TOKEN = os.environ.get("AUDIT_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

    DB_PATH = "payroll_notifications.db"
    GEMINI_MODEL = "gemini-2.0-flash"
    LOOKBACK_HOURS = 48  # catch anything from last 48h to avoid timezone gaps
    REQUEST_TIMEOUT = 30

    @classmethod
    def validate(cls) -> bool:
        missing = []
        if not cls.GEMINI_API_KEY:
            missing.append("GEMINI_KEY")
        if not cls.TELEGRAM_TOKEN:
            missing.append("AUDIT_BOT_TOKEN")
        if not cls.TELEGRAM_CHAT_ID:
            missing.append("TELEGRAM_CHAT_ID")
        if missing:
            logger.error(f"Missing environment variables: {', '.join(missing)}")
            return False
        logger.info("All environment variables configured")
        return True


# ═══════════════════════════════════════════════════════════════════════════════
# SEARCH QUERIES PER COUNTRY
# ═══════════════════════════════════════════════════════════════════════════════

COUNTRY_QUERIES: Dict[str, List[str]] = {
    "India": [
        "India payroll tax changes 2025 2026",
        "India income tax slab amendment notification",
        "India PF EPF EPFO contribution rate circular",
        "India ESI ESIC rate change notification",
        "India minimum wage revision notification",
        "India TDS rate change notification",
        "India labour code rules gazette notification",
        "India professional tax slab change",
        "India gratuity bonus amendment",
        "India payroll compliance deadline filing",
        "India new labour law implementation",
        "India NPS pension contribution change",
    ],
    "UAE": [
        "UAE payroll law changes 2025 2026",
        "UAE MOHRE labor law amendment resolution",
        "UAE WPS wage protection system update",
        "UAE corporate tax payroll impact",
        "UAE end of service gratuity calculation change",
        "UAE minimum wage update",
        "UAE pension GPSSA contribution change",
        "UAE employment law amendment decree",
        "UAE MOHRE ministerial resolution labour",
    ],
    "Philippines": [
        "Philippines payroll tax change 2025 2026",
        "Philippines BIR revenue regulation withholding tax",
        "Philippines SSS contribution rate increase",
        "Philippines PhilHealth premium rate change",
        "Philippines Pag-IBIG HDMF contribution update",
        "Philippines minimum wage order region",
        "Philippines DOLE labor advisory",
        "Philippines 13th month pay compliance",
        "Philippines TRAIN law tax update",
        "Philippines DOLE department order labor",
    ],
    "Nigeria": [
        "Nigeria payroll tax changes 2025 2026",
        "Nigeria PAYE income tax amendment",
        "Nigeria pension PenCom contribution rate",
        "Nigeria NSITF employee compensation change",
        "Nigeria NHF national housing fund update",
        "Nigeria minimum wage amendment",
        "Nigeria FIRS tax circular notification",
        "Nigeria labour act amendment",
        "Nigeria ITF industrial training fund levy",
        "Nigeria finance act payroll impact",
    ],
    "Zimbabwe": [
        "Zimbabwe payroll tax changes 2025 2026",
        "Zimbabwe ZIMRA PAYE tax table change",
        "Zimbabwe NSSA pension contribution rate",
        "Zimbabwe minimum wage gazette notification",
        "Zimbabwe labour law amendment statutory",
        "Zimbabwe AIDS levy tax change",
        "Zimbabwe standards development levy update",
        "Zimbabwe finance act tax amendment",
        "Zimbabwe statutory instrument labour",
    ],
}

# ═══════════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class NewsArticle:
    title: str
    link: str
    source: str
    published: str
    country: str
    query: str
    content_snippet: str = ""
    ai_summary: str = ""
    category: str = ""  # statutory_change / compliance_deadline / general
    is_relevant: bool = False
    relevance_reason: str = ""

    @property
    def url_hash(self) -> str:
        return hashlib.md5(self.link.encode()).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

class Database:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS sent_articles (
                url_hash TEXT PRIMARY KEY,
                url TEXT,
                title TEXT,
                country TEXT,
                category TEXT,
                ai_summary TEXT,
                sent_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS scan_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_date TEXT,
                articles_fetched INTEGER,
                articles_relevant INTEGER,
                articles_sent INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_sent_hash ON sent_articles(url_hash);
        ''')
        self.conn.commit()

    def is_already_sent(self, url_hash: str) -> bool:
        cursor = self.conn.execute(
            'SELECT 1 FROM sent_articles WHERE url_hash = ?', (url_hash,)
        )
        return cursor.fetchone() is not None

    def mark_sent(self, article: NewsArticle):
        self.conn.execute('''
            INSERT OR IGNORE INTO sent_articles (url_hash, url, title, country, category, ai_summary)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (article.url_hash, article.link, article.title, article.country,
              article.category, article.ai_summary))
        self.conn.commit()

    def log_scan(self, fetched: int, relevant: int, sent: int):
        self.conn.execute('''
            INSERT INTO scan_log (scan_date, articles_fetched, articles_relevant, articles_sent)
            VALUES (?, ?, ?, ?)
        ''', (datetime.utcnow().isoformat(), fetched, relevant, sent))
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE NEWS RSS FETCHER
# ═══════════════════════════════════════════════════════════════════════════════

class GoogleNewsRSS:
    """Fetch news from Google News RSS feeds"""

    BASE_URL = "https://news.google.com/rss/search"

    @staticmethod
    def _build_url(query: str) -> str:
        encoded = quote_plus(query)
        # when:1d = last 24 hours
        return f"{GoogleNewsRSS.BASE_URL}?q={encoded}+when:2d&hl=en&gl=US&ceid=US:en"

    @staticmethod
    def _parse_rss(xml_text: str, country: str, query: str) -> List[NewsArticle]:
        articles = []
        try:
            root = ET.fromstring(xml_text)
            channel = root.find('channel')
            if channel is None:
                return articles

            for item in channel.findall('item'):
                title_el = item.find('title')
                link_el = item.find('link')
                pub_date_el = item.find('pubDate')
                source_el = item.find('source')

                title = unescape(title_el.text.strip()) if title_el is not None and title_el.text else ""
                link = link_el.text.strip() if link_el is not None and link_el.text else ""
                pub_date = pub_date_el.text.strip() if pub_date_el is not None and pub_date_el.text else ""
                source = source_el.text.strip() if source_el is not None and source_el.text else "Unknown"

                # Extract snippet from description (HTML content)
                desc_el = item.find('description')
                snippet = ""
                if desc_el is not None and desc_el.text:
                    # Strip HTML tags for a plain text snippet
                    snippet = re.sub(r'<[^>]+>', ' ', unescape(desc_el.text))
                    snippet = ' '.join(snippet.split())[:500]

                if title and link:
                    articles.append(NewsArticle(
                        title=title,
                        link=link,
                        source=source,
                        published=pub_date,
                        country=country,
                        query=query,
                        content_snippet=snippet,
                    ))

        except ET.ParseError as e:
            logger.warning(f"RSS parse error: {e}")

        return articles

    @staticmethod
    async def fetch_all(session: aiohttp.ClientSession) -> List[NewsArticle]:
        all_articles: List[NewsArticle] = []
        seen_links = set()

        tasks = []
        for country, queries in COUNTRY_QUERIES.items():
            for query in queries:
                tasks.append((country, query))

        # Fetch in batches to be polite to Google
        batch_size = 5
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]
            coros = []
            for country, query in batch:
                coros.append(GoogleNewsRSS._fetch_one(session, country, query))

            results = await asyncio.gather(*coros, return_exceptions=True)

            for result in results:
                if isinstance(result, list):
                    for article in result:
                        if article.link not in seen_links:
                            seen_links.add(article.link)
                            all_articles.append(article)

            # Small delay between batches
            if i + batch_size < len(tasks):
                await asyncio.sleep(1)

        logger.info(f"Fetched {len(all_articles)} unique articles from Google News RSS")
        return all_articles

    @staticmethod
    async def _fetch_one(
        session: aiohttp.ClientSession, country: str, query: str
    ) -> List[NewsArticle]:
        url = GoogleNewsRSS._build_url(query)
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=Config.REQUEST_TIMEOUT),
                headers={"User-Agent": "PayrollNewsMonitor/1.0"}
            ) as response:
                if response.status != 200:
                    logger.warning(f"RSS fetch failed for '{query}': HTTP {response.status}")
                    return []
                xml_text = await response.text()
                articles = GoogleNewsRSS._parse_rss(xml_text, country, query)
                if articles:
                    logger.info(f"  {country} | '{query[:40]}...' -> {len(articles)} articles")
                return articles
        except Exception as e:
            logger.warning(f"RSS error for '{query[:30]}': {str(e)[:50]}")
            return []


# ═══════════════════════════════════════════════════════════════════════════════
# GEMINI AI ANALYZER
# ═══════════════════════════════════════════════════════════════════════════════

class GeminiAnalyzer:
    """Use Gemini Flash to filter and summarize articles"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{Config.GEMINI_MODEL}:generateContent"
        )

    async def analyze_batch(
        self, articles: List[NewsArticle], session: aiohttp.ClientSession
    ) -> List[NewsArticle]:
        """Analyze articles in batches using Gemini"""
        if not articles:
            return []

        # Group by country for better context
        by_country: Dict[str, List[NewsArticle]] = defaultdict(list)
        for a in articles:
            by_country[a.country].append(a)

        relevant_articles = []

        for country, country_articles in by_country.items():
            # Process in chunks of 25 articles per Gemini call (fewer API calls = less rate limiting)
            for i in range(0, len(country_articles), 25):
                chunk = country_articles[i:i + 25]
                result = await self._analyze_chunk(country, chunk, session)
                relevant_articles.extend(result)
                await asyncio.sleep(15)  # Respect free tier rate limits

        logger.info(f"Gemini analysis: {len(relevant_articles)} relevant out of {len(articles)}")
        return relevant_articles

    async def _analyze_chunk(
        self, country: str, articles: List[NewsArticle], session: aiohttp.ClientSession
    ) -> List[NewsArticle]:
        article_list = ""
        for idx, a in enumerate(articles, 1):
            article_list += f"\n[{idx}] Title: {a.title}\n"
            article_list += f"    Source: {a.source}\n"
            article_list += f"    Date: {a.published}\n"
            if a.content_snippet:
                article_list += f"    Snippet: {a.content_snippet[:200]}\n"

        prompt = f"""You are a Payroll Compliance Analyst for a payroll software company (PeopleHum).
Your job is to identify news articles that report ACTUAL statutory or regulatory changes
affecting employee payroll processing in {country}.

RELEVANT articles include:
- Changes to tax slabs, rates, or thresholds (income tax, PAYE, TDS, professional tax)
- Social security contribution rate changes (PF, ESI, SSS, PhilHealth, NSSA, NSITF, pension)
- Minimum wage revisions or notifications
- New labor law implementations or amendments affecting payroll
- Compliance filing deadlines or due date changes
- Government circulars/notifications impacting salary computation
- Changes to gratuity, bonus, leave encashment rules
- New statutory deductions or levies

NOT RELEVANT:
- General business news or company earnings
- Political commentary about proposed (not enacted) changes
- Job market reports or hiring trends
- HR best practices or opinion pieces
- Technology/software product announcements
- Strikes, protests, or union negotiations (unless resulting in actual policy change)
- Duplicate/repeat coverage of the same update already listed

ARTICLES:
{article_list}

For each article, respond in this EXACT JSON format:
{{
  "results": [
    {{
      "index": 1,
      "relevant": true,
      "category": "statutory_change",
      "summary": "One-line summary focusing on what changed and the impact on payroll processing",
      "action_item": "What a payroll product manager should do about this"
    }}
  ]
}}

Categories: "statutory_change", "compliance_deadline", "general_payroll"
Only include articles where relevant=true. Omit irrelevant ones entirely.
Be strict - only include articles about CONFIRMED changes, not speculation."""

        for attempt in range(5):
            try:
                url = f"{self.base_url}?key={self.api_key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.1,
                        "maxOutputTokens": 4000,
                        "responseMimeType": "application/json",
                    }
                }

                async with session.post(
                    url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        answer = result['candidates'][0]['content']['parts'][0]['text']
                        return self._parse_response(answer, articles)
                    elif response.status == 429:
                        wait_time = 30 * (attempt + 1)  # 30s, 60s, 90s, 120s, 150s
                        logger.warning(f"Gemini rate limited, waiting {wait_time}s... (attempt {attempt + 1}/5)")
                        await asyncio.sleep(wait_time)
                    else:
                        error_text = await response.text()
                        logger.warning(f"Gemini API error {response.status}: {error_text[:100]}")
                        return []

            except Exception as e:
                logger.warning(f"Gemini error (attempt {attempt + 1}): {str(e)[:80]}")
                if attempt < 4:
                    await asyncio.sleep(15)

        logger.warning(f"Gemini failed after 5 attempts for {country} chunk, skipping")
        return []

    def _parse_response(
        self, answer: str, articles: List[NewsArticle]
    ) -> List[NewsArticle]:
        relevant = []
        try:
            # Clean potential markdown code blocks
            clean = answer.strip()
            if clean.startswith("```"):
                clean = re.sub(r'^```(?:json)?\s*', '', clean)
                clean = re.sub(r'\s*```$', '', clean)

            data = json.loads(clean)
            results = data.get("results", [])

            for r in results:
                idx = r.get("index", 0) - 1  # Convert to 0-based
                if 0 <= idx < len(articles) and r.get("relevant", False):
                    article = articles[idx]
                    article.is_relevant = True
                    article.category = r.get("category", "general_payroll")
                    article.ai_summary = r.get("summary", "")
                    action = r.get("action_item", "")
                    if action:
                        article.ai_summary += f" | Action: {action}"
                    relevant.append(article)

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Failed to parse Gemini response: {e}")
            # Fallback: try line-by-line parsing
            relevant.extend(self._fallback_parse(answer, articles))

        return relevant

    def _fallback_parse(
        self, answer: str, articles: List[NewsArticle]
    ) -> List[NewsArticle]:
        """Fallback parser if JSON fails"""
        relevant = []
        for idx, article in enumerate(articles):
            # Check if this article index is mentioned as relevant
            pattern = rf'\b{idx + 1}\b.*?relevant.*?true'
            if re.search(pattern, answer, re.I | re.S):
                article.is_relevant = True
                article.category = "general_payroll"
                article.ai_summary = article.title
                relevant.append(article)
        return relevant


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM REPORTER
# ═══════════════════════════════════════════════════════════════════════════════

class TelegramReporter:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    async def send_message(self, text: str) -> bool:
        if len(text) > 4000:
            text = text[:3900] + "\n\n_(truncated)_"

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as response:
                    if response.status != 200:
                        error = await response.text()
                        logger.warning(f"Telegram error: {error[:100]}")
                        # Retry without markdown if it fails
                        payload["parse_mode"] = None
                        async with session.post(
                            f"https://api.telegram.org/bot{self.token}/sendMessage",
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=15),
                        ) as retry_resp:
                            return retry_resp.status == 200
                    return True
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    async def send_report(self, articles_by_country: Dict[str, List[NewsArticle]]) -> int:
        """Send formatted country-wise reports, returns count of messages sent"""
        sent_count = 0

        country_flags = {
            "India": "🇮🇳",
            "UAE": "🇦🇪",
            "Philippines": "🇵🇭",
            "Nigeria": "🇳🇬",
            "Zimbabwe": "🇿🇼",
        }

        category_labels = {
            "statutory_change": "📜 Statutory Change",
            "compliance_deadline": "📅 Compliance Deadline",
            "general_payroll": "📰 Payroll Update",
        }

        for country, articles in articles_by_country.items():
            if not articles:
                continue

            flag = country_flags.get(country, "🌍")
            msg = f"{flag} *{country.upper()} — PAYROLL UPDATES*\n"
            msg += f"_{len(articles)} update(s) found_\n"
            msg += "━" * 30 + "\n\n"

            # Group by category
            by_cat: Dict[str, List[NewsArticle]] = defaultdict(list)
            for a in articles:
                by_cat[a.category].append(a)

            for cat, cat_articles in by_cat.items():
                cat_label = category_labels.get(cat, "📄 Other")
                msg += f"*{cat_label}*\n\n"

                for a in cat_articles[:8]:  # Max 8 per category
                    # Sanitize for Telegram markdown
                    safe_title = (a.title[:100]
                                  .replace('*', '')
                                  .replace('_', '')
                                  .replace('[', '(')
                                  .replace(']', ')'))
                    safe_summary = (a.ai_summary[:200]
                                    .replace('*', '')
                                    .replace('_', '')
                                    .replace('[', '(')
                                    .replace(']', ')'))

                    msg += f"• *{safe_title}*\n"
                    if safe_summary:
                        msg += f"  ↳ {safe_summary}\n"
                    msg += f"  📰 {a.source} | {a.published[:16] if a.published else 'Recent'}\n"
                    msg += f"  [Read more]({a.link})\n\n"

                msg += "\n"

            # Split if too long
            if len(msg) > 4000:
                # Send first part
                split_point = msg[:3800].rfind('\n\n')
                if split_point == -1:
                    split_point = 3800
                await self.send_message(msg[:split_point])
                sent_count += 1
                await asyncio.sleep(1)

                remainder = f"{flag} *{country.upper()} — CONTINUED*\n\n" + msg[split_point:]
                await self.send_message(remainder)
                sent_count += 1
            else:
                await self.send_message(msg)
                sent_count += 1

            await asyncio.sleep(1)  # Avoid Telegram rate limits

        return sent_count

    async def send_no_updates(self):
        msg = (
            "✅ *PAYROLL NEWS SCAN COMPLETE*\n\n"
            "No new statutory or compliance updates found today "
            "for India, UAE, Philippines, Nigeria, or Zimbabwe.\n\n"
            f"📅 {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
        )
        await self.send_message(msg)

    async def send_summary(self, fetched: int, relevant: int, sent: int, duration: float):
        msg = (
            f"📊 *SCAN SUMMARY*\n\n"
            f"• Articles scanned: *{fetched}*\n"
            f"• Relevant updates: *{relevant}*\n"
            f"• Messages sent: *{sent}*\n"
            f"• Duration: *{duration:.0f}s*\n\n"
            f"📅 {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
        )
        await self.send_message(msg)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

async def main():
    if not Config.validate():
        sys.exit(1)

    start_time = time.time()
    logger.info("=" * 60)
    logger.info("PAYROLL NEWS MONITOR - Starting scan")
    logger.info(f"Countries: {', '.join(COUNTRY_QUERIES.keys())}")
    logger.info("=" * 60)

    db = Database(Config.DB_PATH)
    analyzer = GeminiAnalyzer(Config.GEMINI_API_KEY)
    reporter = TelegramReporter(Config.TELEGRAM_TOKEN, Config.TELEGRAM_CHAT_ID)

    try:
        # Step 1: Fetch RSS feeds
        logger.info("Step 1: Fetching Google News RSS feeds...")
        async with aiohttp.ClientSession() as session:
            all_articles = await GoogleNewsRSS.fetch_all(session)

        total_fetched = len(all_articles)
        logger.info(f"Total articles fetched: {total_fetched}")

        if not all_articles:
            logger.info("No articles found from RSS feeds")
            await reporter.send_no_updates()
            db.log_scan(0, 0, 0)
            return

        # Step 2: Filter out already-sent articles
        new_articles = [a for a in all_articles if not db.is_already_sent(a.url_hash)]
        logger.info(f"New articles (not previously sent): {len(new_articles)}")

        if not new_articles:
            logger.info("All articles already sent previously")
            await reporter.send_no_updates()
            db.log_scan(total_fetched, 0, 0)
            return

        # Step 3: AI analysis with Gemini
        logger.info("Step 2: Analyzing with Gemini Flash...")
        async with aiohttp.ClientSession() as session:
            relevant_articles = await analyzer.analyze_batch(new_articles, session)

        logger.info(f"Relevant articles after AI analysis: {len(relevant_articles)}")

        if not relevant_articles:
            logger.info("No relevant payroll updates found after AI filtering")
            await reporter.send_no_updates()
            db.log_scan(total_fetched, 0, 0)
            return

        # Step 4: Group by country and send
        logger.info("Step 3: Sending Telegram reports...")
        by_country: Dict[str, List[NewsArticle]] = defaultdict(list)
        for a in relevant_articles:
            by_country[a.country].append(a)

        sent_count = await reporter.send_report(by_country)

        # Step 5: Mark as sent in DB
        for a in relevant_articles:
            db.mark_sent(a)

        # Step 6: Summary
        duration = time.time() - start_time
        await reporter.send_summary(total_fetched, len(relevant_articles), sent_count, duration)
        db.log_scan(total_fetched, len(relevant_articles), sent_count)

        logger.info(f"COMPLETE in {duration:.0f}s | "
                     f"Fetched: {total_fetched} | "
                     f"Relevant: {len(relevant_articles)} | "
                     f"Sent: {sent_count}")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        try:
            await reporter.send_message(f"⚠️ *NEWS MONITOR ERROR*\n\n`{str(e)[:200]}`")
        except Exception:
            pass
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
