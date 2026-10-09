"""The ``server`` form of ``/api/connections/mint``: sign in to a configured server.

A remote MCP server the owner added outside the Connections registry is signed in
to on the same mint engine as a provider. The caller names the server; the URL
the mint connects to comes from the owner's main agent spec, never the request.
These tests pin that bound and the routes' shape. The engine itself is stubbed:
its behaviour is covered in ``test_connections_mint.py``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from dashboard_owner_helpers import as_owner

from kiro_crew.connections import mint
from kiro_crew.dashboard.handlers import connections

_DOCS_URL = "https://docs.example.com/mcp"
NON_OWNER = {"X-Test-User": "someone-else"}


@pytest.fixture()
def agents_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A scratch main agent spec holding one remote, one local, one registry entry."""
    directory = tmp_path / "agents"
    directory.mkdir()
    (directory / "kirocrew.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "docsServer": {"url": _DOCS_URL},
                    "local-tool": {"command": "local-tool-bin", "args": []},
                    "plain-http": {"url": "ftp://files.example.com/mcp"},
                    "notion": {"url": "https://self-hosted.example.com/mcp"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("kiro_crew.agent.kiro_agents_dir_path", lambda: directory)
    monkeypatch.setattr(mint, "_mints", {})
    monkeypatch.setattr(mint, "_mints_lock", asyncio.Lock())
    return directory


@pytest.fixture()
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record the mints the route schedules instead of spawning kiro-cli."""
    calls: list[tuple[str, str]] = []

    async def _fake_start(slug: str, mcp_url: str, token: Any = None, prior: Any = None) -> None:
        calls.append((slug, mcp_url))

    monkeypatch.setattr(mint, "start_oauth_mint", _fake_start)
    return calls


async def _client() -> TestClient:
    app = web.Application()
    app.router.add_post("/api/connections/mint", connections.api_connections_mint)
    app.router.add_get("/api/connections/mint", connections.api_connections_mint_state)
    as_owner(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _drain_mint_tasks() -> None:
    if connections._mint_tasks:
        await asyncio.gather(*list(connections._mint_tasks))


@pytest.mark.asyncio
async def test_a_configured_remote_server_mints_against_the_url_in_the_owner_spec(
    agents_dir: Path, started: list[tuple[str, str]]
):
    client = await _client()
    try:
        resp = await client.post("/api/connections/mint", json={"server": "docsServer"})
        assert resp.status == 200
        body = await resp.json()
        assert body["slug"] == "docsServer"
        assert body["state"] == "minting"
        await _drain_mint_tasks()
    finally:
        await client.close()
    assert started == [("docsServer", _DOCS_URL)]


@pytest.mark.asyncio
async def test_a_url_in_the_request_is_never_what_the_mint_connects_to(
    agents_dir: Path, started: list[tuple[str, str]]
):
    client = await _client()
    try:
        resp = await client.post(
            "/api/connections/mint",
            json={"server": "docsServer", "mcp_url": "https://elsewhere.example.com/mcp"},
        )
        assert resp.status == 200
        await _drain_mint_tasks()
    finally:
        await client.close()
    assert started == [("docsServer", _DOCS_URL)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "server",
    [
        "not-configured",
        "local-tool",
        "plain-http",
        # A registry name stays on the provider path, even when the owner's entry
        # under it points somewhere else.
        "notion",
        "../escape",
        "",
        42,
    ],
)
async def test_a_server_that_is_not_a_configured_remote_server_is_refused(
    agents_dir: Path, started: list[tuple[str, str]], server: Any
):
    client = await _client()
    try:
        resp = await client.post("/api/connections/mint", json={"server": server})
        assert resp.status == 400
        assert (await resp.json())["code"] == "unknown_server"
    finally:
        await client.close()
    assert started == []


@pytest.mark.asyncio
async def test_the_configured_server_mint_is_owner_only(
    agents_dir: Path, started: list[tuple[str, str]]
):
    client = await _client()
    try:
        resp = await client.post(
            "/api/connections/mint", json={"server": "docsServer"}, headers=NON_OWNER
        )
        assert resp.status == 403
    finally:
        await client.close()
    assert started == []


@pytest.mark.asyncio
async def test_an_unreadable_main_spec_refuses_rather_than_minting(
    agents_dir: Path, started: list[tuple[str, str]]
):
    (agents_dir / "kirocrew.json").write_text("[not an object]", encoding="utf-8")
    client = await _client()
    try:
        resp = await client.post("/api/connections/mint", json={"server": "docsServer"})
        assert resp.status == 400
    finally:
        await client.close()
    assert started == []


@pytest.mark.asyncio
async def test_the_state_poll_reads_the_configured_server_row_by_its_exact_name(
    agents_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    asked: list[str] = []

    async def _no_expiry(slug: str) -> None:
        asked.append(slug)

    def _row(slug: str) -> dict[str, Any] | None:
        asked.append(slug)
        return {
            "state": "waiting",
            "oauth_url": "https://docs.example.com/authorize",
            "token": "t1",
        }

    monkeypatch.setattr(mint, "expire_dead_holder", _no_expiry)
    monkeypatch.setattr(mint, "pending_mint_for", _row)
    client = await _client()
    try:
        resp = await client.get("/api/connections/mint", params={"server": "docsServer"})
        assert resp.status == 200
        body = await resp.json()
        assert body["slug"] == "docsServer"
        assert body["oauth_url"] == "https://docs.example.com/authorize"

        refused = await client.get("/api/connections/mint", params={"server": "local-tool"})
        assert refused.status == 400
        assert (await refused.json())["code"] == "unknown_server"
    finally:
        await client.close()
    # Keyed by the exact configured name, not the registry's lowercase slug.
    assert asked == ["docsServer", "docsServer"]


@pytest.mark.asyncio
async def test_a_provider_mint_without_a_server_field_keeps_the_registry_bound(
    agents_dir: Path, started: list[tuple[str, str]]
):
    client = await _client()
    try:
        resp = await client.post("/api/connections/mint", json={"slug": "docsServer"})
        assert resp.status == 400
        assert (await resp.json())["code"] == "unknown_provider"
    finally:
        await client.close()
    assert started == []
