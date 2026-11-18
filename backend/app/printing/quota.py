"""Page limits for the print queue.

A student may print PRINT_DAILY_PAGE_LIMIT sheets-worth of pages (pages x
copies) per UTC day. Pages are reserved when a job is confirmed (queued) and
released again if it is cancelled or fails.
"""
from datetime import datetime, time

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import PrintJob

COUNTED_STATUSES = ("sent_to_device", "queued", "printing", "completed")


def _today_start() -> datetime:
    return datetime.combine(datetime.utcnow().date(), time.min)


def usage(db: Session, student_id: str) -> dict:
    base = db.query(func.coalesce(func.sum(PrintJob.pages * PrintJob.copies), 0)).filter(
        PrintJob.student_id == student_id,
        PrintJob.confirmed_at >= _today_start(),
    )
    used = base.filter(PrintJob.status.in_(COUNTED_STATUSES)).scalar()
    reserved = base.filter(PrintJob.status.in_(("queued", "printing"))).scalar()
    limit = settings.print_daily_page_limit
    return {
        "daily_limit": limit,
        "used_today": int(used),
        "reserved": int(reserved),
        "remaining": max(0, limit - int(used)),
        "max_pages_per_job": settings.print_max_pages_per_job,
        "max_copies": settings.print_max_copies,
    }
