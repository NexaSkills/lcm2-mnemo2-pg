"""UTC period window parsing for lcm_recent (today/yesterday/Nd/week/month/
date:YYYY-MM-DD/last Nh)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def window_bounds(period: str) -> tuple[datetime, datetime, str]:
    now = datetime.now(timezone.utc)
    p = (period or "today").strip().lower()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if p == "today":
        return today, now, "today"
    if p == "yesterday":
        return today - timedelta(days=1), today, "yesterday"
    if p == "week":
        return today - timedelta(days=7), now, "last 7d"
    if p == "month":
        return today - timedelta(days=30), now, "last 30d"
    if p.startswith("last ") and p.endswith("h") and p[5:-1].isdigit():
        return now - timedelta(hours=int(p[5:-1])), now, p
    if p.endswith("d") and p[:-1].isdigit():
        return today - timedelta(days=int(p[:-1])), now, p
    if p.startswith("date:"):
        d = datetime.fromisoformat(p[5:]).replace(tzinfo=timezone.utc)
        return d, d + timedelta(days=1), p
    return today, now, "today"
