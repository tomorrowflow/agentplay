"""The package opens no network connection of its own."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import agentplay


def test_no_module_imports_an_http_client():
    src = Path(agentplay.__file__).parent
    hits = sorted(str(p.relative_to(src)) for p in src.rglob("*.py")
                  if any(m in p.read_text(encoding="utf-8") for m in ("urllib.request", "http.client")))
    assert hits == []


def test_importing_the_package_makes_no_network_call():
    code = ("import socket\n"
            "def no(*a, **k): raise AssertionError('network')\n"
            "socket.socket.connect = no\n"
            "import agentplay.cli, agentplay.server\n")
    subprocess.run([sys.executable, "-c", code], check=True)
