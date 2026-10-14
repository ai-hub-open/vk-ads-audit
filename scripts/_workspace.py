"""
_workspace.py — рабочая папка аудита всегда вне папки скилла.

`python -m scripts.<имя>` запускается только из папки скилла, поэтому относительный
`--workspace` лёг бы внутрь скилла. Скилл, установленный плагином Claude Code, при
обновлении заменяется целиком — данные клиента пропали бы вместе со старой версией.
"""
from __future__ import annotations

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent


def is_inside_skill(path) -> bool:
    try:
        Path(path).resolve().relative_to(SKILL_ROOT)
    except ValueError:
        return False
    return True


def resolve_workspace(raw, what: str = "Рабочая папка") -> Path:
    """Абсолютный путь; внутри скилла — остановка с объяснением."""
    path = Path(raw).expanduser().resolve()
    if is_inside_skill(path):
        sys.exit(
            f"{what} {path} лежит внутри папки скилла ({SKILL_ROOT}).\n"
            "При обновлении плагина папка скилла заменяется целиком — данные клиента пропадут.\n"
            "Передай абсолютный путь вне скилла: <папка пользователя>/vk-audits/<slug>-<дата>"
        )
    return path
