from __future__ import annotations

import pytest

from scripts.normalize import (aggregate, build_dataset, check_sanity, kopecks_to_rub, money,
                               stats_from, targeting_key)
from scripts.raw_store import write_call

P = {"date_from": "2026-09-01", "date_to": "2026-09-30", "metrics": "all"}


def base(spent="0", shows=0, clicks=0, goals=0, ctr=None, vk_result=0):
    ctr = (clicks / shows) if (ctr is None and shows) else (ctr or 0)
    return {"base": {"spent": spent, "shows": shows, "clicks": clicks, "goals": goals, "ctr": ctr,
                     "vk": {"result": vk_result}},
            "uniques": {"reach": shows // 2, "frequency": 2.0 if shows else 0}}


def cabinet(raw, groups, banners_by_group=None, summary=None, banner_summary=None, daily=None):
    write_call(raw, "vk_ads_ad_plans_list", {"paginated": True}, {"items": [{"id": 7, "name": "План"}]})
    write_call(raw, "vk_ads_campaigns_list", {"paginated": True}, {"items": groups})
    for gid, banners in (banners_by_group or {}).items():
        write_call(raw, "vk_ads_banners_list", {"campaign_id": gid, "paginated": True}, {"items": banners})
    if summary is not None:
        write_call(raw, "vk_ads_statistics_summary", {"entity": "campaigns", **P},
                   {"items": [{"id": k, "total": v} for k, v in summary.items()]})
    if banner_summary is not None:
        write_call(raw, "vk_ads_statistics_summary", {"entity": "banners", **P},
                   {"items": [{"id": k, "total": v} for k, v in banner_summary.items()]})
    if daily is not None:
        write_call(raw, "vk_ads_statistics_day", {"entity": "campaigns", **P},
                   {"items": [{"id": k, "rows": rows} for k, rows in daily.items()]})


def test_money_and_kopecks():
    assert money("1234.50") == 1234.5
    assert money("1 234,5") == 1234.5
    assert money(None) == 0.0 and money("") == 0.0
    assert kopecks_to_rub(6000000) == 60000.0
    assert kopecks_to_rub(None) is None
    with pytest.raises(ValueError):
        money("abc")


def test_stats_ratios():
    s = stats_from(base(spent="1000", shows=10000, clicks=100, goals=4), "site_conversions")
    assert s["ctr_pct"] == 1.0 and s["cpc"] == 10.0 and s["cpm"] == 100.0 and s["cpa"] == 250.0
    assert s["conversions"] == 4 and s["vk_ctr_pct"] == pytest.approx(1.0)


def test_conversion_source_by_objective():
    block = base(spent="100", shows=100, clicks=10, goals=0, vk_result=5)
    assert stats_from(block, "leadads")["conversions"] == 5
    assert stats_from(block, "site_conversions")["conversions"] == 0


def test_aggregate_daily():
    rows = [stats_from(base(spent="100", shows=1000, clicks=10, goals=1), None),
            stats_from(base(spent="300", shows=1000, clicks=30, goals=1), None)]
    a = aggregate(rows)
    assert a["spent"] == 400.0 and a["clicks"] == 40 and a["cpa"] == 200.0 and a["ctr_pct"] == 2.0


def test_targeting_key_order_insensitive():
    a = {"geo": {"regions": [2, 1]}, "sex": ["male", "female"], "fulltime": {"mon": [1]}}
    b = {"sex": ["female", "male"], "geo": {"regions": [1, 2]}}
    assert targeting_key(a) == targeting_key(b)


def test_build_dataset(tmp_path):
    raw = tmp_path / "raw"
    groups = [{"id": 1, "name": "G1", "status": "active", "objective": "site_conversions",
               "budget_limit_day": 50000, "issues": [{"code": "NO_MONEY"}],
               "targetings": {"geo": {"regions": [1]}, "segments": [55]}},
              {"id": 2, "name": "G2", "status": "deleted", "objective": "site_conversions"}]
    cabinet(raw, groups, banners_by_group={1: [{"id": 11, "status": "active"}]},
            summary={1: base(spent="900", shows=9000, clicks=90, goals=3)},
            banner_summary={11: base(spent="900", shows=9000, clicks=90, goals=3)})
    ds = build_dataset(raw, "2026-09-01", "2026-09-30", target_cpa=250)
    g = ds["groups"][0]
    assert ds["period"]["days"] == 30
    assert g["budget_day_rub"] == 500.0 and g["issues"] == ["NO_MONEY"] and g["segment_ids"] == [55]
    assert g["banner_ids"] == [11] and ds["banners"][0]["group_id"] == 1
    assert ds["totals"]["spent"] == 900.0
    assert ds["coverage"] == {"path": "B", "banners": True, "summary": True, "metrika": False}
    assert check_sanity(ds) == []


def test_missing_stats_are_zero(tmp_path):
    """Сущности без показов VK не кладёт в items — это нули, а не ошибка."""
    raw = tmp_path / "raw"
    cabinet(raw, [{"id": 1, "name": "G1", "status": "active"}, {"id": 2, "name": "G2", "status": "active"}],
            summary={2: base(spent="100", shows=1000, clicks=10)})
    g1, g2 = build_dataset(raw, "2026-09-01", "2026-09-30")["groups"]
    assert g1["stats"]["spent"] == 0.0 and g1["stats"]["cpa"] is None
    assert g2["stats"]["spent"] == 100.0


def test_daily_used_when_no_summary(tmp_path):
    raw = tmp_path / "raw"
    rows = [{"date": "2026-09-01", **base(spent="100", shows=1000, clicks=10, goals=1)},
            {"date": "2026-09-02", **base(spent="200", shows=1000, clicks=20, goals=1)}]
    cabinet(raw, [{"id": 1, "name": "G1", "status": "active"}], daily={1: rows})
    ds = build_dataset(raw, "2026-09-01", "2026-09-02", path="A")
    g = ds["groups"][0]
    assert [d["date"] for d in g["daily"]] == ["2026-09-01", "2026-09-02"]
    assert g["stats"]["spent"] == 300.0
    assert ds["coverage"]["summary"] is False and ds["coverage"]["path"] == "A"


def test_sanity_catches_ctr_in_percent(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [{"id": 1, "name": "G1", "status": "active"}],
            summary={1: base(spent="100", shows=10000, clicks=100, ctr=1.0)})  # 1.0 = «процент», а не доля
    issues = check_sanity(build_dataset(raw, "2026-09-01", "2026-09-30"))
    assert [i["code"] for i in issues] == ["ctr_units"]


def test_sanity_banners_vs_group(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [{"id": 1, "name": "G1", "status": "active"}],
            banners_by_group={1: [{"id": 11, "status": "active"}]},
            summary={1: base(spent="1000", shows=1000, clicks=10)},
            banner_summary={11: base(spent="500", shows=1000, clicks=10)})
    issues = check_sanity(build_dataset(raw, "2026-09-01", "2026-09-30"))
    assert "banners_vs_group" in [i["code"] for i in issues]


def test_empty_cabinet(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [])
    ds = build_dataset(raw, "2026-09-01", "2026-09-30")
    assert ds["groups"] == [] and ds["totals"]["spent"] == 0.0 and check_sanity(ds) == []
