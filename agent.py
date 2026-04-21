"""Payroll regulatory digest agent.

For each configured country, asks Gemini 2.5 Flash (with Google Search
grounding) to identify payroll-related regulatory changes in the last
LOOKBACK_DAYS. Dedups against state/seen.json. Posts a Markdown digest
to Telegram grouped by severity.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

from countries import COUNTRIES, Country
from telegram_reporter import TelegramReporter

LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "14"))
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "4"))
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_KEY = os.environ.get("GEMINI_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

STATE_DIR = Path(__file__).parent / "state"
SEEN_FILE = STATE_DIR / "seen.json"

GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

VALID_CATEGORIES = {"TAX", "SOCIAL_SECURITY", "LABOR", "MINIMUM_WAGE", "OTHER"}
VALID_SEVERITIES = {"HIGH", "MEDIUM", "LOW"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("agent")


@dataclass
class Finding:
    country_code: str
    country_name: str
    region: str
    title: str
    summary: str
    effective_date: str
    source_url: str
    source_authority: str
    category: str
    severity: str

    def hash_id(self) -> str:
        key = f"{self.country_code}|{self.title.lower().strip()}|{self.effective_date}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]


PROMPT_TEMPLATE = """You are a payroll regulatory intelligence analyst.

Task: Identify ACTUAL payroll-related regulatory changes for {country_name} ({country_code}) that were announced, gazetted, or took effect in the last {lookback_days} days (today is {today}).

Use Google Search to find authoritative sources. Prioritize official bodies over news commentary. In {country_name} the relevant authorities include: {authorities}.

INCLUDE changes to:
- Personal income tax / PAYE / withholding tax rates, slabs, reliefs
- Statutory social security contributions (rates, wage ceilings, new schemes)
- Minimum wage
- Statutory leave, overtime, gratuity, end-of-service, severance rules
- New payroll levies or mandatory funds
- Labor law amendments that affect payroll calculations

EXCLUDE:
- Corporate tax, VAT/GST, customs, import/export duty
- News commentary, op-eds, speculation, rumours
- Draft bills not yet passed into law (unless a gazette/royal decree was issued)
- General compliance reminders with no rule change

Return a JSON array. Each element must have exactly these fields:
{{
  "title": "short imperative title of the change",
  "summary": "1-2 sentence factual summary — what changed, from what to what",
  "effective_date": "YYYY-MM-DD if known, otherwise 'unknown'",
  "source_url": "URL to the most authoritative source (gazette, circular, ministry page)",
  "source_authority": "name of the issuing body (e.g. EPFO, KRA, MOHRE)",
  "category": "one of: TAX, SOCIAL_SECURITY, LABOR, MINIMUM_WAGE, OTHER",
  "severity": "HIGH (rate/slab/contribution change with near-term effective date), MEDIUM (threshold/relief/guidance with real impact), LOW (clarifications, extensions, minor admin changes)"
}}

If you cannot find any qualifying changes, return exactly: []

Output ONLY the JSON array — no prose, no markdown fences, no commentary."""


def validate_env() -> bool:
    missing = [
        k for k, v in [
            ("GEMINI_KEY", GEMINI_KEY),
            ("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
            ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
        ] if not v
    ]
    if missing:
        log.error("Missing env: %s", ", ".join(missing))
        return False
    return True


def load_seen() -> set[str]:
    if not SEEN_FILE.exists():
        return set()
    try:
        data = json.loads(SEEN_FILE.read_text())
        return set(data.get("hashes", []))
    except Exception as e:
        log.warning("seen.json unreadable (%s); starting empty", e)
        return set()


def save_seen(hashes: set[str]) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    payload = {
        "last_run": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(hashes),
        "hashes": sorted(hashes),
    }
    SEEN_FILE.write_text(json.dumps(payload, indent=2) + "\n")


def extract_json_array(text: str) -> list[dict]:
    """Pull a JSON array out of a model response, tolerating code fences."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError as e:
        log.warning("JSON parse failed: %s", e)
        return []


