"""
raw_store.py — сырые ответы MCP на диске: один файл на вызов.

raw/<tool>__<part:03d>.json = {"tool", "arguments", "response"}. Пишет collect.py
(путь B) или модель (путь A) — формат один, дальше код общий.

    python -m scripts.raw_store check --workspace <абсолютный путь>
"""
from __future__ import annotations

import argparse
import json
import re
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
_PART_RE = re.compile(r"[0-9]+")


def _part_of(path: Path, tool: str) -> int:
    rest = path.name[len(tool) + 2:-len(".json")]
    if not _PART_RE.fullmatch(rest):
        raise ValueError(f"{path.name}: номер части должен быть целым числом")
    return int(rest)


def _files(raw_dir: Path, tool: str) -> list:
    raw_dir = Path(raw_dir)
    if not raw_dir.is_dir():
        return []
    found = [p for p in raw_dir.iterdir()
             if p.is_file() and p.name.startswith(tool + "__") and p.name.endswith(".json")]
    return sorted(found, key=lambda p: _part_of(p, tool))


def write_call(raw_dir: Path, tool: str, arguments: dict, response, part: Optional[int] = None) -> Path:
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    if part is None:
        parts = [_part_of(p, tool) for p in _files(raw_dir, tool)]
        part = max(parts) + 1 if parts else 0
    path = raw_dir / f"{tool}__{part:03d}.json"
    text = json.dumps({"tool": tool, "arguments": dict(arguments or {}), "response": response},
                      ensure_ascii=False, indent=1)
    try:
        with path.open("x", encoding="utf-8") as f:
            f.write(text)
    except FileExistsError:
        raise FileExistsError(f"{path.name} уже есть в {raw_dir} — перезапись запрещена") from None
    return path


def _load(path: Path, tool: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise ValueError(f"{path.name}: не читается как JSON ({e})") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: корень должен быть объектом с ключами {', '.join(REQUIRED_KEYS)}")
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ValueError(f"{path.name}: нет ключей {', '.join(missing)} — "
                         "ответ инструмента кладётся в response целиком")
    if not isinstance(data["arguments"], dict):
        raise ValueError(f"{path.name}: arguments должен быть объектом, а не {type(data['arguments']).__name__}")
    if data["tool"] != tool:
        raise ValueError(f"{path.name}: tool в файле {data['tool']!r}, а ожидался {tool!r}")
    return data


def _read(raw_dir: Path, tool: str) -> list:
    return [(p, _load(p, tool)) for p in _files(raw_dir, tool)]


def read_calls(raw_dir: Path, tool: str) -> list:
    return [data for _, data in _read(raw_dir, tool)]


def items_of(raw_dir: Path, tool: str, where: Optional[Callable[[dict], bool]] = None) -> list:
    out: list = []
    for path, call in _read(raw_dir, tool):
        resp = call["response"]
        if not isinstance(resp, dict):
            raise ValueError(f"{path.name}: response должен быть объектом, а не {type(resp).__name__}")
        if "items" in resp and not isinstance(resp["items"], list):
            raise ValueError(f"{path.name}: items должен быть списком, а не {type(resp['items']).__name__}")
        if where is not None and not where(call["arguments"]):
            continue
        out.extend(resp.get("items") or [])
    return out


def _tool_of(path: Path) -> str:
    stem = path.name[:-len(".json")] if path.name.endswith(".json") else ""
    tool, sep, part = stem.rpartition("__")
    if not sep or not tool or not _PART_RE.fullmatch(part):
        raise ValueError(f"{path.name}: имя файла должно быть <tool>__<номер>.json")
    return tool


def main(argv=None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(description="Проверка сырых ответов MCP в raw/")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check")
    p.add_argument("--workspace", required=True)
    args = parser.parse_args(argv)
    raw = resolve_workspace(args.workspace) / "raw"
    if not raw.is_dir():
        print(f"✗ нет папки raw/: {raw}")
        print("файлов: 0, с ошибками: 1")
        return 3
    files = sorted(p for p in raw.iterdir() if p.is_file())
    bad = []
    for path in files:
        try:
            _load(path, _tool_of(path))
        except ValueError as e:
            bad.append(str(e))
    for line in bad:
        print(f"✗ {line}")
    print(f"файлов: {len(files)}, с ошибками: {len(bad)}")
    return 3 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
