"""File storage for generated assets.

PLACEHOLDER: when FIREBASE_ENABLED=true, files are uploaded to Firebase
Storage instead (see app/services/firebase_client.py). By default this saves
to local disk under STORAGE_DIR and serves it back via the /files static
mount registered in app/main.py.
"""
import os
import uuid

from app.config import settings
from app.services import firebase_client

os.makedirs(settings.storage_dir, exist_ok=True)


def save_bytes(data: bytes, extension: str, subdir: str = "") -> str:
    """Saves bytes to storage and returns a URL path the frontend can load."""
    filename = f"{uuid.uuid4().hex}.{extension.lstrip('.')}"

    if settings.firebase_enabled:
        return firebase_client.upload_bytes(data, filename, subdir=subdir)

    target_dir = os.path.join(settings.storage_dir, subdir) if subdir else settings.storage_dir
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, filename)
    with open(path, "wb") as f:
        f.write(data)

    rel_path = f"{subdir}/{filename}" if subdir else filename
    return f"/files/{rel_path}"


def read_bytes(url: str) -> bytes:
    """Loads a stored asset back from a URL previously returned by save_bytes."""
    if url.startswith("/files/"):
        with open(local_path(url), "rb") as f:
            return f.read()

    import httpx

    resp = httpx.get(url, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def local_path(url: str) -> str:
    """Maps a /files/... URL to its path on disk. Rejects path traversal."""
    if not url.startswith("/files/"):
        raise ValueError(f"Not a local storage URL: {url}")
    root = os.path.realpath(settings.storage_dir)
    path = os.path.realpath(os.path.join(root, url[len("/files/"):]))
    if not path.startswith(root + os.sep):
        raise ValueError("Path escapes storage dir")
    return path
