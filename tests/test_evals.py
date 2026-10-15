from __future__ import annotations

import pytest

from evals.fixtures import CABINETS
from evals.run_evals import run


@pytest.mark.parametrize("name", sorted(CABINETS))
def test_cabinet(name, tmp_path):
    found, expected = run(name, tmp_path)
    assert found == expected


def test_five_cabinets():
    assert set(CABINETS) == {"healthy", "leaking", "hygiene", "empty", "path_a_partial"}


def test_path_a_partial_covers_only_five_groups(tmp_path):
    from evals.fixtures import path_a_partial
    from scripts.normalize import build_dataset
    meta = path_a_partial(tmp_path / "raw")
    ds = build_dataset(tmp_path / "raw", meta["date_from"], meta["date_to"], meta["target_cpa"], "A")
    assert ds["coverage"]["banner_groups"] == [1, 2, 3, 4, 5]
    assert any("для 2 групп" in n for n in ds["notes"])
