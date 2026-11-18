"""Mock paper flow — Gemini text model stage + verification.

    materials -> blueprint -> questions + answers -> verify (marks check) -> render

Blueprint: sections, question types, marks per question, topic coverage.
The blueprint is authoritative: verification forces every question's marks to
match its section, checks counts/answers/options, and asks Gemini for one
repair pass if anything is missing. A paper is only rendered once its marks add
up to exactly the requested total.

Without GEMINI_API_KEY a deterministic stub builds the same structures from
the uploaded material (fill-in-the-blank MCQs, short/long answers from key
sentences), so the full flow can be tested end to end.
"""
import json
import logging
import random
import re
from dataclasses import dataclass, field

from app.services import gemini_text

logger = logging.getLogger(__name__)

QUESTION_TYPES = ["mcq", "very_short", "short", "long", "case_study"]
MAX_MATERIAL_CHARS = 200_000


@dataclass
class PaperSpec:
    subject: str
    total_marks: int
    duration_minutes: int
    difficulty: str = "mixed"
    grade: str | None = None
    board: str | None = None
    instructions: str | None = None
    materials: list[dict] = field(default_factory=list)  # {"filename", "kind", "text"}


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

BLUEPRINT_SYSTEM = f"""You are an experienced school examiner designing the blueprint
for a mock exam paper. Base topic coverage on the provided study material and the
style of any past papers. Return ONLY JSON of this shape:
{{
  "topics": [string, ...],                 // topics the paper covers, from the material
  "sections": [
    {{
      "id": "A",                           // A, B, C ...
      "title": string,                     // e.g. "Section A - Multiple Choice"
      "question_type": one of {QUESTION_TYPES},
      "num_questions": integer >= 1,
      "marks_each": integer >= 1,
      "instructions": string               // one line for students
    }}
  ],
  "general_instructions": [string, ...]
}}
The sum of num_questions * marks_each over all sections MUST equal the requested
total marks exactly. Match the difficulty requested. Do not write questions yet."""

QUESTIONS_SYSTEM = """You are an experienced school examiner writing a mock exam paper
from an agreed blueprint. Use ONLY facts supported by the study material (or standard
curriculum knowledge for the subject and grade when material is thin). Mirror the
style of any past papers provided but never copy a question verbatim.

Return ONLY JSON: {"questions": [ ... ]} where each question is:
{
  "number": integer,                 // 1..N, continuous across sections
  "section": "A",                    // blueprint section id
  "type": question_type of the section,
  "marks": integer,                  // = marks_each of the section
  "topic": string,
  "difficulty": "easy" | "medium" | "hard",
  "question": string,
  "options": [4 strings] only for mcq, else omit,
  "answer": string,                  // for mcq: the letter A-D; else a model answer
  "marking_scheme": string           // how marks are awarded, point by point
}
Write exactly the number of questions each section asks for, in section order."""

REPAIR_SYSTEM = """You fix a mock exam paper so it passes verification. You get the
blueprint, the current questions, and a list of problems. Return ONLY JSON
{"questions": [...]} with the full corrected list, same format as before, fixing every
problem. Keep good questions unchanged."""

AUDIT_SYSTEM = """You are a second examiner checking an answer key. For each question,
solve it independently and compare with the given answer. Return ONLY JSON:
{"findings": [{"number": int, "ok": bool, "issue": string}]}
Only include questions where ok is false (wrong answer, ambiguous question, more than
one correct option, or answer not supported by the material). Empty list if all good."""


def _materials_block(spec: PaperSpec) -> str:
    if not spec.materials:
        return "(No material uploaded — use standard curriculum content for this subject and grade.)"
    parts, used = [], 0
    for m in spec.materials:
        text = (m.get("text") or "").strip()
        if not text:
            continue
        room = MAX_MATERIAL_CHARS - used
        if room <= 0:
            break
        chunk = text[:room]
        used += len(chunk)
        parts.append(f"--- {m.get('kind', 'material').upper()}: {m.get('filename')} ---\n{chunk}")
    return "\n\n".join(parts) or "(Uploaded material had no readable text.)"


def _spec_block(spec: PaperSpec) -> str:
    return (
        f"Subject: {spec.subject}\n"
        f"Grade/Class: {spec.grade or 'not specified'}\n"
        f"Board/Curriculum: {spec.board or 'not specified'}\n"
        f"Total marks: {spec.total_marks}\n"
        f"Duration: {spec.duration_minutes} minutes\n"
        f"Difficulty: {spec.difficulty}\n"
        f"Extra instructions from the student: {spec.instructions or 'none'}"
    )


