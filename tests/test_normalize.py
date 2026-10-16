from __future__ import annotations

import pytest

from scripts.normalize import (aggregate, build_dataset, check_sanity, main, money, stats_from,
                               targeting_key)
from scripts.raw_store import write_call

P = {"date_from": "2026-09-01", "date_to": "2026-09-30", "metrics": "base"}


def base(spent="0", shows=0, clicks=0, goals=0, ctr=None, vk_result=0):
    ctr = (clicks / shows * 100) if (ctr is None and shows) else (ctr or 0)   # ctr VK — уже проценты
    return {"base": {"spent": spent, "shows": shows, "clicks": clicks, "goals": goals, "ctr": ctr,
                     "vk": {"result": vk_result}},
            "uniques": {"reach": shows // 2, "frequency": 2.0 if shows else 0}}


def cabinet(raw, groups, banners_by_group=None, summary=None, banner_summary=None, daily=None, banner_daily=None):
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
    if banner_daily is not None:
        write_call(raw, "vk_ads_statistics_day", {"entity": "banners", **P},
                   {"items": [{"id": k, "rows": rows} for k, rows in banner_daily.items()]})


def test_money():
    assert money("1234.50") == 1234.5
    assert money("1 234,5") == 1234.5
    assert money(None) == 0.0 and money("") == 0.0
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
               "budget_limit_day": 500, "issues": [{"code": "NO_MONEY"}],
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
    assert ds["coverage"] == {"path": "B", "banners": True, "summary": True, "stats": True,
                              "pixels": False, "metrika": False, "banner_groups": [1],
                              "users_lists": False, "users_lists_no_access": False}
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
    ds = build_dataset(raw, "2026-09-01", "2026-09-30", path="A")
    g = ds["groups"][0]
    assert [d["date"] for d in g["daily"]] == ["2026-09-01", "2026-09-02"]
    assert g["stats"]["spent"] == 300.0
    assert ds["coverage"]["summary"] is False and ds["coverage"]["path"] == "A"


def test_sanity_catches_ctr_as_fraction(tmp_path):
    """Живой прогон: ctr у VK уже в процентах (0.5176 = 0,52 %); доля 0.01 вместо 1.0 — формат поменялся."""
    raw = tmp_path / "raw"
    cabinet(raw, [{"id": 1, "name": "G1", "status": "active"}],
            summary={1: base(spent="100", shows=10000, clicks=100, ctr=0.01)})
    issues = check_sanity(build_dataset(raw, "2026-09-01", "2026-09-30"))
    assert [i["code"] for i in issues] == ["ctr_units"]


def test_sanity_banners_vs_group(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [{"id": 1, "name": "G1", "status": "active"}],
            banners_by_group={1: [{"id": 11, "status": "active"}]},
            summary={1: base(spent="500", shows=1000, clicks=10)},
            banner_summary={11: base(spent="1000", shows=1000, clicks=10)})
    issues = check_sanity(build_dataset(raw, "2026-09-01", "2026-09-30"))
    assert "banners_vs_group" in [i["code"] for i in issues]


def test_empty_cabinet(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [])
    ds = build_dataset(raw, "2026-09-01", "2026-09-30")
    assert ds["groups"] == [] and ds["totals"]["spent"] == 0.0 and check_sanity(ds) == []


def test_money_rejects_nan_inf_bool():
    for bad in (float("nan"), float("inf"), "nan", "inf", True, False):
        with pytest.raises(ValueError):
            money(bad)


def test_reversed_period_raises(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [])
    with pytest.raises(ValueError):
        build_dataset(raw, "2026-09-30", "2026-09-01")


def test_sanity_catches_ctr_units_in_daily_only(tmp_path):
    """Путь A: только daily — проверка единиц должна работать и по строкам дней."""
    raw = tmp_path / "raw"
    rows = [{"date": "2026-09-01", **base(spent="100", shows=1000, clicks=10, ctr=0.01)}]  # доля вместо процентов
    cabinet(raw, [{"id": 1, "name": "G1", "status": "active"}], daily={1: rows})
    issues = check_sanity(build_dataset(raw, "2026-09-01", "2026-09-30", path="A"))
    assert [(i["code"], i["entity"]) for i in issues] == [("ctr_units", "group:1@2026-09-01")]


def _good_cabinet(raw):
    cabinet(raw, [{"id": 1, "name": "G1", "status": "active"}],
            summary={1: base(spent="100", shows=1000, clicks=10)})


def _ws_run(ws, *extra):
    return main(["--workspace", str(ws), "--date-from", "2026-09-01", "--date-to", "2026-09-30", *extra])


def test_cli_good_cabinet_writes_dataset_only(tmp_path):
    ws = tmp_path / "ws"
    _good_cabinet(ws / "raw")
    assert _ws_run(ws) == 0
    assert (ws / "dataset.json").exists() and not (ws / "sanity.json").exists()


def test_cli_sanity_failure_writes_sanity_only(tmp_path):
    ws = tmp_path / "ws"
    cabinet(ws / "raw", [{"id": 1, "name": "G1", "status": "active"}],
            summary={1: base(spent="100", shows=10000, clicks=100, ctr=0.01)})
    assert _ws_run(ws) == 3
    assert (ws / "sanity.json").exists() and not (ws / "dataset.json").exists()


def test_cli_passing_run_removes_stale_sanity(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "sanity.json").write_text("[]", encoding="utf-8")
    (ws / "dataset.json").write_text("{}", encoding="utf-8")
    _good_cabinet(ws / "raw")
    assert _ws_run(ws) == 0
    assert not (ws / "sanity.json").exists()
    assert (ws / "dataset.json").read_text(encoding="utf-8") != "{}"


def test_cli_malformed_raw_exits_3_and_writes_nothing(tmp_path, capsys):
    ws = tmp_path / "ws"
    raw = ws / "raw"
    write_call(raw, "vk_ads_campaigns_list", {"paginated": True}, {"items": []})
    write_call(raw, "vk_ads_banners_list", {"campaign_id": 1}, [1, 2])  # response — список, а не объект
    (ws / "dataset.json").write_text("{}", encoding="utf-8")  # старый результат должен исчезнуть
    assert _ws_run(ws) == 3
    assert not (ws / "dataset.json").exists() and not (ws / "sanity.json").exists()
    assert "raw/ не разобран" in capsys.readouterr().out


# ---------- итоговая волна исправлений ----------

def _active(gid, **kw):
    return {"id": gid, "name": f"G{gid}", "status": "active", **kw}


def _stat_call(raw, tool, entity, ids, items, **extra):
    args = {"entity": entity, "ids": ",".join(map(str, ids)), **P, **extra}
    write_call(raw, tool, args, {"items": items})


def test_banner_groups_from_calls_and_banner_campaign_id(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1), _active(2), _active(3)], banners_by_group={1: [{"id": 11, "status": "active"}]})
    write_call(raw, "vk_ads_banners_list", {"paginated": True}, {"items": [{"id": 21, "campaign_id": 2}]})
    ds = build_dataset(raw, "2026-09-01", "2026-09-30")
    assert ds["coverage"]["banner_groups"] == [1, 2]
    assert ds["coverage"]["banners"] is True


