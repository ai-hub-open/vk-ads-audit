from __future__ import annotations

import pytest

from evals.fixtures import CABINETS
from evals.run_evals import run


@pytest.mark.parametrize("name", sorted(CABINETS))
def test_cabinet(name, tmp_path):
    found, expected = run(name, tmp_path)
    assert found == expected


def test_four_cabinets():
    assert set(CABINETS) == {"healthy", "leaking", "hygiene", "empty"}
