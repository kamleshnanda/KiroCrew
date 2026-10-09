"""``hooks.confirm_tools``: tool patterns that always ask before running.

A user-added MCP server is written into the agent's ``allowedTools`` on every
rebuild, and an ``allowedTools`` entry is approved by the backend without a
permission request, so the approval gate never sees the call. ``confirm_tools``
is the operator's opt-in to keep a server on the gate: a server it names stays
mounted but gets no blanket grant, and at the gate a match outranks an
``auto_approve_tools`` grant. With the key unset nothing changes.
"""

from __future__ import annotations

import json

import pytest

from kiro_crew.agent_materialization import auto_approve
from kiro_crew.hooks import (
    TOOL_ALLOW,
    TOOL_AUTO_APPROVE,
    TOOL_DENY,
    HookManager,
    HooksConfig,
)

# ── config parsing ────────────────────────────────────────────────────────────


def test_confirm_tools_defaults_to_empty() -> None:
    assert HooksConfig.from_dict({}).confirm_tools == []
    assert HooksConfig().confirm_tools == []


def test_confirm_tools_parses_and_round_trips() -> None:
    cfg = HooksConfig.from_dict({"confirm_tools": ["@atlassian/*", "@github/create_*"]})
    assert cfg.confirm_tools == ["@atlassian/*", "@github/create_*"]
    assert HooksConfig.from_dict(cfg.to_dict()).confirm_tools == cfg.confirm_tools


@pytest.mark.parametrize("junk", ["@atlassian", 1, None, {"a": 1}])
def test_confirm_tools_junk_degrades_to_empty(junk: object) -> None:
    assert HooksConfig.from_dict({"confirm_tools": junk}).confirm_tools == []


def test_confirm_tools_drops_non_string_entries() -> None:
    cfg = HooksConfig.from_dict({"confirm_tools": ["@jira", 3, None]})
    assert cfg.confirm_tools == ["@jira"]


# ── server matching (rebuild side) ────────────────────────────────────────────


@pytest.mark.parametrize(
    "patterns",
    [
        ("@atlassian",),
        ("@atlassian/*",),
        ("@atlassian/createJiraIssue",),
        ("Running: @atlassian/create*",),
        ("@ATLASSIAN",),
        ("@atl*",),
        ("@*",),
        ("*",),
    ],
)
def test_server_requires_confirmation_matches(patterns: tuple[str, ...]) -> None:
    assert auto_approve.server_requires_confirmation("atlassian", patterns)


@pytest.mark.parametrize(
    "patterns",
    [
        (),
        ("@github",),
        ("@atlassian-cloud",),
        ("execute_bash",),
        ("Running: git*",),
        ("@",),
    ],
)
def test_server_requires_confirmation_does_not_match(patterns: tuple[str, ...]) -> None:
    assert not auto_approve.server_requires_confirmation("atlassian", patterns)


def test_confirm_tool_patterns_unreadable_config_yields_none(monkeypatch) -> None:
    from kiro_crew.config import loader

    def _boom(*_a, **_k):
        raise OSError("config unreadable")

    monkeypatch.setattr(loader.KiroCrewConfig, "load", classmethod(lambda cls, *a, **k: _boom()))
    assert auto_approve._confirm_tool_patterns() == ()


# ── rebuild: the grant is withheld only for named servers ────────────────────


def _rebuild(tmp_path, monkeypatch, servers: dict, patterns: tuple[str, ...]) -> dict:
    from kiro_crew.agent import rebuild_agent_config

    agent_dir = tmp_path / "agents"
    agent_dir.mkdir()
    (agent_dir / "defaults.json").write_text(json.dumps({"name": "kirocrew"}))
    (agent_dir / "prompt.md").write_text("prompt")
    monkeypatch.setenv("KIROCREW_PROJECT_DIR", str(tmp_path))
    kiro_dir = tmp_path / ".kiro" / "agents"
    kiro_dir.mkdir(parents=True)
    settings_dir = tmp_path / ".kiro" / "settings"
    settings_dir.mkdir(parents=True)
    (settings_dir / "mcp.json").write_text(json.dumps({"mcpServers": servers}))
    monkeypatch.setattr("kiro_crew.agent.KIRO_AGENTS_DIR", kiro_dir)
    monkeypatch.setattr("kiro_crew.agent._KIRO_MCP_JSON", settings_dir / "mcp.json")
    monkeypatch.setattr("kiro_crew.agent._CC_MCP_JSON", tmp_path / "nonexistent_cc.json")
    monkeypatch.setattr("kiro_crew.agent._KIROCREW_BIN", "/usr/bin/kirocrew")
    monkeypatch.setattr("shutil.which", lambda cmd, path=None: f"/usr/bin/{cmd}")
    monkeypatch.setattr(auto_approve, "_confirm_tool_patterns", lambda: patterns)

    rebuild_agent_config()
    return json.loads((kiro_dir / "kirocrew.json").read_text(encoding="utf-8"))