def test_note_when_banners_missing_for_active_groups(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1), _active(2), _active(3)], banners_by_group={1: [{"id": 11, "status": "active"}]})
    ds = build_dataset(raw, "2026-09-01", "2026-09-30")
    assert "Объявления не собраны для 2 групп — по ним выводы на уровне группы." in ds["notes"]


def test_summary_missing_group_in_ids_is_zero(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1), _active(2)])
    _stat_call(raw, "vk_ads_statistics_summary", "campaigns", [1, 2],
               [{"id": 2, "total": base(spent="100", shows=1000, clicks=10)}])
    g1, g2 = build_dataset(raw, "2026-09-01", "2026-09-30")["groups"]
    assert g1["stats"]["spent"] == 0.0 and not g1.get("stats_missing")
    assert g2["stats"]["spent"] == 100.0


def test_group_not_covered_by_summary_uses_daily(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1), _active(2)])
    _stat_call(raw, "vk_ads_statistics_summary", "campaigns", [1],
               [{"id": 1, "total": base(spent="100", shows=1000, clicks=10)}])
    rows = [{"date": "2026-09-01", **base(spent="70", shows=1000, clicks=10)}]
    _stat_call(raw, "vk_ads_statistics_day", "campaigns", [2], [{"id": 2, "rows": rows}])
    g1, g2 = build_dataset(raw, "2026-09-01", "2026-09-30")["groups"]
    assert g2["stats"]["spent"] == 70.0 and not g2.get("stats_missing")


