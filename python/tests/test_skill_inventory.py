"""`skill_inventory`: the skill names a factory node could invoke, from its mirrored metadata."""

from __future__ import annotations

import json
from pathlib import Path

from agentplay.skill_inventory import installed_skills, pulled_at


def _file(path: Path, text: str = "---\nname: x\n---\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _node(tmp_path: Path) -> Path:
    home = tmp_path / "linux-factory-mini"
    _file(home / "skills-pulled-at", "2026-10-16T02:00:00Z\n")
    _file(home / "skills" / "own-skill" / "SKILL.md")
    _file(home / "commands" / "ship-it.md")
    cache = home / "plugins" / "cache" / "mattpocock"
    for ver, skill in (("1.0.0", "old-only"), ("2.0.0", "grilling")):
        root = cache / "mattpocock-skills" / ver
        _file(root / ".claude-plugin" / "plugin.json", json.dumps({"name": "mattpocock-skills"}))
        _file(root / "skills" / skill / "SKILL.md")
    _file(cache / "mattpocock-skills" / "2.0.0" / "commands" / "review.md")
    _file(cache / "nameless" / "0.1.0" / "skills" / "plain" / "SKILL.md")
    _file(home / "plugins" / "installed_plugins.json", json.dumps({"version": 2, "plugins": {
        "mattpocock-skills@mattpocock": [{"installPath": "/home/factory/.claude/plugins/cache/mattpocock/mattpocock-skills/2.0.0"}],
        "nameless@mattpocock": [{"installPath": "/home/factory/.claude/plugins/cache/mattpocock/nameless/0.1.0"}],
    }}))
    synced = home / "plugins" / "synced" / "pstack~g2"
    _file(synced / ".claude-plugin" / "plugin.json", "{}")
    _file(synced / "skills" / "productivity" / "grill-me" / "SKILL.md")
    return home


def test_a_node_lists_personal_and_installed_plugin_skills_by_invocation_name(tmp_path):
    home = _node(tmp_path)
    assert pulled_at(home) == "2026-10-16T02:00:00Z"
    assert installed_skills(home) == {
        "own-skill",
        "ship-it",
        "mattpocock-skills:grilling",
        "mattpocock-skills:review",
        "nameless:plain",
        "pstack:grill-me",
    }


def test_a_cached_version_that_is_not_installed_does_not_count(tmp_path):
    skills = installed_skills(_node(tmp_path))
    assert skills is not None and not any(s.endswith("old-only") for s in skills)


def test_malformed_installed_plugins_json_drops_only_the_cache_plugins(tmp_path):
    home = _node(tmp_path)
    (home / "plugins" / "installed_plugins.json").write_text("{not json")
    assert installed_skills(home) == {"own-skill", "ship-it", "pstack:grill-me"}


def test_without_the_pull_stamp_there_is_no_inventory(tmp_path):
    home = _node(tmp_path)
    (home / "skills-pulled-at").unlink()
    assert pulled_at(home) is None
    assert installed_skills(home) is None
