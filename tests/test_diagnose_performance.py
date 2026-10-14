from __future__ import annotations

from scripts.diagnose import diagnose, load_thresholds, reference_cpa
from tests.helpers import make_banner, make_ds, make_group, stats

TH = load_thresholds()


def found(ds):
    return {(f["rule"], f["entity"]["id"]): f for f in diagnose(ds, TH)["findings"]}


def banners(gid, n=3):
    return [make_banner(id=gid * 10 + i, group_id=gid) for i in range(n)]


def day(d, spent, shows, clicks, conv=0):
    return {"date": d, **stats(spent, shows, clicks, conv)}


def test_spend_no_conversions_vs_target():
    g = make_group(1, stats=stats(spent=1200, shows=20000, clicks=200, conversions=0))
    f = found(make_ds([g], banners(1), target_cpa=500))[("spend_no_conversions", 1)]
    assert f["level"] == "red" and f["effect_rub"] == 1200.0


def test_no_reference_cpa_uses_min_spend():
    low = make_group(1, stats=stats(spent=2000, shows=20000, clicks=200))
    high = make_group(2, stats=stats(spent=5000, shows=20000, clicks=200))
    got = found(make_ds([low, high], banners(1) + banners(2)))
    assert ("spend_no_conversions", 1) not in got and ("spend_no_conversions", 2) in got


def test_cpa_above_target():
    g = make_group(1, stats=stats(spent=10000, shows=50000, clicks=500, conversions=10))
    f = found(make_ds([g], banners(1), target_cpa=500))[("cpa_above_target", 1)]
    assert f["effect_rub"] == 5000.0 and f["evidence"]["cpa"] == 1000.0


def test_cpa_above_needs_volume():
    g = make_group(1, stats=stats(spent=4000, shows=5000, clicks=50, conversions=2))
    assert ("cpa_above_target", 1) not in found(make_ds([g], banners(1), target_cpa=500))


def test_zero_conversions_never_cpa_above_target():
    g = make_group(1, stats=stats(spent=9000, shows=50000, clicks=500, conversions=0))
    got = found(make_ds([g], banners(1), target_cpa=500))
    assert ("cpa_above_target", 1) not in got and ("spend_no_conversions", 1) in got


def test_low_ctr_on_banner():
    b = make_banner(19, 1, stats=stats(spent=900, shows=10000, clicks=20))
    got = found(make_ds([make_group(1)], [b] + banners(1)))
    assert ("low_ctr", 19) in got


def test_low_ctr_needs_shows():
    b = make_banner(19, 1, stats=stats(spent=50, shows=1000, clicks=1))
    assert ("low_ctr", 19) not in found(make_ds([make_group(1)], [b] + banners(1)))


def test_low_ctr_confidence_uses_entity_clicks():
    b = make_banner(19, 1, stats=stats(spent=900, shows=10000, clicks=20))
    f = found(make_ds([make_group(1)], [b] + banners(1)))[("low_ctr", 19)]
    assert f["confidence"] == "low"


def test_low_ctr_falls_back_to_group_stats_without_banner_stats():
    g = make_group(1, stats=stats(spent=900, shows=10000, clicks=20))
    f = found(make_ds([g], banners(1)))[("low_ctr", 1)]
    assert f["entity"]["type"] == "group"


def test_low_ctr_skips_banners_of_deleted_group():
    live = make_group(1)
    dead = make_group(2, status="deleted")
    b = make_banner(29, 2, stats=stats(spent=900, shows=10000, clicks=20))
    assert ("low_ctr", 29) not in found(make_ds([live, dead], banners(1) + [b]))


def test_burnout():
    daily = [day(f"2026-09-{d:02d}", 100, 1000, 20) for d in range(1, 16)] + \
            [day(f"2026-09-{d:02d}", 100, 1000, 10) for d in range(16, 31)]
    g = make_group(1, daily=daily, stats=stats(3000, 30000, 450, 0, frequency=4.2))
    assert ("burnout", 1) in found(make_ds([g], banners(1)))


def test_burnout_sorts_rows_by_date():
    daily = [day(f"2026-09-{d:02d}", 100, 1000, 20) for d in range(1, 16)] + \
            [day(f"2026-09-{d:02d}", 100, 1000, 10) for d in range(16, 31)]
    shuffled = daily[1::2] + daily[0::2]
    g = make_group(1, daily=shuffled, stats=stats(3000, 30000, 450, 0, frequency=4.2))
    assert ("burnout", 1) in found(make_ds([g], banners(1)))