def test_group_not_covered_anywhere_is_stats_missing(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1), _active(2), {"id": 3, "name": "G3", "status": "deleted"}])
    _stat_call(raw, "vk_ads_statistics_summary", "campaigns", [1],
               [{"id": 1, "total": base(spent="100", shows=1000, clicks=10)}])
    ds = build_dataset(raw, "2026-09-01", "2026-09-30")
    g2 = ds["groups"][1]
    assert g2["stats_missing"] is True and g2["stats"]["spent"] == 0.0
    assert "Статистика не получена для 1 группы — итоги и выводы по ним неполные." in ds["notes"]   # падеж по числу


def test_summary_all_time_does_not_block_audit(tmp_path):
    """statistics_summary — только за всё время: итоги берём из дней, расхождение — не код 3."""
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)])
    _stat_call(raw, "vk_ads_statistics_summary", "campaigns", [1],
               [{"id": 1, "total": base(spent="1000", shows=1000, clicks=10)}])
    rows = [{"date": "2026-09-01", **base(spent="400", shows=1000, clicks=10)}]
    _stat_call(raw, "vk_ads_statistics_day", "campaigns", [1], [{"id": 1, "rows": rows}])
    ds = build_dataset(raw, "2026-09-01", "2026-09-30")
    assert ds["groups"][0]["stats"]["spent"] == 400.0 and ds["totals"]["spent"] == 400.0
    assert check_sanity(ds) == []


def test_identical_duplicate_listing_is_deduped(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], banners_by_group={1: [{"id": 11, "status": "active"}]})
    write_call(raw, "vk_ads_campaigns_list", {"paginated": True}, {"items": [_active(1)]})
    write_call(raw, "vk_ads_banners_list", {"campaign_id": 1}, {"items": [{"id": 11, "status": "active"}]})
    write_call(raw, "vk_ads_remarketing_segments_list", {}, {"items": [{"id": 5, "name": "s"}]})
    write_call(raw, "vk_ads_remarketing_segments_list", {}, {"items": [{"id": 5, "name": "s"}]})
    ds = build_dataset(raw, "2026-09-01", "2026-09-30")
    assert len(ds["groups"]) == 1 and len(ds["banners"]) == 1 and len(ds["segments"]) == 1
    assert ds["groups"][0]["banner_ids"] == [11]


def test_duplicate_statistics_items_deduped(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)])
    item = {"id": 1, "total": base(spent="100", shows=1000, clicks=10)}
    _stat_call(raw, "vk_ads_statistics_summary", "campaigns", [1], [item])
    _stat_call(raw, "vk_ads_statistics_summary", "campaigns", [1], [item])
    assert build_dataset(raw, "2026-09-01", "2026-09-30")["totals"]["spent"] == 100.0


@pytest.mark.parametrize("tool,items", [
    ("vk_ads_campaigns_list", [_active(1), _active(1, name="другое")]),
    ("vk_ads_ad_plans_list", [{"id": 7, "name": "a"}, {"id": 7, "name": "b"}]),
    ("vk_ads_remarketing_pixels_list", [{"id": 7, "name": "a"}, {"id": 7, "name": "b"}]),
])
def test_conflicting_duplicates_raise(tmp_path, tool, items):
    raw = tmp_path / "raw"
    cabinet(raw, [])
    write_call(raw, tool, {"paginated": True}, {"items": items})
    with pytest.raises(ValueError, match=tool):
        build_dataset(raw, "2026-09-01", "2026-09-30")


def test_conflicting_banner_duplicates_raise(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], banners_by_group={1: [{"id": 11, "status": "active"}]})
    write_call(raw, "vk_ads_banners_list", {"campaign_id": 1}, {"items": [{"id": 11, "status": "blocked"}]})
    with pytest.raises(ValueError, match="vk_ads_banners_list.*11"):
        build_dataset(raw, "2026-09-01", "2026-09-30")


def test_conflicting_statistics_duplicates_raise(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)])
    _stat_call(raw, "vk_ads_statistics_summary", "campaigns", [1],
               [{"id": 1, "total": base(spent="100", shows=1000, clicks=10)}])
    _stat_call(raw, "vk_ads_statistics_summary", "campaigns", [1],
               [{"id": 1, "total": base(spent="200", shows=1000, clicks=10)}])
    with pytest.raises(ValueError, match="vk_ads_statistics_summary.*1"):
        build_dataset(raw, "2026-09-01", "2026-09-30")


def test_cli_conflicting_duplicates_exit_3(tmp_path, capsys):
    ws = tmp_path / "ws"
    cabinet(ws / "raw", [_active(1)])
    write_call(ws / "raw", "vk_ads_campaigns_list", {}, {"items": [_active(1, name="x")]})
    assert _ws_run(ws) == 3
    assert "raw/ не разобран" in capsys.readouterr().out


