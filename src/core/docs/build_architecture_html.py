"""Embed core_architecture.md into architecture_shell.html → architecture.html"""
from __future__ import annotations

import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
md = (HERE / "core_architecture.md").read_text(encoding="utf-8")
shell = (HERE / "architecture_shell.html").read_text(encoding="utf-8")
out = shell.replace("__MARKDOWN_JSON__", json.dumps(md))
(HERE / "architecture.html").write_text(out, encoding="utf-8", newline="\n")
print(f"Wrote {(HERE / 'architecture.html').resolve()} ({len(out)} bytes)")
