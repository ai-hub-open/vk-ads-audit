from __future__ import annotations

from scripts.diagnose import diagnose, load_thresholds, reference_cpa
from tests.helpers import make_banner, make_ds, make_group, stats

TH = load_thresholds()


def rules(ds):
    return [(f["rule"], f["entity"]["id"]) for f in diagnose(ds, TH)["findings"]]


def healthy_banners(gid, n=3):
    return [make_banner(id=gid * 10 + i, group_id=gid) for i in range(n)]


def test_healthy_cabinet_has_no_hygiene_findings():
    assert rules(make_ds([make_group(1)], healthy_banners(1))) == []


def test_no_active_banners():
    ds = make_ds([make_group(1)], [make_banner(11, status="blocked")])
    assert ("no_active_banners", 1) in rules(ds)


def test_rejected_banner():
    ds = make_ds([make_group(1)], healthy_banners(1) + [make_banner(19, moderation_status="banned",
                                                                    can_remoderate=True)])
    f = [x for x in diagnose(ds, TH)["findings"] if x["rule"] == "rejected_banners"][0]
    assert f["entity"]["id"] == 19 and f["evidence"]["can_remoderate"] is True


def test_blocking_issue():
    assert ("blocking_issues", 1) in rules(make_ds([make_group(1, issues=["NO_MONEY"])], healthy_banners(1)))


def test_autodelete_risk():
    g = make_group(1, created="2026-03-01 00:00:00", stats=stats(shows=0))
    assert ("autodelete_risk", 1) in rules(make_ds([g], healthy_banners(1)))
    young = make_group(2, created="2026-09-01 00:00:00")
    assert ("autodelete_risk", 2) not in rules(make_ds([young], healthy_banners(2)))


def test_banners_per_group():
    assert ("banners_per_group", 1) in rules(make_ds([make_group(1)], healthy_banners(1, n=1)))
    assert ("banners_per_group", 2) in rules(make_ds([make_group(2)], healthy_banners(2, n=9)))


def test_low_budget():
    assert ("low_budget", 1) in rules(make_ds([make_group(1, budget_day_rub=200.0)], healthy_banners(1)))


def test_low_budget_for_learning_with_target():
    g = make_group(1, autobidding_mode="max_goals", budget_day_rub=600.0)
    assert ("low_budget", 1) in rules(make_ds([g], healthy_banners(1), target_cpa=500))


def test_duplicate_targeting():
    g1 = make_group(1, targeting_key='{"geo":1}')
    g2 = make_group(2, targeting_key='{"geo":1}')
    found = rules(make_ds([g1, g2], healthy_banners(1) + healthy_banners(2)))
    assert ("duplicate_targeting", 1) in found or ("duplicate_targeting", 2) in found


def test_unused_segments():
    ds = make_ds([make_group(1, segment_ids=[5])], healthy_banners(1),
                 segments=[{"id": 5, "name": "used"}, {"id": 6, "name": "unused"}])
    assert ("unused_segments", 6) in rules(ds) and ("unused_segments", 5) not in rules(ds)


def test_no_pixel():
    ds = make_ds([make_group(1)], healthy_banners(1), pixels=[])
    assert ("no_pixel", 1) in rules(ds)


def test_deleted_groups_ignored():
    assert rules(make_ds([make_group(1, status="deleted", budget_day_rub=10.0)])) == []


def test_reference_cpa_sources():
    assert reference_cpa(make_ds([], target_cpa=500), TH) == (500.0, "target")
    gs = [make_group(i, stats=stats(spent=1000 * i, clicks=100, conversions=10)) for i in (1, 2, 3)]
    assert reference_cpa(make_ds(gs), TH) == (200.0, "median")
    assert reference_cpa(make_ds([make_group(1)]), TH) == (None, "none")


def test_sorted_by_level_then_effect():
    ds = make_ds([make_group(1, budget_day_rub=100.0)], healthy_banners(1, n=1), pixels=[])
    levels = [f["level"] for f in diagnose(ds, TH)["findings"]]
    order = {"red": 0, "orange": 1, "green": 2, "white": 3}
    assert levels == sorted(levels, key=order.get)
