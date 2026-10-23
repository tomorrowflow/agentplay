"""The skills installed on a factory node, read from its mirrored metadata.

The nightly pull mirrors each node's ``~/.claude`` skill metadata into
``<ARCHIVE>/<node>/`` and stamps ``skills-pulled-at`` when it succeeds. This
module turns that folder into the names a session on the node could invoke:
personal skills and commands bare, plugin ones as ``<plugin>:<name>``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ._core.ingestion.coding_agent.skill_names import is_valid_skill_name, skill_name_from_skill_md_path

STAMP = "skills-pulled-at"


def pulled_at(home: Path) -> str | None:
    """When the node's skills were last pulled; None when no inventory was captured."""
    try:
        return (home / STAMP).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def installed_skills(home: Path) -> frozenset[str] | None:
    """Every skill and command name the node could invoke; None without an inventory."""
    if pulled_at(home) is None:
        return None
    names = set(_names(home))
    for root, fallback in _plugin_roots(home):
        plugin = _read_json(root / ".claude-plugin" / "plugin.json").get("name")
        prefix = plugin if isinstance(plugin, str) and plugin else fallback
        names.update(f"{prefix}:{n}" for n in _names(root))
    return frozenset(names)


def _names(root: Path) -> Iterator[str]:
    for md in (root / "skills").rglob("SKILL.md"):
        if name := skill_name_from_skill_md_path(str(md)):
            yield name
    for md in (root / "commands").rglob("*.md"):
        if is_valid_skill_name(md.stem):
            yield md.stem


def _plugin_roots(home: Path) -> Iterator[tuple[Path, str]]:
    """Each counted plugin root with the name to use when its plugin.json has none."""
    plugins = _read_json(home / "plugins" / "installed_plugins.json").get("plugins")
    for key, installs in (plugins.items() if isinstance(plugins, dict) else ()):
        for install in installs if isinstance(installs, list) else ():
            path = install.get("installPath") if isinstance(install, dict) else None
            _, sep, rest = path.partition("/.claude/") if isinstance(path, str) else ("", "", "")
            if sep and (home / rest).is_dir():
                yield home / rest, key.partition("@")[0]
    for manifest in (home / "plugins" / "synced").rglob("plugin.json"):
        if manifest.parent.name == ".claude-plugin":
            root = manifest.parent.parent
            yield root, re.sub(r"~g\d+$", "", root.name)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}