async def call_gemini(session: aiohttp.ClientSession, country: Country) -> list[dict]:
    today = datetime.now(timezone.utc).date().isoformat()
    prompt = PROMPT_TEMPLATE.format(
        country_name=country.name,
        country_code=country.code,
        lookback_days=LOOKBACK_DAYS,
        today=today,
        authorities=", ".join(country.authorities),
    )
    payload: dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": 0.15,
            "maxOutputTokens": 4096,
        },
    }
    url = f"{GEMINI_URL}?key={GEMINI_KEY}"

    for attempt in range(3):
        try:
            async with session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=90)
            ) as resp:
                if resp.status == 429:
                    wait = 4 * (attempt + 1)
                    log.warning("[%s] rate-limited, waiting %ds", country.code, wait)
                    await asyncio.sleep(wait)
                    continue
                if resp.status != 200:
                    body = (await resp.text())[:300]
                    log.warning("[%s] HTTP %s: %s", country.code, resp.status, body)
                    return []
                data = await resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    log.warning("[%s] no candidates in response", country.code)
                    return []
                parts = candidates[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
                return extract_json_array(text)
        except asyncio.TimeoutError:
            log.warning("[%s] timeout on attempt %d", country.code, attempt + 1)
        except Exception as e:
            log.warning("[%s] error: %s", country.code, e)
            return []
    return []


def coerce_finding(country: Country, raw: dict) -> Finding | None:
    try:
        title = str(raw.get("title", "")).strip()
        summary = str(raw.get("summary", "")).strip()
        source_url = str(raw.get("source_url", "")).strip()
        if not (title and summary and source_url):
            return None
        if not source_url.startswith("http"):
            return None
        category = str(raw.get("category", "OTHER")).upper().strip()
        if category not in VALID_CATEGORIES:
            category = "OTHER"
        severity = str(raw.get("severity", "LOW")).upper().strip()
        if severity not in VALID_SEVERITIES:
            severity = "LOW"
        return Finding(
            country_code=country.code,
            country_name=country.name,
            region=country.region,
            title=title[:200],
            summary=summary[:500],
            effective_date=str(raw.get("effective_date", "unknown"))[:20],
            source_url=source_url[:500],
            source_authority=str(raw.get("source_authority", "")).strip()[:100],
            category=category,
            severity=severity,
        )
    except Exception as e:
        log.warning("coerce failed: %s", e)
        return None


async def scan_country(
    session: aiohttp.ClientSession,
    country: Country,
    sem: asyncio.Semaphore,
) -> list[Finding]:
    async with sem:
        raw_items = await call_gemini(session, country)
        findings: list[Finding] = []
        for item in raw_items:
            f = coerce_finding(country, item)
            if f is not None:
                findings.append(f)
        log.info("[%s] %s: %d findings", country.code, country.name, len(findings))
        return findings


async def run() -> int:
    if not validate_env():
        return 1

    start = time.time()
    seen = load_seen()
    log.info("Starting scan: %d countries, %d-day window, %d already seen",
             len(COUNTRIES), LOOKBACK_DAYS, len(seen))

    sem = asyncio.Semaphore(MAX_CONCURRENT)
    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT * 2, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [scan_country(session, c, sem) for c in COUNTRIES]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_findings: list[Finding] = []
    for r in results:
        if isinstance(r, list):
            all_findings.extend(r)
        elif isinstance(r, Exception):
            log.warning("task exception: %s", r)

    new_findings = [f for f in all_findings if f.hash_id() not in seen]
    log.info("Total findings: %d | new: %d", len(all_findings), len(new_findings))

    reporter = TelegramReporter(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
    duration = time.time() - start

    if not new_findings:
        await reporter.send_empty_digest(len(COUNTRIES), LOOKBACK_DAYS, duration)
    else:
        await reporter.send_digest(new_findings, LOOKBACK_DAYS, duration)

    for f in new_findings:
        seen.add(f.hash_id())
    save_seen(seen)

    log.info("Done in %.1fs", duration)
    return 0


def main() -> None:
    code = asyncio.run(run())
    sys.exit(code)


if __name__ == "__main__":
    main()
