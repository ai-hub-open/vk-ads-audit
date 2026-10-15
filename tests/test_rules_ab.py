from __future__ import annotations

from scripts.diagnose import diagnose, load_thresholds
from tests.helpers import make_banner, make_ds, make_group, stats

TH = load_thresholds()


def found(ds, rule):
    return [f for f in diagnose(ds, TH)["findings"] if f["rule"] == rule]


def test_ord_missing_only_when_field_known():
    known = make_ds([make_group(1)], [make_banner(11, 1, erid=""), make_banner(12, 1, erid="Kra")])
    assert found(known, "ord_missing")[0]["evidence"]["banners_without_erid"] == 1
    assert found(make_ds([make_group(1)], [make_banner(11, 1)]), "ord_missing") == []


def test_utm_missing():
    bs = [make_banner(11, 1, urls=["https://site.ru/"]), make_banner(12, 1, urls=["https://site.ru/?utm_source=vk"]),
          make_banner(13, 1, urls=["https://vk.com/club1"])]
    f = found(make_ds([make_group(1)], bs), "utm_missing")[0]
    assert f["evidence"]["without_utm"] == 1 and f["evidence"]["site_banners"] == 2


def test_utm_auto_on_group_is_ok():
    g = make_group(1, auto_utm=True)
    assert found(make_ds([g], [make_banner(11, 1, urls=["https://site.ru/"])]), "utm_missing") == []


def test_no_retargeting_and_layers():
    ds = make_ds([make_group(1, layer="cold"), make_group(2, layer="cold")])
    assert len(found(ds, "no_retargeting")) == 1
    assert found(ds, "layer_missing")[0]["evidence"]["missing_layers"] == ["lal"]
    full = make_ds([make_group(1, layer="warm"), make_group(2, layer="lal"), make_group(3, layer="cold")])
    assert found(full, "no_retargeting") == [] and found(full, "layer_missing") == []


def test_layer_share_big_budget():
    gs = [make_group(1, layer="warm", stats=stats(30000, 1000, 10)), make_group(2, layer="lal", stats=stats(5000, 1000, 10)),
          make_group(3, layer="cold", stats=stats(5000, 1000, 10))]
    f = found(make_ds(gs), "layer_share")[0]
    assert f["evidence"]["actual"]["warm"] == 0.75


def test_layer_share_small_budget_70_30():
    gs = [make_group(1, layer="warm", stats=stats(7000, 1000, 10)), make_group(3, layer="cold", stats=stats(3000, 1000, 10))]
    assert found(make_ds(gs), "layer_share") == []        # 10 000 ₽ за 30 дней — малый бюджет, 70/30 соблюдено


def test_layer_share_zero_spend():
    assert found(make_ds([make_group(1, layer="warm")]), "layer_share") == []


def test_mixed_plan():
    ds = make_ds([make_group(1, layer="warm", ad_plan_id=7), make_group(2, layer="cold", ad_plan_id=7)])
    assert found(ds, "mixed_plan")[0]["evidence"]["layers"] == ["cold", "warm"]


def test_fragmentation():
    gs = [make_group(i, budget_day_rub=150.0) for i in range(1, 11)]
    assert len(found(make_ds(gs), "fragmentation")) == 1
    assert found(make_ds(gs[:9]), "fragmentation") == []


def test_no_cbo_only_when_plan_budget_known():
    plans = [{"id": 7, "name": "P", "status": "active", "budget_day_rub": None, "budget_total_rub": None, "has_budget": False}]
    ds = make_ds([make_group(1), make_group(2)], plans=plans)
    assert len(found(ds, "no_cbo")) == 1
    assert found(make_ds([make_group(1), make_group(2)]), "no_cbo") == []     # has_budget None — неизвестно


def test_naming():
    ds = make_ds([make_group(1, name="Группа 1"), make_group(2, name="Группа объявлений 2")],
                 [make_banner(11, 1, name="Объявление 1"), make_banner(12, 2, name="ретарг_Мск_Ж25-45")])
    f = found(ds, "naming")[0]
    assert f["evidence"]["default_names"] == 3
    good = make_ds([make_group(1, name="ретарг_Мск_Ж25-45_моб")], [make_banner(11, 1, name="видео_9x16_оффер")],
                   plans=[{"id": 7, "name": "ретаргет_Мск", "status": "active", "budget_day_rub": None,
                           "budget_total_rub": None, "has_budget": None}])
    assert found(good, "naming") == []
