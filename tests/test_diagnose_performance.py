from __future__ import annotations

from scripts.diagnose import diagnose, load_thresholds
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


def test_low_ctr_on_banner():
    b = make_banner(19, 1, stats=stats(spent=900, shows=10000, clicks=20))
    got = found(make_ds([make_group(1)], [b] + banners(1)))
    assert ("low_ctr", 19) in got


def test_low_ctr_needs_shows():
    b = make_banner(19, 1, stats=stats(spent=50, shows=1000, clicks=1))
    assert ("low_ctr", 19) not in found(make_ds([make_group(1)], [b] + banners(1)))


def test_burnout():
    daily = [day(f"2026-09-{d:02d}", 100, 1000, 20) for d in range(1, 16)] + \
            [day(f"2026-09-{d:02d}", 100, 1000, 10) for d in range(16, 31)]
    g = make_group(1, daily=daily, stats=stats(3000, 30000, 450, 0, frequency=4.2))
    assert ("burnout", 1) in found(make_ds([g], banners(1)))


def test_no_burnout_with_low_frequency():
    daily = [day(f"2026-09-{d:02d}", 100, 1000, 20) for d in range(1, 16)] + \
            [day(f"2026-09-{d:02d}", 100, 1000, 10) for d in range(16, 31)]
    g = make_group(1, daily=daily, stats=stats(3000, 30000, 450, 0, frequency=1.5))
    assert ("burnout", 1) not in found(make_ds([g], banners(1)))


def test_learning_starved():
    g = make_group(1, autobidding_mode="max_goals", stats=stats(3000, 30000, 300, 8))
    assert ("learning_starved", 1) in found(make_ds([g], banners(1)))


def test_spend_concentration():
    gs = [make_group(1, stats=stats(8000, 1, 1)), make_group(2, stats=stats(1000, 1, 1)),
          make_group(3, stats=stats(1000, 1, 1))]
    assert ("spend_concentration", 1) in found(make_ds(gs, banners(1) + banners(2) + banners(3)))


def test_scale_leader():
    daily = [day(f"2026-09-{d:02d}", 950, 5000, 50, 3) for d in range(1, 31)]
    g = make_group(1, budget_day_rub=1000.0, daily=daily, stats=stats(28500, 150000, 1500, 90))
    f = found(make_ds([g], banners(1), target_cpa=500))[("scale_leader", 1)]
    assert f["level"] == "green" and f["effect_rub"] == round(950 * 0.5 * 30, 2)
