"""
raw_store.py — сырые ответы MCP на диске: один файл на вызов.

raw/<tool>__<part:03d>.json = {"tool", "arguments", "response"}. Пишет collect.py
(путь B) или модель (путь A) — формат один, дальше код общий.

    python -m scripts.raw_store check --workspace <абсолютный путь>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Optional

try:
    from scripts._console import setup_console
    from scripts._workspace import resolve_workspace
except ImportError:  # запуск напрямую
    from _console import setup_console
    from _workspace import resolve_workspace

REQUIRED_KEYS = ("tool", "arguments", "response")


def _files(raw_dir: Path, tool: str) -> list:
    return sorted(Path(raw_dir).glob(f"{tool}__*.json"))


def write_call(raw_dir: Path, tool: str, arguments: dict, response, part: Optional[int] = None) -> Path:
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    if part is None:
        part = len(_files(raw_dir, tool))
    path = raw_dir / f"{tool}__{part:03d}.json"
    path.write_text(json.dumps({"tool": tool, "arguments": dict(arguments or {}), "response": response},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"{path.name}: не читается как JSON ({e})") from None
    if not isinstance(data, dict) or any(k not in data for k in REQUIRED_KEYS):
        raise ValueError(f"{path.name}: нужны ключи {', '.join(REQUIRED_KEYS)} — "
                         "ответ инструмента кладётся в response целиком")
    return data


def read_calls(raw_dir: Path, tool: str) -> list:
    return [_load(p) for p in _files(raw_dir, tool)]


def items_of(raw_dir: Path, tool: str, where: Optional[Callable[[dict], bool]] = None) -> list:
    out: list = []
    for call in read_calls(raw_dir, tool):
        if where is not None and not where(call["arguments"]):
            continue
        resp = call["response"]
        if isinstance(resp, dict):
            out.extend(resp.get("items") or [])
    return out


def main(argv=None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(description="Проверка сырых ответов MCP в raw/")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check")
    p.add_argument("--workspace", required=True)
    args = parser.parse_args(argv)
    raw = resolve_workspace(args.workspace) / "raw"
    bad = []
    files = sorted(raw.glob("*__*.json"))
    for path in files:
        try:
            _load(path)
        except ValueError as e:
            bad.append(str(e))
    for line in bad:
        print(f"✗ {line}")
    print(f"файлов: {len(files)}, с ошибками: {len(bad)}")
    return 3 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