def test_no_burnout_with_low_frequency():
    daily = [day(f"2026-09-{d:02d}", 100, 1000, 20) for d in range(1, 16)] + \
            [day(f"2026-09-{d:02d}", 100, 1000, 10) for d in range(16, 31)]
    g = make_group(1, daily=daily, stats=stats(3000, 30000, 450, 0, frequency=1.5))
    assert ("burnout", 1) not in found(make_ds([g], banners(1)))


def test_no_burnout_without_frequency():
    daily = [day(f"2026-09-{d:02d}", 100, 1000, 20) for d in range(1, 16)] + \
            [day(f"2026-09-{d:02d}", 100, 1000, 10) for d in range(16, 31)]
    g = make_group(1, daily=daily, stats=stats(3000, 30000, 450, 0))
    assert ("burnout", 1) not in found(make_ds([g], banners(1)))


def test_no_burnout_with_fewer_than_four_rows():
    daily = [day("2026-09-01", 100, 1000, 20), day("2026-09-02", 100, 1000, 20),
             day("2026-09-03", 100, 1000, 5)]
    g = make_group(1, daily=daily, stats=stats(3000, 30000, 45, 0, frequency=4.2))
    assert ("burnout", 1) not in found(make_ds([g], banners(1)))


def test_learning_starved():
    g = make_group(1, autobidding_mode="max_goals", stats=stats(3000, 30000, 300, 8))
    assert ("learning_starved", 1) in found(make_ds([g], banners(1)))


def test_learning_starved_evidence_and_confidence():
    g = make_group(1, autobidding_mode="max_goals", stats=stats(3000, 30000, 300, 8))
    f = found(make_ds([g], banners(1)))[("learning_starved", 1)]
    assert f["evidence"]["conversions_per_week"] == 1.9 and f["confidence"] == "high"


def test_manual_bidding_mode_is_not_learning_starved():
    g = make_group(1, autobidding_mode="fixed", stats=stats(3000, 30000, 300, 8))
    assert ("learning_starved", 1) not in found(make_ds([g], banners(1)))


def test_paused_group_is_not_learning_starved():
    g = make_group(1, status="blocked", autobidding_mode="max_goals", stats=stats(3000, 30000, 300, 8))
    assert ("learning_starved", 1) not in found(make_ds([g], banners(1)))


def test_learning_uses_run_days_not_period():
    # 14 дней с показами, 25 конверсий → 12.5 в неделю ≥ 10; по всему периоду (30 дней) было бы 5.8
    daily = [day(f"2026-09-{d:02d}", 200, 1000, 20, 2) for d in range(1, 15)] + \
            [day(f"2026-09-{d:02d}", 0, 0, 0) for d in range(15, 31)]
    g = make_group(1, autobidding_mode="max_goals", daily=daily, stats=stats(2800, 14000, 280, 25))
    assert ("learning_starved", 1) not in found(make_ds([g], banners(1)))


def test_spend_concentration():
    gs = [make_group(1, stats=stats(8000, 1, 1)), make_group(2, stats=stats(1000, 1, 1)),
          make_group(3, stats=stats(1000, 1, 1))]
    assert ("spend_concentration", 1) in found(make_ds(gs, banners(1) + banners(2) + banners(3)))


def test_spend_concentration_evidence():
    gs = [make_group(1, stats=stats(8000, 1, 1)), make_group(2, stats=stats(1000, 1, 1)),
          make_group(3, stats=stats(1000, 1, 1))]
    f = found(make_ds(gs, banners(1) + banners(2) + banners(3)))[("spend_concentration", 1)]
    assert f["evidence"]["share"] == 0.8 and f["evidence"]["groups_with_spend"] == 3


def test_concentration_needs_three_groups():
    gs = [make_group(1, stats=stats(9000, 1, 1)), make_group(2, stats=stats(1000, 1, 1))]
    assert ("spend_concentration", 1) not in found(make_ds(gs, banners(1) + banners(2)))


def test_concentration_below_share_threshold():
    gs = [make_group(1, stats=stats(4000, 1, 1)), make_group(2, stats=stats(3000, 1, 1)),
          make_group(3, stats=stats(3000, 1, 1))]
    assert ("spend_concentration", 1) not in found(make_ds(gs, banners(1) + banners(2) + banners(3)))


def test_scale_leader():
    daily = [day(f"2026-09-{d:02d}", 950, 5000, 50, 3) for d in range(1, 31)]
    g = make_group(1, budget_day_rub=1000.0, daily=daily, stats=stats(28500, 150000, 1500, 90))
    f = found(make_ds([g], banners(1), target_cpa=500))[("scale_leader", 1)]
    assert f["level"] == "green" and f["effect_rub"] == round(950 * 0.5 * 30, 2)


