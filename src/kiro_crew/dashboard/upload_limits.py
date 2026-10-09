"""The per-file ceiling for dashboard uploads: ``dashboard.upload_max_mb``.

One reader for the chat composer's ``POST /api/upload/file`` and the Knowledge
page's ``POST /api/knowledge/ingest``, so both enforce, and the dashboard
advertises, the same figure. Video keeps its own, larger ceiling in the upload
handler.

Deliberately separate from ``handlers.files._MAX_UPLOAD_BYTES``: that constant
also bounds the in-memory READ paths (file-raw, file-download, office and sheet
preview), which raising the upload ceiling must not widen.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: The ``dashboard.upload_max_mb`` field default, in bytes. Answered when the
#: config cannot be read, so a broken config never refuses every upload.
DEFAULT_UPLOAD_MAX_BYTES = 100 * 1024 * 1024


def upload_max_bytes() -> int:
    """``dashboard.upload_max_mb`` in bytes (loader-bounded to 1-512 MB).

    Read through the mtime-cached config load, so an edit takes effect on the
    next upload with no restart. Blocking -- callers run it off the event loop.
    """
    from kiro_crew.config.loader import KiroCrewConfig

    try:
        return int(KiroCrewConfig.load().dashboard.upload_max_mb) * 1024 * 1024
    except Exception:
        logger.debug("upload_max_mb lookup failed; using the default ceiling", exc_info=True)
        return DEFAULT_UPLOAD_MAX_BYTES
