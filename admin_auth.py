"""Shared host-only admin secret for the web and directory services."""

import os
import secrets
import time
from pathlib import Path

SECRET_FILE = Path(os.environ.get("SOCKETROOM_ADMIN_SECRET_FILE") or
                   Path(__file__).resolve().parent / "admin_secret.txt")


def load_or_create_admin_secret() -> str:
    """Create a strong persistent secret with exclusive file creation."""
    if not SECRET_FILE.exists():
        secret = secrets.token_urlsafe(48)
        try:
            fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(secret + "\n")
            try:
                os.chmod(SECRET_FILE, 0o600)
            except OSError:
                pass
    for _ in range(20):
        try:
            secret = SECRET_FILE.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            secret = ""
        if len(secret) >= 48:
            return secret
        time.sleep(0.05)
    raise RuntimeError(f"Admin secret file is missing a strong secret: {SECRET_FILE}")