def test_rebuild_without_confirm_tools_keeps_the_grant(tmp_path, monkeypatch) -> None:
    data = _rebuild(tmp_path, monkeypatch, {"atlassian": {"command": "atl"}}, ())
    assert "@atlassian" in data["tools"]
    assert "@atlassian" in data["allowedTools"]


def test_rebuild_withholds_the_grant_for_a_named_server(tmp_path, monkeypatch) -> None:
    data = _rebuild(
        tmp_path,
        monkeypatch,
        {"atlassian": {"command": "atl"}, "github": {"command": "gh"}},
        ("@atlassian/create*",),
    )
    # Still mounted, so the agent keeps the server's tools.
    assert "atlassian" in data["mcpServers"]
    assert "@atlassian" in data["tools"]
    # No blanket grant in either spelling, so each call raises a permission request.
    assert not [ref for ref in data["allowedTools"] if ref.startswith("@atlassian")]
    # A server the operator did not name is untouched.
    assert "@github" in data["allowedTools"]


def test_rebuild_drops_the_emitted_auto_approve_for_a_named_server(tmp_path, monkeypatch) -> None:
    data = _rebuild(
        tmp_path,
        monkeypatch,
        {
            "atlassian": {"command": "atl", "autoApprove": ["createJiraIssue"]},
            "github": {"command": "gh", "autoApprove": ["get_issue"]},
        },
        ("@atlassian",),
    )
    assert "autoApprove" not in data["mcpServers"]["atlassian"]
    assert data["mcpServers"]["github"].get("autoApprove") == ["get_issue"]


def test_rebuild_strips_a_grant_left_from_an_earlier_build(tmp_path, monkeypatch) -> None:
    _rebuild(tmp_path, monkeypatch, {"atlassian": {"command": "atl"}}, ())
    # The same tree rebuilt after the operator opts in.
    from kiro_crew.agent import rebuild_agent_config

    monkeypatch.setattr(auto_approve, "_confirm_tool_patterns", lambda: ("@atlassian",))
    rebuild_agent_config()
    data = json.loads((tmp_path / ".kiro" / "agents" / "kirocrew.json").read_text(encoding="utf-8"))
    assert "@atlassian" in data["tools"]
    assert not [ref for ref in data["allowedTools"] if ref.startswith("@atlassian")]


# ── gate: a confirm match outranks an operator grant ─────────────────────────


def _mcp_call(mgr: HookManager, server: str, tool: str, *, title: str = "") -> str:
    return mgr.on_tool_call(
        title or f"Running: @{server}/{tool}",
        mcp_server_name=server,
        mcp_tool_name=tool,
        mcp_identity_trusted=True,
    ).action


def test_gate_grant_applies_without_confirm_tools() -> None:
    mgr = HookManager(HooksConfig(auto_approve_tools=["@atlassian/*"]))
    assert _mcp_call(mgr, "atlassian", "createJiraIssue") == TOOL_AUTO_APPROVE


@pytest.mark.parametrize("pattern", ["@atlassian", "@atlassian/create*", "Running: @atlassian/*"])
def test_gate_confirm_outranks_the_grant(pattern: str) -> None:
    mgr = HookManager(HooksConfig(auto_approve_tools=["@atlassian/*"], confirm_tools=[pattern]))
    assert _mcp_call(mgr, "atlassian", "createJiraIssue") == TOOL_ALLOW


def test_gate_confirm_leaves_other_tools_granted() -> None:
    mgr = HookManager(
        HooksConfig(auto_approve_tools=["@atlassian/*"], confirm_tools=["@atlassian/create*"])
    )
    assert _mcp_call(mgr, "atlassian", "getJiraIssue") == TOOL_AUTO_APPROVE


def test_gate_confirm_cannot_be_dodged_by_the_title() -> None:
    mgr = HookManager(
        HooksConfig(auto_approve_tools=["@atlassian/*"], confirm_tools=["@atlassian/create*"])
    )
    action = _mcp_call(mgr, "atlassian", "createJiraIssue", title="Look up the ticket")
    assert action == TOOL_ALLOW


def test_gate_confirm_does_not_override_a_deny() -> None:
    mgr = HookManager(
        HooksConfig(auto_deny_tools=["@atlassian/delete*"], confirm_tools=["@atlassian"])
    )
    assert _mcp_call(mgr, "atlassian", "deleteJiraIssue") == TOOL_DENY
