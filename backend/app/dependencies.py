from fastapi import Header, HTTPException

from app.config import settings
from app.services import firebase_client


def get_current_student(
    authorization: str | None = Header(default=None),
    x_student_id: str | None = Header(default=None),
) -> str:
    """Resolves the authenticated student id.

    With FIREBASE_PROJECT_ID set: requires `Authorization: Bearer <idToken>`
    from Firebase Auth and returns the verified uid. X-Student-Id is ignored,
    so students can't impersonate each other or reset their print quota.

    Without it (local dev): trusts the `X-Student-Id` header, or falls back to
    a fixed "demo-student" id.
    """
    if not settings.auth_required:
        return x_student_id or "demo-student"

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Please sign in")
    try:
        decoded = firebase_client.verify_id_token(authorization.split(" ", 1)[1])
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Your session has expired, please sign in again ({exc})")
    return decoded["uid"]
