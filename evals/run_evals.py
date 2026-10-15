"""Детерминированные эталоны: raw/ → dataset → findings, сравнение с ожидаемым множеством."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from evals.fixtures import CABINETS
from scripts._console import setup_console
from scripts.diagnose import diagnose, load_thresholds
from scripts.normalize import build_dataset, check_sanity


def run(name: str, workdir: Path):
    raw = Path(workdir) / name / "raw"
    meta = CABINETS[name](raw)
    ds = build_dataset(raw, meta["date_from"], meta["date_to"], meta["target_cpa"])
    assert check_sanity(ds) == [], f"{name}: синтетика не проходит проверку здравого смысла"
    found = {(f["rule"], f["entity"]["id"]) for f in diagnose(ds, load_thresholds())["findings"]}
    return found, meta["expected"]


def main() -> int:
    setup_console()
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        for name in sorted(CABINETS):
            found, expected = run(name, Path(tmp))
            ok = found == expected
            bad += not ok
            print(f"{'✓' if ok else '✗'} {name}: найдено {sorted(found)}"
                  + ("" if ok else f", ожидалось {sorted(expected)}"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
