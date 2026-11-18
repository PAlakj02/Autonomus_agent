# Design Fulfillment Agent — Backend

FastAPI implementation of the pipeline:

```
Student Request → Understand → Generate → Safety Check + Preview → Confirmation
```

## Setup

Requires Python 3.11 (the pinned `pydantic`/`groq`/`google-generativeai` wheels
don't yet support 3.14; on this machine `python3` resolves to 3.14, so a
Homebrew 3.11 is used instead).

```bash
cd backend
/opt/homebrew/bin/python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Visit `http://localhost:8000/docs` for interactive API docs, and
`http://localhost:8000/health` to see which integrations are configured.

## Placeholders — what's real vs. stubbed

Every external integration is wired through `app/config.py` env vars and has
a working fallback, so the whole pipeline runs with zero API keys:

| Stage | Real integration (set the env var to enable) | Fallback when unset |
|---|---|---|
| Understand | `GROQ_API_KEY` → Groq/Llama JSON parsing (`app/services/parser.py`) | Regex/keyword-based stub parser |
| Generate (poster/logo) | `GEMINI_API_KEY` → Gemini Flash Image (`app/services/image_gen.py`) | Pillow-rendered placeholder image |
| Generate (flyer/worksheet/notes) | `GROQ_API_KEY` → Groq text generation, then rendered to PDF via reportlab (`app/services/content_gen.py`) | Deterministic placeholder text, same PDF renderer |
| Moderation | `MODERATION_API_KEY` → wire up a real provider in `_call_real_api` (`app/services/moderation.py`) | Keyword-based safety check |
| Auth + storage | `FIREBASE_ENABLED=true` + `FIREBASE_CREDENTIALS_PATH` (`app/services/firebase_client.py`) | Dev-mode header auth (`X-Student-Id`) + local disk storage served at `/files` |

When you're ready to go live: drop real values into `.env` (copy from
`.env.example`), and for moderation implement `_call_real_api` in
`app/services/moderation.py` with your provider of choice.

## API

All endpoints are scoped to the calling student (`X-Student-Id` header in dev
mode, or `Authorization: Bearer <firebase-id-token>` once Firebase is
enabled).

- `POST /api/requests` — Stage 1 (Understand). Body: `{"raw_request": "..."}`.
  Returns a job. If a required field is missing, `status` is
  `pending_clarification` and `clarification_question` is set.
- `POST /api/requests/{id}/clarify` — Body: `{"answer": "..."}`. Re-parses
  with the answer merged in; may still ask again if still ambiguous.
- `POST /api/requests/{id}/generate` — Stages 2 + 3 (Generate, then
  moderate + preview). Routes to the image or text/PDF workflow based on
  `workflow_type` (`poster_artwork`, `logo_icon`, `flyer_worksheet`,
  `notes_essay`). Sets `status` to `preview_ready` with a `preview_url`, or
  `rejected` with a `rejection_reason` if either the request or the output
  fails the safety check.
- `POST /api/requests/{id}/reject` — student wants to try again; sends the
  job back to `ready_to_generate`.
- `POST /api/requests/{id}/confirm` — Stage 4 (Confirmation). Only valid from
  `preview_ready`. Sets `status` to `confirmed` and copies `preview_url` to
  `final_url`.
- `GET /api/requests/{id}` — poll job status (structured info, moderation
  results, preview/final URL).
- `GET /api/requests` — list the current student's jobs, newest first.

## Job status lifecycle

```
parsing → pending_clarification ⇄ (clarify) ⇄ ready_to_generate
                                                    │
                                              (generate)
                                                    ▼
                          moderating_request → generating → moderating_output
                                 │                                  │
                              rejected                          rejected
                                                                     │
                                                             preview_ready
                                                              │        │
                                                          (reject)  (confirm)
                                                              │        │
                                                    ready_to_generate  confirmed
```

`failed` can occur instead of `generating`/`moderating_output` if a
generation call raises (see `error_message`).

## Notes for the frontend

- Generated assets are served as static files at `/files/...` — the
  `preview_url`/`final_url` fields returned by the API are already relative
  paths ready to prefix with the backend origin.
- CORS is open to `FRONTEND_ORIGIN` (default `http://localhost:3000`).
- Swap the local-disk storage for real Firebase Storage at any time by
  setting `FIREBASE_ENABLED=true` — no API response shapes change, only
  where the URL points.

## Mock paper flow (new)

