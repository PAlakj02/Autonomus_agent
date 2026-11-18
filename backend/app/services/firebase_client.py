"""Firebase integration: auth token verification + storage upload.

Auth: set FIREBASE_PROJECT_ID to verify student ID tokens. Verification only
uses Google's public signing keys, so no service account is required.

Storage (optional): set FIREBASE_ENABLED=true plus FIREBASE_CREDENTIALS_PATH /
FIREBASE_STORAGE_BUCKET to store generated files in Firebase Storage instead
of local disk.
"""
import logging

from app.config import settings

logger = logging.getLogger(__name__)

_app = None
_bucket = None


def _init():
    global _app, _bucket
    if _app is not None:
        return

    import firebase_admin
    from firebase_admin import credentials

    options = {}
    if settings.firebase_project_id:
        options["projectId"] = settings.firebase_project_id

    if settings.firebase_enabled:
        from firebase_admin import storage

        cred = credentials.Certificate(settings.firebase_credentials_path)
        options["storageBucket"] = settings.firebase_storage_bucket
        _app = firebase_admin.initialize_app(cred, options)
        _bucket = storage.bucket()
        return

    from google.auth.credentials import AnonymousCredentials

    class _Anonymous(credentials.Base):
        def get_credential(self):
            return AnonymousCredentials()

    _app = firebase_admin.initialize_app(_Anonymous(), options)


def verify_id_token(id_token: str) -> dict:
    """Verifies a Firebase ID token and returns its claims (incl. 'uid')."""
    from firebase_admin import auth

    _init()
    return auth.verify_id_token(id_token)


def upload_bytes(data: bytes, filename: str, subdir: str = "") -> str:
    if not settings.firebase_enabled:
        raise RuntimeError("Firebase is not enabled; use app.services.storage instead")

    _init()
    blob_path = f"{subdir}/{filename}" if subdir else filename
    blob = _bucket.blob(blob_path)
    blob.upload_from_string(data)
    blob.make_public()
    return blob.public_url
