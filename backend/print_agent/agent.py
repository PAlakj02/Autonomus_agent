#!/usr/bin/env python3
"""Print agent — runs on a computer next to the school printer.

Use this when the FastAPI backend is hosted in the cloud and can't reach the
printer directly (PRINTER_BACKEND=agent on the server). The agent only makes
outbound HTTPS requests, so no port forwarding / VPN is needed:

    loop:
        POST /api/printer-agent/claim          -> next confirmed job (or 204)
        GET  /api/printer-agent/jobs/{id}/file -> the PDF
        print it locally (CUPS `lp` on macOS/Linux, SumatraPDF on Windows)
        POST /api/printer-agent/jobs/{id}/status {completed|failed}

Standard library only. Usage:

    export BACKEND_URL=https://your-backend.example.com
    export PRINT_AGENT_TOKEN=<same value as the server's PRINT_AGENT_TOKEN>
    export PRINTER_NAME=HP_LaserJet_Library   # optional; `lpstat -p` lists names
    python3 agent.py
"""
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")
TOKEN = os.environ.get("PRINT_AGENT_TOKEN", "")
PRINTER_NAME = os.environ.get("PRINTER_NAME", "")
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "3"))
DRY_RUN = os.environ.get("DRY_RUN") == "1"  # save PDFs instead of printing


def api(method: str, path: str, body: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(
        f"{BACKEND_URL}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Agent-Token": TOKEN, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def report(job_id: str, status: str, error: str | None = None, external_id: str | None = None) -> None:
    api("POST", f"/api/printer-agent/jobs/{job_id}/status",
        {"status": status, "error_message": error, "external_job_id": external_id})


def print_pdf(path: str, job: dict) -> str:
    if DRY_RUN:
        out = os.path.join(os.getcwd(), f"printed-{job['id']}.pdf")
        shutil.copy(path, out)
        return f"dry-run:{out}"

    if platform.system() == "Windows":
        sumatra = shutil.which("SumatraPDF") or r"C:\Program Files\SumatraPDF\SumatraPDF.exe"
        settings = f"{job['copies']}x,{'duplexlong' if job['duplex'] else 'simplex'},{'color' if job['color'] else 'monochrome'},fit"
        target = ["-print-to", PRINTER_NAME] if PRINTER_NAME else ["-print-to-default"]
        subprocess.run([sumatra, *target, "-print-settings", settings, "-silent", path], check=True, timeout=300)
        return "sumatra"

    cmd = ["lp", "-n", str(job["copies"]), "-o", f"media={job['paper_size']}", "-o", "fit-to-page",
           "-o", "sides=two-sided-long-edge" if job["duplex"] else "sides=one-sided",
           "-o", "print-color-mode=color" if job["color"] else "print-color-mode=monochrome",
           "-t", job["title"][:60]]
    if PRINTER_NAME:
        cmd[1:1] = ["-d", PRINTER_NAME]
    proc = subprocess.run([*cmd, path], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "lp failed")
    out = proc.stdout.strip()
    cups_id = out.split("request id is ", 1)[-1].split(" ", 1)[0] if "request id is" in out else out

    # Wait until CUPS has finished sending it to the printer.
    deadline = time.time() + 15 * 60
    while cups_id in subprocess.run(["lpstat", "-o"], capture_output=True, text=True).stdout:
        if time.time() > deadline:
            raise RuntimeError("Timed out waiting for the printer")
        time.sleep(3)
    return cups_id


def main() -> None:
    if not TOKEN:
        sys.exit("Set PRINT_AGENT_TOKEN (must match the server's PRINT_AGENT_TOKEN)")
    print(f"Print agent polling {BACKEND_URL} -> printer '{PRINTER_NAME or 'system default'}'"
          f"{' (DRY RUN)' if DRY_RUN else ''}")
    query = f"?printer_name={urllib.request.quote(PRINTER_NAME or 'default')}"
    while True:
        try:
            status, body = api("POST", f"/api/printer-agent/claim{query}")
            if status == 204:
                time.sleep(POLL_SECONDS)
                continue
            if status != 200:
                print(f"claim failed ({status}): {body[:200]!r}")
                time.sleep(max(POLL_SECONDS, 10))
                continue

            job = json.loads(body)
            print(f"Printing {job['title']!r}: {job['pages']} pages x {job['copies']}")
            status, pdf = api("GET", job["download_path"])
            if status != 200:
                report(job["id"], "failed", f"download failed ({status})")
                continue
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(pdf)
                path = f.name
            try:
                external_id = print_pdf(path, job)
                report(job["id"], "completed", external_id=external_id)
                print(f"  done ({external_id})")
            except Exception as exc:
                report(job["id"], "failed", str(exc))
                print(f"  failed: {exc}")
            finally:
                os.unlink(path)
        except urllib.error.URLError as exc:
            print(f"backend unreachable: {exc.reason}; retrying")
            time.sleep(max(POLL_SECONDS, 10))
        except KeyboardInterrupt:
            print("stopped")
            return


if __name__ == "__main__":
    main()