```
Upload material -> Gemini text model (blueprint -> questions + answers) -> Verify (marks check) -> Render (HTML -> PDF)
```

- `POST /api/materials` (multipart: `file`, `kind`=worksheet|past_paper|notes) — PDF/DOCX/TXT text is
  extracted locally; photos and scanned PDFs are read by Gemini when `GEMINI_API_KEY` is set.
- `POST /api/papers` — `{subject, total_marks, duration_minutes, difficulty, material_ids, ...}`. Returns
  202 immediately; generation runs in the background. Poll `GET /api/papers/{id}`:
  `queued -> blueprinting -> writing -> verifying -> rendering -> ready` (or `failed`).
- `POST /api/papers/{id}/regenerate`, `GET /api/papers`, `GET/DELETE /api/materials`.

Verification (`app/services/paper_gen.py`): the blueprint is rebalanced so it totals exactly the requested
marks; every question's marks are forced to its section's value; section counts, MCQ options/answers,
missing answers and duplicates are checked. If anything is still wrong Gemini gets one repair pass, and
then a second "examiner" pass audits the answer key (any warnings are shown in the UI). A paper whose
marks don't add up is never rendered. Output is two PDFs: the question paper and the answer key.

Without `GEMINI_API_KEY` a deterministic generator builds questions from the uploaded text
(fill-in-the-blank MCQs, short/long answers) so the whole flow is testable offline.

`GEMINI_TEXT_MODEL` (default `gemini-2.5-flash`) picks the model.

## Print queue + printer

```
design (confirmed) ─┐
paper / answer key ─┴─> POST /api/print-jobs (awaiting_confirmation, real page count)
                        -> POST /api/print-jobs/{id}/confirm (quota check) -> queued -> printing -> completed
```

- Confirming a design now also produces a **print-ready PDF** (`print_url`): the image is fitted onto the
  exact paper size at 300 dpi.
- Limits (env): `PRINT_DAILY_PAGE_LIMIT` (pages x copies per student per UTC day), `PRINT_MAX_PAGES_PER_JOB`,
  `PRINT_MAX_COPIES`. `GET /api/print-quota`, `GET /api/printer`, `GET /api/print-jobs`,
  `POST /api/print-jobs/{id}/cancel`.

### How printing reaches a printer

**Students' own printers (default, no setup):** a print job's `destination` is `device`. On confirm it
becomes `sent_to_device` and the frontend (`web-ai-link/src/lib/print.ts`) opens the device's own print
dialog with the PDF. That dialog lists every printer the laptop/tablet/phone already uses — Wi-Fi, USB,
Bluetooth, AirPrint, Mopria — so it works with any printer without configuration. (A website cannot
talk to printers directly; the OS print dialog is the universal bridge.)

**Optional shared school printer:** set `PRINTER_BACKEND` (`app/printing/backends.py`) and students get a
"School printer" choice (`destination: shared`, `queued -> printing -> completed`):

| Value | When to use | How it works |
|---|---|---|
| `none` (default) | no shared printer | only "My printer" is offered |
| `mock` | testing the shared flow | PDFs are copied to `generated/printed/` and marked completed |
| `cups` | backend runs on a machine that has the printer installed | an in-process worker sends each job with `lp` (copies, A4/A3, duplex, colour) and waits for CUPS. Set `PRINTER_NAME` from `lpstat -p` |
| `agent` | backend is in the cloud | run `print_agent/agent.py` on a PC next to the printer. It polls `/api/printer-agent/claim` with `X-Agent-Token: $PRINT_AGENT_TOKEN`, downloads the PDF, prints locally, reports back. Outbound HTTPS only |

```bash
# on the school PC (stdlib only, Python 3.10+)
BACKEND_URL=https://api.yourschool.example PRINT_AGENT_TOKEN=... PRINTER_NAME=Library_HP python3 agent.py
# DRY_RUN=1 saves PDFs instead of printing — useful for a first test
```

## Student login (Firebase Auth)

Set `FIREBASE_PROJECT_ID` and every `/api` call requires `Authorization: Bearer <Firebase ID token>`;
the verified uid scopes jobs and the print quota. Only the project id is needed (tokens are checked
against Google's public keys) — a service account is only required for Firebase Storage
(`FIREBASE_ENABLED=true`). Leave `FIREBASE_PROJECT_ID` blank for local dev with the `X-Student-Id` header.
The frontend signs students in with Google or email/password (`web-ai-link/src/lib/auth.tsx`).