# --------------------------------------------------------------------------
# Blueprint
# --------------------------------------------------------------------------


def _blueprint_total(blueprint: dict) -> int:
    return sum(s["num_questions"] * s["marks_each"] for s in blueprint["sections"])


def normalize_blueprint(blueprint: dict, total_marks: int) -> tuple[dict, list[str]]:
    """Cleans section fields and rebalances question counts so the blueprint
    totals exactly `total_marks`. Returns (blueprint, list of fixes applied)."""
    fixes: list[str] = []
    sections = []
    for i, s in enumerate(blueprint.get("sections") or []):
        qtype = s.get("question_type") if s.get("question_type") in QUESTION_TYPES else "short"
        sections.append(
            {
                "id": chr(ord("A") + i),
                "title": str(s.get("title") or f"Section {chr(ord('A') + i)}"),
                "question_type": qtype,
                "num_questions": max(1, int(s.get("num_questions") or 1)),
                "marks_each": max(1, int(s.get("marks_each") or 1)),
                "instructions": str(s.get("instructions") or ""),
            }
        )
    if not sections:
        raise ValueError("Blueprint has no sections")
    blueprint["sections"] = sections

    diff = total_marks - _blueprint_total(blueprint)
    if diff:
        fixes.append(f"Blueprint totalled {total_marks - diff} marks; rebalanced to {total_marks}.")
    guard = 0
    while diff and guard < 50:
        guard += 1
        # 1) a section whose marks_each divides the gap exactly
        for s in sorted(sections, key=lambda s: s["marks_each"]):
            step = diff // s["marks_each"]
            if diff % s["marks_each"] == 0 and s["num_questions"] + step >= 1:
                s["num_questions"] += step
                diff = 0
                break
        else:
            if diff > 0:
                # 2) add 1-mark very short answers to cover the remainder
                vs = next((s for s in sections if s["marks_each"] == 1), None)
                if vs is None:
                    vs = {
                        "id": chr(ord("A") + len(sections)),
                        "title": "Very Short Answer",
                        "question_type": "very_short",
                        "num_questions": 0,
                        "marks_each": 1,
                        "instructions": "Answer in one word or one sentence.",
                    }
                    sections.append(vs)
                vs["num_questions"] += diff
                diff = 0
            else:
                # 3) too many marks: drop a question from the biggest section that can spare one
                spare = [s for s in sections if s["num_questions"] > 1]
                if not spare:
                    raise ValueError("Cannot rebalance blueprint to requested total")
                s = max(spare, key=lambda s: s["marks_each"])
                s["num_questions"] -= 1
                diff += s["marks_each"]

    blueprint.setdefault("topics", [])
    blueprint.setdefault("general_instructions", [])
    blueprint["total_marks"] = _blueprint_total(blueprint)
    return blueprint, fixes


def _stub_blueprint(spec: PaperSpec, topics: list[str]) -> dict:
    t = spec.total_marks
    n_mcq = round(t * 0.2)
    n_long = max(1, round(t * 0.4 / 5))
    rest = t - n_mcq - 5 * n_long
    n_short, odd = divmod(rest, 2)
    n_mcq += odd
    sections = [
        {"title": "Section A - Multiple Choice", "question_type": "mcq", "num_questions": n_mcq,
         "marks_each": 1, "instructions": "Choose the correct option."},
        {"title": "Section B - Short Answer", "question_type": "short", "num_questions": n_short,
         "marks_each": 2, "instructions": "Answer in 2-3 sentences."},
        {"title": "Section C - Long Answer", "question_type": "long", "num_questions": n_long,
         "marks_each": 5, "instructions": "Answer in detail with examples."},
    ]
    return {
        "topics": topics,
        "sections": [s for s in sections if s["num_questions"] > 0],
        "general_instructions": [
            "All questions are compulsory.",
            "Marks for each question are shown in brackets.",
            "Write neatly and show working where required.",
        ],
    }


# --------------------------------------------------------------------------
# Stub question writer (no API key)
# --------------------------------------------------------------------------

_STOP = set(
    "the a an and or of to in on for with is are was were be been by as at from that this "
    "these those it its into which who whom what when where why how not can will would should "
    "may might also than then there their they them he she his her you your we our".split()
)


def _sentences(spec: PaperSpec) -> list[str]:
    text = " ".join((m.get("text") or "") for m in spec.materials)
    text = re.sub(r"\s+", " ", text)
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text)]
    return [s for s in sents if 40 <= len(s) <= 260 and len(s.split()) >= 7]


