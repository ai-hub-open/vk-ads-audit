from __future__ import annotations

from scripts.diagnose import diagnose, load_thresholds, main, reference_cpa
from tests.helpers import make_banner, make_ds, make_group, stats

TH = load_thresholds()
HYGIENE = {"no_active_banners", "rejected_banners", "blocking_issues", "autodelete_risk", "banners_per_group",
           "low_budget", "duplicate_targeting", "unused_segments", "no_pixel"}


def rules(ds):
    return [(f["rule"], f["entity"]["id"]) for f in diagnose(ds, TH)["findings"] if f["rule"] in HYGIENE]


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


def test_no_banners_code_is_not_a_blocking_issue():
    assert "NO_BANNERS_WITH_ACTIVE_STATUS" not in TH["blocking_issue_codes"]
    ds = make_ds([make_group(1, issues=["NO_BANNERS_WITH_ACTIVE_STATUS"])], healthy_banners(1))
    assert not [r for r in rules(ds) if r[0] == "blocking_issues"]


def test_autodelete_risk():
    g = make_group(1, created="2026-03-01 00:00:00", stats=stats(shows=0))
    assert ("autodelete_risk", 1) in rules(make_ds([g], healthy_banners(1)))
    young = make_group(2, created="2026-09-01 00:00:00")
    assert ("autodelete_risk", 2) not in rules(make_ds([young], healthy_banners(2)))


def test_autodelete_skipped_when_stats_not_collected():
    g = make_group(1, created="2026-03-01 00:00:00", stats=stats(shows=0))
    cov = {"path": "B", "banners": True, "summary": False, "stats": False, "pixels": True, "metrika": False}
    assert rules(make_ds([g], healthy_banners(1), coverage=cov)) == []


def test_banners_per_group():
    assert ("banners_per_group", 1) in rules(make_ds([make_group(1)], healthy_banners(1, n=1)))
    assert ("banners_per_group", 2) in rules(make_ds([make_group(2)], healthy_banners(2, n=9)))


def test_low_budget():
    assert ("low_budget", 1) in rules(make_ds([make_group(1, budget_day_rub=200.0)], healthy_banners(1)))


def test_zero_budget_means_no_limit():
    assert rules(make_ds([make_group(1, budget_day_rub=0)], healthy_banners(1))) == []


def test_low_budget_for_learning_with_target():
    g = make_group(1, autobidding_mode="max_goals", budget_day_rub=600.0)
    assert ("low_budget", 1) in rules(make_ds([g], healthy_banners(1), target_cpa=500))


def test_manual_bidding_mode_has_no_learning_budget_finding():
    g = make_group(1, autobidding_mode="fixed", budget_day_rub=500.0)
    assert ("low_budget", 1) not in rules(make_ds([g], healthy_banners(1), target_cpa=500))


def test_learning_budget_uses_median_when_no_target():
    gs = [make_group(1, autobidding_mode="max_goals", budget_day_rub=500.0,
                     stats=stats(spent=1000, clicks=100, conversions=10)),
          make_group(2, stats=stats(spent=3000, clicks=100, conversions=10)),
          make_group(3, stats=stats(spent=5000, clicks=100, conversions=10))]
    ds = make_ds(gs, [b for i in (1, 2, 3) for b in healthy_banners(i)])
    f = [x for x in diagnose(ds, TH)["findings"] if x["rule"] == "low_budget"]
    assert [(x["entity"]["id"], x["evidence"]["ref_source"], x["evidence"]["needed_rub"]) for x in f] \
        == [(1, "median", 600.0)]


def test_duplicate_targeting():
    g1 = make_group(1, targeting_key='{"geo":1}')
    g2 = make_group(2, targeting_key='{"geo":1}')
    ds = make_ds([g1, g2], healthy_banners(1) + healthy_banners(2))
    assert [r for r in rules(ds) if r[0] == "duplicate_targeting"] == [("duplicate_targeting", 1)]
    f = [x for x in diagnose(ds, TH)["findings"] if x["rule"] == "duplicate_targeting"][0]
    assert f["evidence"]["groups"] == [1, 2]


def test_unused_segments():
    ds = make_ds([make_group(1, segment_ids=[5])], healthy_banners(1),
                 segments=[{"id": 5, "name": "used"}, {"id": 6, "name": "unused"}])
    assert ("unused_segments", 6) in rules(ds) and ("unused_segments", 5) not in rules(ds)


def test_no_pixel():
    ds = make_ds([make_group(1)], healthy_banners(1), pixels=[])
    assert ("no_pixel", 1) in rules(ds)


def test_no_pixel_skipped_when_pixels_not_fetched():
    cov = {"path": "B", "banners": True, "summary": True, "stats": True, "pixels": False, "metrika": False}
    assert rules(make_ds([make_group(1)], healthy_banners(1), pixels=[], coverage=cov)) == []


def test_no_pixel_when_pixels_fetched_empty_and_banners_not_covered():
    cov = {"path": "B", "banners": False, "summary": True, "stats": True, "pixels": True, "metrika": False}
    assert ("no_pixel", 1) in rules(make_ds([make_group(1)], [], pixels=[], coverage=cov))


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


def test_finding_has_check_id():
    f = diagnose(make_ds([make_group(1, budget_day_rub=200.0)], healthy_banners(1)), TH)["findings"]
    assert {x["rule"]: x["check"] for x in f}["low_budget"] == "D1"


def test_low_budget_white_band():
    f = [x for x in diagnose(make_ds([make_group(1, budget_day_rub=400.0)], healthy_banners(1)), TH)["findings"]
         if x["rule"] == "low_budget"]
    assert [x["level"] for x in f] == ["white"]


def test_learning_budget_band_wins_over_white_band():
    g = make_group(1, autobidding_mode="max_goals", budget_day_rub=400.0)
    f = [x for x in diagnose(make_ds([g], healthy_banners(1), target_cpa=500), TH)["findings"]
         if x["rule"] == "low_budget"]
    assert [(x["level"], "needed_rub" in x["evidence"]) for x in f] == [("orange", True)]


def test_banners_per_group_norm_2_to_5():
    assert ("banners_per_group", 2) in rules(make_ds([make_group(2)], healthy_banners(2, n=6)))
    assert ("banners_per_group", 3) not in rules(make_ds([make_group(3)], healthy_banners(3, n=5)))


def test_rejected_for_documents_points_to_settings():
    b = make_banner(19, 1, moderation_status="banned", moderation_reasons=["Нужны документы на услугу"])
    f = [x for x in diagnose(make_ds([make_group(1)], healthy_banners(1) + [b]), TH)["findings"]
         if x["rule"] == "rejected_banners"][0]
    assert "Документы для модерации" in f["action"]


def test_missing_dataset_reports_and_returns_2(tmp_path, capsys):
    assert main(["--workspace", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "нет dataset.json — сначала нормализация (python -m scripts.normalize ...)" in err
    assert "Traceback" not in err
