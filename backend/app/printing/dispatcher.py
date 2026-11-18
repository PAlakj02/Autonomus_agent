"""Background worker that drains the print queue for the `mock` and `cups`
backends. (With the `agent` backend, jobs stay `queued` until the remote
print agent claims them via /api/printer-agent.)

Runs one job at a time, oldest confirmed first, so the printer gets jobs in
the order students confirmed them.
"""
import logging
import threading
import time
from datetime import datetime

from app.config import settings
from app.database import SessionLocal
from app.models import PrintJob
from app.printing import backends
from app.services import storage

logger = logging.getLogger(__name__)

POLL_SECONDS = 2
_stop = threading.Event()
_thread: threading.Thread | None = None


def _finish(db, job: PrintJob, status: str, error: str | None = None) -> None:
    job.status = status
    job.error_message = error
    if status == "completed":
        job.completed_at = datetime.utcnow()
    db.commit()


def _process_next(db) -> bool:
    job = (
        db.query(PrintJob)
        .filter(PrintJob.status == "queued")
        .order_by(PrintJob.confirmed_at.asc())
        .first()
    )
    if job is None:
        return False

    job.status = "printing"
    job.printer_backend = settings.printer_backend
    job.printer_name = settings.printer_name or ("simulated" if settings.printer_backend == "mock" else "default")
    db.commit()

    try:
        if settings.printer_backend == "mock":
            job.external_job_id = backends.mock_print(job)
            _finish(db, job, "completed")
        else:
            job.external_job_id = backends.cups_submit(storage.local_path(job.file_url), job)
            db.commit()
            deadline = time.time() + 15 * 60
            while not backends.cups_is_done(job.external_job_id):
                if time.time() > deadline or _stop.is_set():
                    raise backends.PrintError("Timed out waiting for the printer")
                time.sleep(3)
            _finish(db, job, "completed")
    except Exception as exc:
        logger.exception("Print job %s failed", job.id)
        _finish(db, job, "failed", str(exc))
    return True


def _loop() -> None:
    logger.info("Print dispatcher started (backend=%s)", settings.printer_backend)
    while not _stop.is_set():
        db = SessionLocal()
        try:
            worked = _process_next(db)
        except Exception:
            logger.exception("Print dispatcher error")
            worked = False
        finally:
            db.close()
        if not worked:
            _stop.wait(POLL_SECONDS)


def start() -> None:
    global _thread
    if settings.printer_backend not in ("mock", "cups") or (_thread and _thread.is_alive()):
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="print-dispatcher", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
