"""Key figures for the start page (GET /api/dashboard/stats).

Computed from the archive (documents) and the review queue. Only the small
columns are loaded, never the stored files. Days and months follow the
local time zone (STATS_TZ, default Europe/Berlin) so "today" matches the user.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import current_user
from .db import Document, ReviewItem, User, get_session

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

AUTOMATIC = "worker"  # created_by of documents made by the inbox worker without a click


def _tz() -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(os.environ.get("STATS_TZ", "Europe/Berlin"))
    except Exception:  # no tz database in the image
        return UTC


class DayCount(BaseModel):
    date: date
    automatic: int = 0
    manual: int = 0


class MonthFigure(BaseModel):
    month: str  # YYYY-MM
    count: int = 0
    volume: Decimal = Decimal("0")  # gross, EUR only


class BuyerFigure(BaseModel):
    name: str
    count: int
    volume: Decimal


class Period(BaseModel):
    count: int = 0
    volume: Decimal = Decimal("0")


class ReviewFigures(BaseModel):
    open: int = 0
    resolved: int = 0  # in the period
    discarded: int = 0  # in the period
    avg_hours_to_resolve: float | None = None


class Stats(BaseModel):
    days: int
    currency: str = "EUR"
    daily: list[DayCount]
    monthly: list[MonthFigure]
    this_month: Period
    last_month: Period  # whole previous month
    last_month_to_date: Period  # previous month up to the same day, for a fair comparison
    automation_rate: float | None  # share of documents created by the worker in the period
    by_profile: dict[str, int]
    by_source: dict[str, int]
    top_buyers: list[BuyerFigure]
    review: ReviewFigures


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _months_back(d: date, n: int) -> date:
    y, m = d.year, d.month - n
    while m <= 0:
        y, m = y - 1, m + 12
    return date(y, m, 1)


def _local(dt: datetime, tz: tzinfo) -> datetime:
    if dt.tzinfo is None:  # SQLite returns naive UTC
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(tz)


def compute(session: Session, days: int = 30, now: datetime | None = None) -> Stats:
    tz = _tz()
    today = _local(now or datetime.now(UTC), tz).date()
    first_day = today - timedelta(days=days - 1)
    first_month = _months_back(today, 11)
    since = datetime.combine(min(first_day, _months_back(today, 1)), datetime.min.time(), tz)
    since = min(since, datetime.combine(first_month, datetime.min.time(), tz))

    rows = session.execute(
        select(
            Document.created_at,
            Document.created_by,
            Document.source,
            Document.profile,
            Document.buyer_name,
            Document.grand_total,
            Document.currency,
        ).where(Document.created_at >= since.astimezone(UTC))
    ).all()

    daily = {first_day + timedelta(days=i): DayCount(date=first_day + timedelta(days=i)) for i in range(days)}
    month_keys = [_month_key(_months_back(today, i)) for i in range(11, -1, -1)]
    monthly = {k: MonthFigure(month=k) for k in month_keys}
    this_key, last_key = _month_key(today), _month_key(_months_back(today, 1))
    last_to_date = Period()
    by_profile: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    buyers: dict[str, list] = defaultdict(lambda: [0, Decimal("0")])
    automatic = total = 0

    for created_at, created_by, source, profile, buyer, gross, currency in rows:
        day = _local(created_at, tz).date()
        eur = Decimal(gross or 0) if (currency or "EUR") == "EUR" else Decimal("0")
        month = monthly.get(_month_key(day))
        if month:
            month.count += 1
            month.volume += eur
        if month and month.month == last_key and day.day <= today.day:
            last_to_date.count += 1
            last_to_date.volume += eur
        if day in daily:
            total += 1
            is_auto = created_by == AUTOMATIC
            automatic += is_auto
            if is_auto:
                daily[day].automatic += 1
            else:
                daily[day].manual += 1
            by_profile[profile] += 1
            by_source[source] += 1
            b = buyers[buyer or "(ohne Namen)"]
            b[0] += 1
            b[1] += eur

    def period(key: str) -> Period:
        m = monthly.get(key)
        return Period(count=m.count, volume=m.volume) if m else Period()

    start = datetime.combine(first_day, datetime.min.time(), tz).astimezone(UTC)
    review_rows = session.execute(select(ReviewItem.status, ReviewItem.created_at, ReviewItem.updated_at)).all()
    rv = ReviewFigures()
    durations: list[float] = []
    for status, created_at, updated_at in review_rows:
        if status == "offen":
            rv.open += 1
            continue
        if _local(updated_at, UTC) < start:
            continue
        if status == "erledigt":
            rv.resolved += 1
            durations.append((_local(updated_at, UTC) - _local(created_at, UTC)).total_seconds() / 3600)
        elif status == "verworfen":
            rv.discarded += 1
    if durations:
        rv.avg_hours_to_resolve = round(sum(durations) / len(durations), 1)

    top = sorted(buyers.items(), key=lambda kv: (kv[1][1], kv[1][0]), reverse=True)[:5]
    return Stats(
        days=days,
        daily=list(daily.values()),
        monthly=list(monthly.values()),
        this_month=period(this_key),
        last_month=period(last_key),
        last_month_to_date=last_to_date,
        automation_rate=round(automatic / total, 3) if total else None,
        by_profile=dict(by_profile),
        by_source=dict(by_source),
        top_buyers=[BuyerFigure(name=n, count=c, volume=v) for n, (c, v) in top],
        review=rv,
    )


@router.get("/stats", response_model=Stats)
def stats(
    days: int = Query(30, ge=7, le=365),
    _: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Stats:
    """Key figures for the start page: documents per day/month, automation rate, formats, top buyers, review queue."""
    return compute(session, days)
