"""How a confirmed print job physically reaches a printer.

Students' own printers need nothing here: device jobs are printed by the
browser's print dialog on the student's device. This module is only for the
optional SHARED printer. PRINTER_BACKEND selects one of:

  none   Default. No shared printer is offered.

  mock   Simulates printing (copies the PDF to STORAGE_DIR/printed and
         marks it completed after a short delay). Use for development.

  cups   The backend machine itself prints, via the CUPS `lp` command. Works
         on macOS and Linux with any printer added to the OS (USB, Wi-Fi, IPP,
         AirPrint). Set PRINTER_NAME to a queue from `lpstat -p`, or leave it
         blank for the system default. Use when the server runs on a machine
         in the same building/network as the printer.

  agent  The backend is hosted in the cloud and cannot reach the school
         printer. A small print agent (print_agent/agent.py) runs on any
         computer on the school network, polls this API for confirmed jobs,
         downloads the PDF and prints it locally with CUPS, then reports back.
         No inbound ports or VPN needed — the agent only makes outbound HTTPS calls.
"""
import logging
import os
import shutil
import subprocess
import time

from app.config import settings
from app.models import PrintJob
from app.services import storage

logger = logging.getLogger(__name__)

# Updated by the agent router on every agent call, read for printer status.
agent_last_seen: float | None = None


class PrintError(Exception):
    pass


def _lp_options(job: PrintJob) -> list[str]:
    opts = [
        "-n", str(job.copies),
        "-o", f"media={job.paper_size}",
        "-o", "fit-to-page",
        "-o", "sides=two-sided-long-edge" if job.duplex else "sides=one-sided",
        "-o", "print-color-mode=color" if job.color else "print-color-mode=monochrome",
        "-t", f"{job.title[:60]} ({job.id[:8]})",
    ]
    if settings.printer_name:
        opts = ["-d", settings.printer_name] + opts
    return opts


def cups_submit(pdf_path: str, job: PrintJob) -> str:
    """Submits to CUPS and returns the CUPS job id (e.g. 'HP_LaserJet-42')."""
    if shutil.which("lp") is None:
        raise PrintError("`lp` command not found — CUPS is not installed on this machine")
    proc = subprocess.run(["lp", *_lp_options(job), pdf_path], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise PrintError(proc.stderr.strip() or "lp failed")
    # stdout: "request id is HP_LaserJet-42 (1 file(s))"
    out = proc.stdout.strip()
    return out.split("request id is ", 1)[-1].split(" ", 1)[0] if "request id is" in out else out


def cups_is_done(external_job_id: str) -> bool:
    proc = subprocess.run(["lpstat", "-o"], capture_output=True, text=True, timeout=15)
    return external_job_id not in proc.stdout


def cups_status() -> tuple[bool, str]:
    if shutil.which("lpstat") is None:
        return False, "CUPS not installed on the server"
    target = ["-p", settings.printer_name] if settings.printer_name else ["-d"]
    proc = subprocess.run(["lpstat", *target], capture_output=True, text=True, timeout=15)
    out = (proc.stdout or proc.stderr).strip()
    if proc.returncode != 0 or "no system default" in out or not out:
        return False, out or "No printer configured in CUPS"
    if "disabled" in out:
        return False, out
    return True, out


def mock_print(job: PrintJob) -> str:
    target_dir = os.path.join(settings.storage_dir, "printed")
    os.makedirs(target_dir, exist_ok=True)
    data = storage.read_bytes(job.file_url)
    path = os.path.join(target_dir, f"{job.id}.pdf")
    with open(path, "wb") as f:
        f.write(data)
    time.sleep(min(8, 1 + 0.4 * job.pages * job.copies))  # pretend the printer is busy
    return f"mock-{job.id[:8]}"


def status() -> tuple[bool, str]:
    backend = settings.printer_backend
    if backend == "none":
        return False, "No shared printer set up"
    if backend == "mock":
        return True, "Simulated printer — PDFs are saved to generated/printed/"
    if backend == "cups":
        return cups_status()
    if backend == "agent":
        if agent_last_seen and time.time() - agent_last_seen < 30:
            return True, "Print agent connected"
        return False, "Print agent offline — start print_agent/agent.py on the school PC"
    return False, f"Unknown PRINTER_BACKEND '{backend}'"