def _keywords(sentence: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z\-]{4,}", sentence)
    return [w for w in words if w.lower() not in _STOP]


def _topics_from(sentences: list[str], subject: str) -> list[str]:
    counts: dict[str, int] = {}
    for s in sentences:
        for w in _keywords(s):
            counts[w.lower()] = counts.get(w.lower(), 0) + 1
    top = sorted(counts, key=lambda w: -counts[w])[:6]
    return [w.title() for w in top] or [subject.title()]


def _stub_questions(spec: PaperSpec, blueprint: dict) -> list[dict]:
    rng = random.Random(f"{spec.subject}-{spec.total_marks}")
    sents = _sentences(spec)
    rng.shuffle(sents)
    vocab = sorted({w for s in sents for w in _keywords(s)})
    topics = blueprint.get("topics") or [spec.subject.title()]
    pool = iter(sents)
    questions, number = [], 1

    for sec in blueprint["sections"]:
        for _ in range(sec["num_questions"]):
            sent = next(pool, None)
            topic = topics[(number - 1) % len(topics)]
            q = {"number": number, "section": sec["id"], "type": sec["question_type"],
                 "marks": sec["marks_each"], "topic": topic,
                 "difficulty": ["easy", "medium", "hard"][min(2, sec["marks_each"] // 2)]}

            if sec["question_type"] == "mcq":
                kws = _keywords(sent) if sent else []
                target = max(kws, key=len) if kws else ""
                others = [w for w in vocab if w.lower() != target.lower()]
                if target and len(others) >= 3:
                    distractors = rng.sample(others, 3)
                    opts = distractors + [target]
                    rng.shuffle(opts)
                    blanked = re.sub(rf"\b{re.escape(target)}\b", "________", sent, count=1)
                    q.update(question=f"Fill in the blank: {blanked}", options=opts,
                             answer="ABCD"[opts.index(target)],
                             marking_scheme=f"1 mark for '{target}'.")
                else:
                    q.update(question=f"Which of the following best relates to {topic}? (placeholder Q{number})",
                             options=[f"{topic} - statement {c}" for c in "1234"], answer="A",
                             marking_scheme="1 mark for the correct option.")
            elif sent:
                verb = "Explain" if sec["marks_each"] <= 3 else "Discuss in detail, with examples,"
                kw = max(_keywords(sent) or [topic], key=len)
                q.update(question=f"{verb} the following in the context of {kw}: \"{sent}\"",
                         answer=sent,
                         marking_scheme=f"Award up to {sec['marks_each']} marks for accurate, "
                                        "well-explained points matching the model answer.")
            else:
                q.update(question=f"Write about {topic} (question {number}). [placeholder — "
                                  "upload material or set GEMINI_API_KEY]",
                         answer=f"Model answer on {topic}.",
                         marking_scheme=f"Up to {sec['marks_each']} marks for relevant points.")
            questions.append(q)
            number += 1
    return questions


# --------------------------------------------------------------------------
# Verification (marks check)
# --------------------------------------------------------------------------


def verify(questions: list[dict], blueprint: dict, total_marks: int) -> dict:
    """Checks the paper against the blueprint, auto-fixing what can be fixed
    deterministically (numbering, per-question marks, trailing extras).
    Returns a report; `questions` is mutated in place to the fixed list."""
    fixes: list[str] = []
    issues: list[str] = []
    sections = {s["id"]: s for s in blueprint["sections"]}

    by_section: dict[str, list[dict]] = {sid: [] for sid in sections}
    for q in questions:
        sid = str(q.get("section") or "").strip().upper()
        if sid not in by_section:
            issues.append(f"Question '{str(q.get('question'))[:40]}' has unknown section '{sid}'.")
            continue
        by_section[sid].append(q)

    fixed: list[dict] = []
    seen_text: set[str] = set()
    for sid, sec in sections.items():
        qs = by_section[sid]
        if len(qs) > sec["num_questions"]:
            fixes.append(f"Section {sid}: trimmed {len(qs) - sec['num_questions']} extra question(s).")
            qs = qs[: sec["num_questions"]]
        elif len(qs) < sec["num_questions"]:
            issues.append(f"Section {sid}: has {len(qs)} questions, blueprint needs {sec['num_questions']}.")

        for q in qs:
            q["section"] = sid
            q["type"] = sec["question_type"]
            if q.get("marks") != sec["marks_each"]:
                fixes.append(f"Section {sid}: set marks to {sec['marks_each']} on a question marked {q.get('marks')}.")
                q["marks"] = sec["marks_each"]
            text = str(q.get("question") or "").strip()
            if not text:
                issues.append(f"Section {sid}: a question has no text.")
            norm = re.sub(r"\W+", " ", text.lower()).strip()
            if norm and norm in seen_text:
                issues.append(f"Section {sid}: duplicate question '{text[:50]}'.")
            seen_text.add(norm)
            if not str(q.get("answer") or "").strip():
                issues.append(f"Section {sid}: question '{text[:40]}' has no answer.")
            if sec["question_type"] == "mcq":
                opts = q.get("options") or []
                if len(opts) != 4:
                    issues.append(f"Section {sid}: MCQ '{text[:40]}' has {len(opts)} options, needs 4.")
                ans = str(q.get("answer") or "").strip().upper()[:1]
                if ans not in "ABCD" or not ans:
                    issues.append(f"Section {sid}: MCQ '{text[:40]}' answer must be A-D.")
                else:
                    q["answer"] = ans
            else:
                q.pop("options", None)
            fixed.append(q)

    for i, q in enumerate(fixed, start=1):
        if q.get("number") != i:
            q["number"] = i
    questions[:] = fixed

    actual_total = sum(q["marks"] for q in questions)
    if actual_total != total_marks:
        issues.append(f"Marks add up to {actual_total}, expected {total_marks}.")

    return {
        "passed": not issues,
        "total_marks_expected": total_marks,
        "total_marks_actual": actual_total,
        "question_count": len(questions),
        "section_totals": {
            sid: sum(q["marks"] for q in questions if q["section"] == sid) for sid in sections
        },
        "auto_fixes": fixes,
        "issues": issues,
    }


# --------------------------------------------------------------------------
# Public entry points (called by the pipeline, one per stage)
# --------------------------------------------------------------------------


def build_blueprint(spec: PaperSpec) -> tuple[dict, str, list[str]]:
    """Returns (blueprint, source, fixes)."""
    try:
        raw = gemini_text.generate_json(
            BLUEPRINT_SYSTEM,
            f"{_spec_block(spec)}\n\nSTUDY MATERIAL AND PAST PAPERS:\n{_materials_block(spec)}",
        )
        source = "gemini"
    except gemini_text.GeminiNotConfigured:
        raw = _stub_blueprint(spec, _topics_from(_sentences(spec), spec.subject))
        source = "stub"
    blueprint, fixes = normalize_blueprint(raw, spec.total_marks)
    return blueprint, source, fixes


def write_questions(spec: PaperSpec, blueprint: dict, source: str) -> list[dict]:
    if source == "stub":
        return _stub_questions(spec, blueprint)
    raw = gemini_text.generate_json(
        QUESTIONS_SYSTEM,
        f"{_spec_block(spec)}\n\nBLUEPRINT:\n{json.dumps(blueprint, indent=2)}\n\n"
        f"STUDY MATERIAL AND PAST PAPERS:\n{_materials_block(spec)}",
        temperature=0.7,
    )
    return list(raw.get("questions") or [])


def verify_and_repair(spec: PaperSpec, blueprint: dict, questions: list[dict], source: str) -> dict:
    """Marks check + structural checks, with one Gemini repair pass and an
    independent answer-key audit when Gemini is available."""
    report = verify(questions, blueprint, spec.total_marks)

    if not report["passed"] and source == "gemini":
        logger.info("Paper failed verification, attempting repair: %s", report["issues"])
        raw = gemini_text.generate_json(
            REPAIR_SYSTEM,
            f"BLUEPRINT:\n{json.dumps(blueprint, indent=2)}\n\n"
            f"QUESTIONS:\n{json.dumps(questions, indent=2)}\n\n"
            f"PROBLEMS:\n" + "\n".join(f"- {i}" for i in report["issues"]),
        )
        questions[:] = list(raw.get("questions") or [])
        first_issues = report["issues"]
        report = verify(questions, blueprint, spec.total_marks)
        report["repaired"] = first_issues

    report["answer_audit"] = []
    if report["passed"] and source == "gemini":
        try:
            audit = gemini_text.generate_json(
                AUDIT_SYSTEM,
                f"{_spec_block(spec)}\n\nQUESTIONS WITH ANSWERS:\n{json.dumps(questions, indent=2)}",
                temperature=0.0,
            )
            report["answer_audit"] = [f for f in audit.get("findings", []) if not f.get("ok", False)]
        except Exception:
            logger.exception("Answer audit failed; continuing without it")
    return report
