"""
make_bundle.py — архив vk-ads-audit.skill/.zip для Claude Desktop и релизов.

Белый шум исключений: в архив не попадают ни секреты (.env, .mcp.json), ни данные
клиентов (vk-audits/), ни файлы разработки. Собирать из чистого клона: в git worktree
`.git` — файл и иначе уехал бы в архив (исключается и он).

    python -m scripts.make_bundle [--output dist/vk-ads-audit.skill]
"""
from __future__ import annotations

import argparse
import fnmatch
import sys
import zipfile
from pathlib import Path

try:
    from scripts._console import setup_console
    from scripts._workspace import SKILL_ROOT
except ImportError:  # запуск напрямую
    from _console import setup_console
    from _workspace import SKILL_ROOT

NAME = "vk-ads-audit"
EXCLUDE_DIRS = {".git", "__pycache__", ".pytest_cache", "tests", "evals", "docs", ".github",
                ".claude-plugin", ".claude", ".superpowers", "vk-audits", "dist", ".venv", "venv"}
EXCLUDE_FILES = {".git", ".env", ".mcp.json", ".gitignore", "pytest.ini", "requirements-dev.txt",
                 "CHANGELOG.md", ".DS_Store", "Thumbs.db"}
EXCLUDE_GLOBS = ("*.pyc", "*.pyo", "*.zip", "*.skill", "*.tmp", "*.bak")


def _skip(rel: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel.parts[:-1]) or rel.parts[0] in EXCLUDE_DIRS:
        return True
    return rel.name in EXCLUDE_FILES or any(fnmatch.fnmatch(rel.name, p) for p in EXCLUDE_GLOBS)


def build(output: Path) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(SKILL_ROOT.rglob("*")):
            rel = path.relative_to(SKILL_ROOT)
            if path.is_file() and not _skip(rel):
                zf.write(path, f"{NAME}/{rel.as_posix()}")
    return output


def main(argv=None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(description="Собрать архив скилла")
    parser.add_argument("--output", default=str(SKILL_ROOT / "dist" / f"{NAME}.skill"))
    args = parser.parse_args(argv)
    out = build(Path(args.output))
    print(f"✓ {out} ({out.stat().st_size // 1024} КБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