def test_sanity_banners_below_group_is_note_not_issue(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], banners_by_group={1: [{"id": 11, "status": "active"}]},
            summary={1: base(spent="1000", shows=1000, clicks=10)},
            banner_summary={11: base(spent="500", shows=1000, clicks=10)})
    ds = build_dataset(raw, "2026-09-01", "2026-09-30")
    assert check_sanity(ds) == []
    assert any("Сумма по объявлениям меньше расхода группы 1" in n for n in ds["notes"])


def test_sanity_banners_above_group_still_flagged(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], banners_by_group={1: [{"id": 11, "status": "active"}]},
            summary={1: base(spent="500", shows=1000, clicks=10)},
            banner_summary={11: base(spent="1000", shows=1000, clicks=10)})
    assert "banners_vs_group" in [i["code"] for i in check_sanity(build_dataset(raw, "2026-09-01", "2026-09-30"))]


def test_statistics_period_mismatch_raises(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)])
    write_call(raw, "vk_ads_statistics_summary",
               {"entity": "campaigns", "ids": "1", "date_from": "2026-08-01", "date_to": "2026-08-31"},
               {"items": []})
    with pytest.raises(ValueError, match="период raw/"):
        build_dataset(raw, "2026-09-01", "2026-09-30")


def test_missing_vk_ctr_is_none_not_zero():
    block = {"base": {"spent": "100", "shows": 10000, "clicks": 100, "goals": 0}}
    assert stats_from(block, None)["vk_ctr_pct"] is None


def test_null_names_become_empty(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [{"id": 1, "name": None, "status": "active"}],
            banners_by_group={1: [{"id": 11, "name": None, "status": "active"}]})
    write_call(raw, "vk_ads_remarketing_segments_list", {}, {"items": [{"id": 5, "name": None}]})
    ds = build_dataset(raw, "2026-09-01", "2026-09-30")
    assert ds["groups"][0]["name"] == "" and ds["banners"][0]["name"] == "" and ds["segments"][0]["name"] == ""


def test_v1_fields(tmp_path):
    raw = tmp_path / "raw"
    groups = [{"id": 1, "name": "G1", "status": "active", "objective": "site_conversions", "ad_plan_id": 7,
               "max_price": "50.00", "enable_utm": True,
               "targetings": {"geo": {"regions": [1]}, "segments": [102], "keywords": ["велосипед"]}},
              {"id": 2, "name": "G2", "status": "active", "objective": "site_conversions", "ad_plan_id": 7,
               "targetings": {"geo": {"regions": [1]}}}]
    cabinet(raw, groups, banners_by_group={1: [{"id": 11, "status": "active", "created": "2026-09-20 10:00:00",
                                                "call_to_action": "sign_up", "ord_marker": "Kra23",
                                                "textblocks": {"text_90": {"text": "Запишитесь"}},
                                                "urls": {"primary": {"id": 3, "url": "https://s.ru/?utm_source=vk"}},
                                                "content": {"image_1080x1080": {"id": 50}}}]},
            summary={1: base(spent="100", shows=1000, clicks=10)})
    write_call(raw, "vk_ads_remarketing_segments_list", {"paginated": True},
               {"items": [{"id": 102, "name": "LAL клиенты", "size": 40}]})
    write_call(raw, "vk_ads_users_lists_list", {"paginated": True},
               {"items": [{"id": 5, "name": "Клиенты", "entries_count": 900, "updated": "2026-05-01 00:00:00"}]})
    ds = build_dataset(raw, "2026-09-01", "2026-09-30", as_of="2026-10-01T10:00")
    g1, g2 = ds["groups"]
    assert g1["layer"] == "lal" and g2["layer"] == "cold"
    assert g1["features"]["has_keywords"] is True and g2["features"]["has_keywords"] is False
    assert g1["max_price_rub"] == 50.0 and g2["max_price_rub"] is None
    assert g1["auto_utm"] is True and g2["auto_utm"] is None   # нет ключа enable_utm — не подтверждено (2-й прогон)
    b = ds["banners"][0]
    assert b["cta"] == "sign_up" and b["text"] == "Запишитесь" and b["erid"] == "Kra23"
    assert b["urls"] == ["https://s.ru/?utm_source=vk"] and b["ratios"] == ["1:1"] and b["content_ids"] == [50]
    assert b["age_days"] == 10
    assert ds["segments"][0]["lal"] is True and ds["segments"][0]["size"] == 40
    assert ds["users_lists"][0] == {"id": 5, "name": "Клиенты", "size": 900, "updated": "2026-05-01 00:00:00"}
    assert ds["as_of"] == "2026-10-01T10:00"


