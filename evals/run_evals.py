"""Детерминированные эталоны: raw/ → dataset → findings, сравнение с ожидаемым множеством."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from evals.fixtures import CABINETS
from scripts.checks import run_checks
from scripts._console import setup_console
from scripts.diagnose import diagnose, load_thresholds
from scripts.normalize import build_dataset, check_sanity


def _dataset(name, workdir):
    raw = Path(workdir) / name / "raw"
    meta = CABINETS[name](raw)
    ds = build_dataset(raw, meta["date_from"], meta["date_to"], meta["target_cpa"], as_of=meta.get("as_of"))
    assert check_sanity(ds) == [], f"{name}: синтетика не проходит проверку здравого смысла: {check_sanity(ds)}"
    return ds, meta


def run(name: str, workdir: Path):
    ds, meta = _dataset(name, workdir)
    findings = diagnose(ds, load_thresholds())["findings"]
    if "expected_rules" in meta:
        fired = {f["rule"] for f in findings}
        return (meta["expected_rules"] & fired), meta["expected_rules"]
    found = {(f["rule"], f["entity"]["id"]) for f in findings}
    if meta.get("scope") is not None:
        found = {x for x in found if x[0] in meta["scope"]}
    return found, meta["expected"]


def checklist_of(name: str, workdir: Path) -> dict:
    ds, _ = _dataset(name, workdir)
    th = load_thresholds()
    result = diagnose(ds, th)
    return run_checks(ds, th, result["findings"], result["evaluated"])


def main() -> int:
    setup_console()
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        for name in sorted(CABINETS):
            found, expected = run(name, Path(tmp))
            ok = found == expected
            bad += not ok
            if ok:
                print(f"✓ {name}: {len(found)} находок совпало с ожидаемыми")
            elif isinstance(expected, set) and all(isinstance(x, str) for x in expected):
                print(f"✗ {name}: не сработали правила {sorted(expected - found)}")
            else:
                print(f"✗ {name}: найдено {sorted(found)}, ожидалось {sorted(expected)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
