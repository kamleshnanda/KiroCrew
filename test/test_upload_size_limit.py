"""``dashboard.upload_max_mb``: one configurable per-file upload ceiling.

The chat composer's ``POST /api/upload/file`` and the Knowledge page's
``POST /api/knowledge/ingest`` both read the ceiling per request, refuse an
over-cap file with a 413 that names the limit, and ``GET /api/dashboard/config``
serves it read-only so both pages pre-check and advertise the same figure.
"""

from __future__ import annotations

import contextlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from dashboard_owner_helpers import as_owner

from kiro_crew.config.loader import KiroCrewConfig
from kiro_crew.config.sections import UPLOAD_MAX_MB_MAX, UPLOAD_MAX_MB_MIN, DashboardConfig
from kiro_crew.dashboard import upload_limits

MB = 1024 * 1024


def _zip_bytes(pad: int = 0) -> bytes:
    """A valid ZIP, optionally padded with a stored member of *pad* bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("readme.txt", "hello")
        if pad:
            zf.writestr("pad.bin", b"\x00" * pad)
    return buf.getvalue()


@pytest.fixture()
def cfg_file(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    with patch("kiro_crew.config.loader.config_path", return_value=p):
        yield p


def _set_limit(cfg_file: Path, value: object) -> None:
    cfg_file.write_text(json.dumps({"dashboard": {"upload_max_mb": value}}), encoding="utf-8")


# ---------- config ----------


def test_default_is_100_mb() -> None:
    assert DashboardConfig().upload_max_mb == 100
    assert upload_limits.DEFAULT_UPLOAD_MAX_BYTES == 100 * MB


@pytest.mark.parametrize(
    ("stored", "loaded"),
    [
        (250, 250),
        ("300", 300),
        (0, UPLOAD_MAX_MB_MIN),
        (-5, UPLOAD_MAX_MB_MIN),
        (10**9, UPLOAD_MAX_MB_MAX),
        ("lots", 100),
        (True, 100),
    ],
)
def test_loader_bounds_the_stored_value(cfg_file, stored, loaded) -> None:
    _set_limit(cfg_file, stored)
    assert KiroCrewConfig.load().dashboard.upload_max_mb == loaded


def test_upload_max_bytes_reads_the_config(cfg_file) -> None:
    _set_limit(cfg_file, 7)
    assert upload_limits.upload_max_bytes() == 7 * MB


def test_upload_max_bytes_falls_back_when_config_is_unreadable() -> None:
    with patch.object(KiroCrewConfig, "load", side_effect=OSError("boom")):
        assert upload_limits.upload_max_bytes() == upload_limits.DEFAULT_UPLOAD_MAX_BYTES


# ---------- chat composer upload ----------


@pytest.fixture
def upload_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "uploads"
    monkeypatch.setattr("kiro_crew.dashboard.handlers.files._UPLOAD_DIR", target)
    return target


@pytest.fixture
def files_sel():
    with patch("kiro_crew.dashboard.handlers.files._sel") as sel:
        sel.return_value = MagicMock()
        yield sel.return_value


async def _post_upload(payload: bytes, filename: str) -> tuple[int, dict]:
    from kiro_crew.dashboard.handlers.files import api_upload_file

    app = web.Application()
    app["state"] = MagicMock()
    app.router.add_post("/api/upload/file", api_upload_file)
    form = aiohttp.FormData()
    form.add_field("file", payload, filename=filename, content_type="application/zip")
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/upload/file", data=form)
        return resp.status, await resp.json()


@pytest.mark.asyncio
async def test_zip_over_the_configured_cap_is_413_naming_the_limit(
    cfg_file, upload_dir, files_sel
) -> None:
    _set_limit(cfg_file, 1)
    status, body = await _post_upload(_zip_bytes(pad=MB + 1024), "project.zip")
    assert status == 413, body
    assert body["code"] == "file_too_large"
    assert body["max_mb"] == 1
    assert "(max 1 MB)" in body["error"]
    assert "project.zip" in body["error"]
    assert list(upload_dir.glob("*")) == []


@pytest.mark.asyncio
async def test_zip_over_the_old_50_mb_line_is_accepted_under_the_new_default(
    cfg_file, upload_dir, files_sel, monkeypatch
) -> None:
    """The reported case: a file past the old fixed cap now lands. Scaled down --
    the old fixed cap is shrunk to 1 MB and the configured default to 2 MB -- so
    the test proves WHICH ceiling the handler consults without a 50 MB body."""
    monkeypatch.setattr("kiro_crew.dashboard.handlers.files._MAX_UPLOAD_BYTES", MB)
    monkeypatch.setattr("kiro_crew.dashboard.handlers.files.upload_max_bytes", lambda: 2 * MB)
    payload = _zip_bytes(pad=MB + 1024)
    status, body = await _post_upload(payload, "project.zip")
    assert status == 200, body
    assert Path(body["paths"][0]).read_bytes() == payload


@pytest.mark.asyncio
async def test_an_edit_takes_effect_on_the_next_upload(cfg_file, upload_dir, files_sel) -> None:
    payload = _zip_bytes(pad=MB + 1024)
    _set_limit(cfg_file, 1)
    status, _ = await _post_upload(payload, "a.zip")
    assert status == 413
    _set_limit(cfg_file, 2)
    status, body = await _post_upload(payload, "a.zip")
    assert status == 200, body


# ---------- dashboard config ----------


@pytest.fixture()
def config_app(cfg_file):
    from kiro_crew.dashboard.handlers.files import api_dashboard_config

    m = MagicMock()
    with patch("kiro_crew.dashboard.handlers.sel", return_value=m):
        app = web.Application()
        app.router.add_get("/api/dashboard/config", api_dashboard_config)
        app.router.add_put("/api/dashboard/config", api_dashboard_config)
        yield as_owner(app)


@pytest.mark.asyncio
async def test_dashboard_config_serves_the_limit_read_only(config_app, cfg_file) -> None:
    _set_limit(cfg_file, 250)
    async with TestClient(TestServer(config_app)) as client:
        resp = await client.get("/api/dashboard/config")
        body = await resp.json()
        assert body["upload_max_mb"] == 250
        # The settings UI spreads the whole GET body into its PUT: the
        # round-tripped read-only field must not 400 an unrelated save, and a
        # caller-supplied value must not be adopted.
        body["quick_send"] = True
        body["upload_max_mb"] = 1024
        resp = await client.put("/api/dashboard/config", json=body)
        assert resp.status == 200, await resp.text()
    assert KiroCrewConfig.load().dashboard.upload_max_mb == 250


# ---------- knowledge ingest ----------


class _FakeStore:
    def __init__(self) -> None:
        self.db = MagicMock()
        self._rows: dict[str, dict] = {}

    def get_source_by_uri(self, uri):
        return self._rows.get(uri)

    def add_source(self, *, name, source_type, uri, properties):
        self._rows[uri] = {"id": "sid1"}
        return "sid1"


async def _post_ingest(payload: bytes, filename: str) -> tuple[int, dict]:
    from kiro_crew.dashboard.handlers.knowledge import ingest_file

    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=_FakeStore())
    app["knowledge_pipeline"] = SimpleNamespace(
        ingest_file=AsyncMock(),
        reserve_import_budget=AsyncMock(return_value=None),
        release_import_budget=MagicMock(),
        ingestion_in_flight=contextlib.nullcontext,
    )
    app.router.add_post("/api/knowledge/ingest", ingest_file)
    form = aiohttp.FormData()
    form.add_field("file", payload, filename=filename, content_type="text/plain")
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/knowledge/ingest", data=form)
        return resp.status, await resp.json()


@pytest.mark.asyncio
async def test_knowledge_ingest_enforces_and_names_the_configured_limit(cfg_file) -> None:
    _set_limit(cfg_file, 1)
    with patch("kiro_crew.dashboard.handlers.knowledge.sel") as sel:
        sel.return_value = MagicMock()
        status, body = await _post_ingest(b"a" * (MB + 1), "notes.txt")
    assert status == 413, body
    assert body["error"] == "file too large (max 1 MB)"