def test_erid_unknown_when_field_absent(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [{"id": 1, "name": "G1", "status": "active"}],
            banners_by_group={1: [{"id": 11, "status": "active"}]}, summary={})
    assert build_dataset(raw, "2026-09-01", "2026-09-30")["banners"][0]["erid"] is None


def test_plan_budget_known(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [])
    write_call(raw, "vk_ads_ad_plans_list", {"paginated": True},
               {"items": [{"id": 8, "name": "P", "budget_limit_day": 3000}, {"id": 9, "name": "Q"}]})
    plans = {p["id"]: p for p in build_dataset(raw, "2026-09-01", "2026-09-30")["plans"]}
    assert plans[8]["has_budget"] is True and plans[8]["budget_day_rub"] == 3000.0
    assert plans[9]["has_budget"] is False


def test_windows(tmp_path):
    raw = tmp_path / "raw"
    rows = [{"date": f"2026-09-{d:02d}", **base(spent="100", shows=1000, clicks=10, goals=1)} for d in range(1, 31)]
    cabinet(raw, [{"id": 1, "name": "G1", "status": "active"}], daily={1: rows})
    g = build_dataset(raw, "2026-09-01", "2026-09-30")["groups"][0]
    assert g["windows"]["7"]["spent"] == 700.0 and g["windows"]["30"]["spent"] == 3000.0


def test_windows_short_period(tmp_path):
    raw = tmp_path / "raw"
    P10 = {"date_from": "2026-09-21", "date_to": "2026-09-30", "metrics": "all"}
    write_call(raw, "vk_ads_campaigns_list", {"paginated": True}, {"items": [{"id": 1, "name": "G", "status": "active"}]})
    write_call(raw, "vk_ads_statistics_day", {"entity": "campaigns", **P10},
               {"items": [{"id": 1, "rows": [{"date": f"2026-09-{d}", **base(spent="10", shows=100, clicks=1)}
                                             for d in range(21, 31)]}]})
    g = build_dataset(raw, "2026-09-21", "2026-09-30")["groups"][0]
    assert g["windows"]["7"] is not None and g["windows"]["14"] is None and g["windows"]["30"] is None


def test_excluded_segments_are_not_targeting():
    from scripts.normalize import _segment_ids
    assert _segment_ids({"segments": [1], "segments_exclude": [2]}) == [1]


def test_windows_empty_are_none(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [{"id": 1, "name": "G1", "status": "active"}], summary={1: base(spent="100", shows=1000, clicks=10)})
    ds = build_dataset(raw, "2026-09-01", "2026-09-30")
    assert ds["groups"][0]["windows"] == {"7": None, "14": None, "30": None}
    assert ds["totals_windows"] == {"7": None, "14": None, "30": None}


def test_windows_partial_rows(tmp_path):
    raw = tmp_path / "raw"
    rows = [{"date": f"2026-09-{d:02d}", **base(spent="100", shows=1000, clicks=10)} for d in range(1, 11)]
    cabinet(raw, [{"id": 1, "name": "G1", "status": "active"}], daily={1: rows})
    ds = build_dataset(raw, "2026-09-01", "2026-09-30")
    assert ds["groups"][0]["windows"]["7"] is None and ds["groups"][0]["windows"]["30"]["spent"] == 1000.0
    assert ds["totals_windows"]["7"] is None and ds["totals_windows"]["30"] is not None


def test_non_numeric_max_price_is_none():
    from scripts.normalize import _price
    assert _price("abc") is None and _price("12,5") == 12.5


def test_layer_unknown_without_targetings_key(tmp_path):
    raw = tmp_path / "raw"
    groups = [{"id": 1, "name": "G1", "status": "active", "objective": "site_conversions", "ad_plan_id": 7},
              {"id": 2, "name": "G2", "status": "active", "objective": "site_conversions", "ad_plan_id": 7,
               "targetings": {"geo": {"regions": [1]}}}]
    cabinet(raw, groups)
    ds = build_dataset(raw, "2026-09-01", "2026-09-30", as_of="2026-10-01T10:00")
    g1, g2 = ds["groups"]
    assert g1["layer"] is None and g2["layer"] == "cold"
