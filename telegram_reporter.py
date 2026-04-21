"""Telegram digest sender. HTML parse mode — less escaping pain than Markdown."""

from __future__ import annotations

import html
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterable

import aiohttp

if TYPE_CHECKING:
    from agent import Finding

log = logging.getLogger("telegram")

TELEGRAM_LIMIT = 4000  # leave headroom under the 4096 hard cap

FLAG = {
    "IN": "🇮🇳", "SG": "🇸🇬", "MY": "🇲🇾", "TH": "🇹🇭", "VN": "🇻🇳",
    "PH": "🇵🇭", "AE": "🇦🇪", "SA": "🇸🇦", "QA": "🇶🇦", "OM": "🇴🇲",
    "KW": "🇰🇼", "BH": "🇧🇭", "KE": "🇰🇪", "UG": "🇺🇬", "TZ": "🇹🇿",
    "RW": "🇷🇼", "MW": "🇲🇼", "ZA": "🇿🇦", "ZW": "🇿🇼", "ZM": "🇿🇲",
    "NA": "🇳🇦", "MU": "🇲🇺", "NG": "🇳🇬", "GH": "🇬🇭", "CM": "🇨🇲",
    "AO": "🇦🇴", "CD": "🇨🇩",
}

SEVERITY_BADGE = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "⚪"}
SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

CATEGORY_LABEL = {
    "TAX": "Tax",
    "SOCIAL_SECURITY": "Social Security",
    "LABOR": "Labor",
    "MINIMUM_WAGE": "Minimum Wage",
    "OTHER": "Other",
}


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def format_finding(f: "Finding") -> str:
    flag = FLAG.get(f.country_code, "")
    badge = SEVERITY_BADGE.get(f.severity, "")
    category = CATEGORY_LABEL.get(f.category, f.category)
    lines = [
        f"{badge} {flag} <b>{esc(f.country_name)}</b> · {esc(f.source_authority or category)}",
        f"<b>{esc(f.title)}</b>",
        esc(f.summary),
    ]
    meta = []
    if f.effective_date and f.effective_date != "unknown":
        meta.append(f"📅 {esc(f.effective_date)}")
    meta.append(f"🏷 {esc(category)}")
    lines.append(" · ".join(meta))
    lines.append(f'<a href="{esc(f.source_url)}">Source</a>')
    return "\n".join(lines)


def build_messages(findings: Iterable["Finding"], lookback_days: int, duration: float) -> list[str]:
    findings = sorted(
        findings,
        key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.region, f.country_name),
    )
    by_sev: dict[str, list["Finding"]] = defaultdict(list)
    for f in findings:
        by_sev[f.severity].append(f)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    header = (
        f"<b>🌍 Payroll Regulatory Digest · {today}</b>\n"
        f"<i>{len(findings)} new change(s) · last {lookback_days} days · "
        f"scan took {duration:.0f}s</i>\n"
    )

    blocks: list[str] = [header]
    for sev in ("HIGH", "MEDIUM", "LOW"):
        items = by_sev.get(sev, [])
        if not items:
            continue
        blocks.append(f"\n<b>{SEVERITY_BADGE[sev]} {sev} ({len(items)})</b>\n")
        for f in items:
            blocks.append("\n" + format_finding(f) + "\n")

    messages: list[str] = []
    buf = ""
    for block in blocks:
        if len(buf) + len(block) > TELEGRAM_LIMIT and buf:
            messages.append(buf)
            buf = block
        else:
            buf += block
    if buf:
        messages.append(buf)
    return messages


class TelegramReporter:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.api = f"https://api.telegram.org/bot{token}/sendMessage"

    async def _send(self, text: str) -> bool:
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.api, json=payload, timeout=aiohttp.ClientTimeout(total=20)
                ) as resp:
                    if resp.status != 200:
                        body = (await resp.text())[:300]
                        log.warning("Telegram %s: %s", resp.status, body)
                        return False
                    return True
        except Exception as e:
            log.warning("Telegram error: %s", e)
            return False

    async def send_digest(self, findings: list["Finding"], lookback_days: int, duration: float) -> None:
        messages = build_messages(findings, lookback_days, duration)
        for msg in messages:
            await self._send(msg)

    async def send_empty_digest(self, country_count: int, lookback_days: int, duration: float) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        text = (
            f"<b>🌍 Payroll Regulatory Digest · {today}</b>\n"
            f"<i>No new changes across {country_count} countries · "
            f"last {lookback_days} days · scan took {duration:.0f}s</i>"
        )
        await self._send(text)