def test_paused_leader_is_not_scaled():
    daily = [day(f"2026-09-{d:02d}", 950, 5000, 50, 3) for d in range(1, 31)]
    g = make_group(1, status="blocked", budget_day_rub=1000.0, daily=daily,
                   stats=stats(28500, 150000, 1500, 90))
    assert ("scale_leader", 1) not in found(make_ds([g], banners(1), target_cpa=500))


def test_scale_leader_averages_only_spend_days():
    daily = [day(f"2026-09-{d:02d}", 0, 0, 0) for d in range(1, 11)] + \
            [day(f"2026-09-{d:02d}", 950, 5000, 50, 3) for d in range(11, 31)]
    g = make_group(1, budget_day_rub=1000.0, daily=daily, stats=stats(28500, 150000, 1500, 90))
    f = found(make_ds([g], banners(1), target_cpa=500))[("scale_leader", 1)]
    assert f["evidence"]["avg_daily_spent"] == 950.0 and f["effect_rub"] == round(950 * 0.5 * 30, 2)


def test_scale_leader_needs_daily_budget():
    daily = [day(f"2026-09-{d:02d}", 950, 5000, 50, 3) for d in range(1, 31)]
    for b in (0.0, None):
        g = make_group(1, budget_day_rub=b, daily=daily, stats=stats(28500, 150000, 1500, 90))
        assert ("scale_leader", 1) not in found(make_ds([g], banners(1), target_cpa=500))


# ---------- покрытие объявлений и группы без статистики ----------

def _partial(banner_groups):
    groups = [make_group(1), make_group(2), make_group(3)]
    ds = make_ds(groups, banners(1), coverage={"path": "A", "banners": True, "summary": True, "stats": True,
                                               "pixels": True, "metrika": False,
                                               "banner_groups": banner_groups})
    return ds


def test_no_active_banners_only_for_groups_with_banner_coverage():
    ds = _partial([1, 2])  # группа 2 опрошена и пуста; группа 3 не опрошена вовсе
    got = found(ds)
    assert ("no_active_banners", 2) in got
    assert ("no_active_banners", 3) not in got


def test_banners_per_group_only_for_covered_groups():
    ds = _partial([1])
    ds["banners"] = [make_banner(21, 2)]
    ds["groups"][1]["banner_ids"] = [21]
    got = found(ds)
    assert ("banners_per_group", 2) not in got  # группа 2 не в banner_groups
    ds["coverage"]["banner_groups"] = [1, 2]
    assert ("banners_per_group", 2) in found(ds)


def test_low_ctr_group_level_for_groups_without_banner_coverage():
    g1 = make_group(1, stats=stats(900, 10000, 20))
    g2 = make_group(2, stats=stats(900, 10000, 20))
    b = make_banner(11, 1, stats=stats(900, 10000, 20))
    ds = make_ds([g1, g2], [b], coverage={"path": "A", "banners": True, "summary": True, "stats": True,
                                           "pixels": True, "metrika": False, "banner_groups": [1]})
    got = found(ds)
    assert got[("low_ctr", 11)]["entity"]["type"] == "banner"
    assert got[("low_ctr", 2)]["entity"]["type"] == "group"
    assert ("low_ctr", 1) not in got


def _missing_group(**kw):
    return make_group(2, **{"stats": stats(), "stats_missing": True, **kw})


def test_measured_excludes_stats_missing_from_reference_cpa():
    good = make_group(1, stats=stats(1000, 10000, 100, 5))
    fake = _missing_group(stats=stats(100, 10000, 100, 10))  # нули в датасете подменены, чтобы доказать фильтр
    ds = make_ds([good, fake], banners(1) + banners(2))
    assert reference_cpa(ds, TH) == (200.0, "median")


def test_stats_missing_group_gets_no_performance_findings():
    ds = make_ds([_missing_group()], banners(2), target_cpa=500)
    got = found(ds)
    assert not [k for k in got if k[1] == 2 and k[0] in (
        "spend_no_conversions", "cpa_above_target", "low_ctr", "burnout", "learning_starved",
        "spend_concentration", "scale_leader", "autodelete_risk")]


def test_stats_missing_group_skipped_by_autodelete_and_learning():
    g = _missing_group(created="2026-01-10 00:00:00", autobidding_mode="max_goals")
    got = found(make_ds([g], banners(2)))
    assert ("autodelete_risk", 2) not in got and ("learning_starved", 2) not in got
