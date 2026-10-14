from __future__ import annotations

from scripts.quick import quick_summary
from tests.helpers import make_ds, make_group, stats


def rows(values):
    # values: список (spent, conversions) на даты 2026-10-06..
    return [{"date": f"2026-10-{6 + i:02d}", **stats(s, 1000, 10, c)} for i, (s, c) in enumerate(values)]


def test_flags_and_lines():
    normal = make_group(1, name="Норма", daily=rows([(100, 2)] * 8))
    spike = make_group(2, name="Всплеск", daily=rows([(100, 2)] * 7 + [(300, 2)]))
    stopped = make_group(3, name="Встала", daily=rows([(100, 2)] * 7 + [(0, 0)]))
    res = quick_summary(make_ds([normal, spike, stopped]), "2026-10-13")
    flags = {g["id"]: g["flags"] for g in res["groups"]}
    assert flags[1] == [] and "spend_spike" in flags[2] and "stopped" in flags[3]
    assert res["today"]["spent"] == 400.0
    assert 3 <= len(res["lines"]) <= 10
    assert res["lines"][0].startswith("📅")


def test_over_target():
    g = make_group(1, daily=rows([(100, 1)] * 7 + [(600, 1)]))
    res = quick_summary(make_ds([g]), "2026-10-13", target_cpa=300)
    assert "over_target" in res["groups"][0]["flags"]


def test_no_data_for_day():
    res = quick_summary(make_ds([make_group(1, daily=[])]), "2026-10-13")
    assert res["today"]["spent"] == 0.0
    assert any("нет" in line.lower() for line in res["lines"])


def test_lines_capped_at_ten():
    gs = [make_group(i, daily=rows([(100, 2)] * 7 + [(0, 0)])) for i in range(1, 30)]
    assert len(quick_summary(make_ds(gs), "2026-10-13")["lines"]) <= 10
