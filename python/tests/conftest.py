from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture()
def skills_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """A git repo whose first commit has project skills a, b and c, and whose second adds late."""
    repo = tmp_path / "repo"

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def add(*paths: str) -> str:
        for rel in paths:
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text("---\nname: x\n---\n")
        git("add", ".")
        git("commit", "-q", "-m", "skills")
        return git("rev-parse", "HEAD")

    repo.mkdir()
    git("init", "-q")
    first = add(".claude/skills/a/SKILL.md", ".claude/skills/a/notes.md", ".claude/skills/cat/b/SKILL.md",
                ".claude/commands/c.md", "README.md")
    second = add(".claude/skills/late/SKILL.md")
    return repo, first, second
