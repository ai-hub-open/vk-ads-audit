"""Первый живой прогон (16.10.2026): итоги по дням, проценты, рубли, нет прав, формы данных живого кабинета."""
from __future__ import annotations

import pytest

from scripts import normalize
from scripts.checks import run_checks
from scripts.diagnose import diagnose, load_thresholds
from scripts.features import content_features
from scripts.normalize import build_dataset, check_sanity, stats_from
from scripts.raw_store import write_call
from scripts.render_report import limitations
from tests.helpers import make_banner, make_ds, make_group, stats
from tests.test_normalize import _active, _stat_call, base, cabinet

TH = load_thresholds()
D = ("2026-09-01", "2026-09-30")


def res(ds):
    return diagnose(ds, TH)


def found(ds, rule):
    return [f for f in res(ds)["findings"] if f["rule"] == rule]


def checklist(ds):
    r = res(ds)
    return run_checks(ds, TH, r["findings"], r["evaluated"])


def status(cl, cid):
    return next(c for b in cl["blocks"] for c in b["checks"] if c["id"] == cid)


def day_rows(n, spent="100", shows=1000, clicks=10, goals=1):
    return [{"date": f"2026-09-{d:02d}", **base(spent=spent, shows=shows, clicks=clicks, goals=goals)}
            for d in range(1, n + 1)]


# ---- 2. итоги периода — сумма дней, а не statistics_summary (она отдаёт всё время) ----

def test_group_totals_are_sum_of_daily_rows_not_all_time_summary(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)])
    _stat_call(raw, "vk_ads_statistics_summary", "campaigns", [1],
               [{"id": 1, "total": base(spent="99999", shows=999999, clicks=9999)}])   # всё время
    _stat_call(raw, "vk_ads_statistics_day", "campaigns", [1], [{"id": 1, "rows": day_rows(3)}])
    ds = build_dataset(raw, *D)
    assert ds["groups"][0]["stats"]["spent"] == 300.0 and ds["totals"]["spent"] == 300.0
    assert ds["groups"][0]["stats"]["conversions"] == 3 and ds["groups"][0]["stats"]["clicks"] == 30
    assert check_sanity(ds) == []


def test_banner_totals_from_daily_banners(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], banners_by_group={1: [{"id": 11, "status": "active"}, {"id": 12, "status": "active"}]},
            daily={1: day_rows(2)}, banner_daily={11: day_rows(2, spent="60"), 12: day_rows(2, spent="40")})
    ds = build_dataset(raw, *D)
    by = {b["id"]: b for b in ds["banners"]}
    assert by[11]["stats"]["spent"] == 120.0 and by[12]["stats"]["spent"] == 80.0
    assert check_sanity(ds) == []


def test_banner_without_daily_or_summary_has_no_stats(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], banners_by_group={1: [{"id": 11, "status": "active"}]},
            daily={1: day_rows(2)}, banner_daily={})
    assert build_dataset(raw, *D)["banners"][0]["stats"] is None


def test_old_raw_with_banner_summary_still_works(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], banners_by_group={1: [{"id": 11, "status": "active"}]},
            summary={1: base(spent="500", shows=5000, clicks=50)},
            banner_summary={11: base(spent="500", shows=5000, clicks=50)})
    ds = build_dataset(raw, *D)
    assert ds["groups"][0]["stats"]["spent"] == 500.0 and ds["banners"][0]["stats"]["spent"] == 500.0


def _freq_cabinet(raw, created, summary_shows):
    cabinet(raw, [_active(1, created=created)])
    _stat_call(raw, "vk_ads_statistics_summary", "campaigns", [1],
               [{"id": 1, "total": {**base(spent="300", shows=summary_shows, clicks=30),
                                    "uniques": {"reach": 1500, "frequency": 2.0}}}])
    _stat_call(raw, "vk_ads_statistics_day", "campaigns", [1], [{"id": 1, "rows": day_rows(3)}])
    return build_dataset(raw, *D)


def test_frequency_from_summary_only_when_group_lifetime_fits_period(tmp_path):
    ds = _freq_cabinet(tmp_path / "a", "2026-09-01 10:00:00", 3000)
    assert ds["groups"][0]["stats"]["frequency"] == 2.0 and ds["groups"][0]["stats"]["reach"] == 1500
    assert not any("Частота и охват" in n for n in ds["notes"])


def test_frequency_unknown_when_group_older_than_period(tmp_path):
    ds = _freq_cabinet(tmp_path / "b", "2026-08-01 10:00:00", 3000)
    assert ds["groups"][0]["stats"]["frequency"] is None
    assert any("Частота и охват" in n and "1 групп" in n for n in ds["notes"])


def test_frequency_unknown_when_summary_does_not_match_period_shows(tmp_path):
    ds = _freq_cabinet(tmp_path / "c", "2026-09-01 10:00:00", 90000)   # в сводке больше показов: она за всё время
    assert ds["groups"][0]["stats"]["frequency"] is None


def test_frequency_unknown_without_summary(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1, created="2026-09-02 00:00:00")], daily={1: day_rows(3)})
    ds = build_dataset(raw, *D, path="A")
    assert ds["groups"][0]["stats"]["frequency"] is None


def test_burnout_silent_and_f3_unknown_without_frequency():
    g = make_group(1, stats={**stats(900, 20000, 100, 1), "frequency": None},
                   daily=[{"date": f"2026-09-{d:02d}", **stats(30, 700, 3, 0)} for d in range(1, 31)])
    ds = make_ds([g])
    assert found(ds, "burnout") == []
    c = status(checklist(ds), "F3")
    assert c["status"] == "unknown" and "частот" in c["reason"]


def test_f3_still_checked_when_frequency_known():
    g = make_group(1, stats=stats(900, 20000, 100, 1, frequency=1.5),
                   daily=[{"date": f"2026-09-{d:02d}", **stats(30, 700, 3, 0)} for d in range(1, 31)])
    assert status(checklist(make_ds([g])), "F3")["status"] == "pass"


# ---- 3. ctr / cr у VK уже проценты ----

def test_vk_ctr_is_percent_as_is():
    block = {"base": {"spent": "100", "shows": 117469, "clicks": 608, "goals": 0, "ctr": 0.5176}}
    s = stats_from(block, None)
    assert s["vk_ctr_pct"] == 0.5176 and s["ctr_pct"] == pytest.approx(0.5176, abs=1e-3)


def test_sanity_does_not_fire_on_live_ctr(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)])
    row = {"date": "2026-09-01", "base": {"spent": "100", "shows": 117469, "clicks": 608, "goals": 0,
                                           "ctr": 0.5176, "cr": 0.0}}
    _stat_call(raw, "vk_ads_statistics_day", "campaigns", [1], [{"id": 1, "rows": [row]}])
    assert check_sanity(build_dataset(raw, *D)) == []


# ---- 4. бюджеты в рублях ----

def test_budgets_are_rubles(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1, budget_limit_day=50000, budget_limit=1200000), _active(2, budget_limit_day=500)])
    g1, g2 = build_dataset(raw, *D)["groups"]
    assert g1["budget_day_rub"] == 50000.0 and g1["budget_total_rub"] == 1200000.0
    assert g2["budget_day_rub"] == 500.0 and g2["budget_total_rub"] is None


def test_kopecks_helper_removed():
    assert not hasattr(normalize, "kopecks_to_rub")


def test_low_budget_threshold_on_rubles():
    ok = make_group(1, budget_day_rub=500.0)
    low = make_group(2, budget_day_rub=200.0)
    got = {f["entity"]["id"] for f in found(make_ds([ok, low]), "low_budget") if f["level"] == "orange"}
    assert got == {2}


# ---- 9. нет прав на базы клиентов ----

def test_no_access_to_users_lists_marks_c14_unknown_with_reason(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], daily={1: day_rows(2)})
    log = {"calls": [{"tool": "vk_ads_users_lists_list", "arguments": {}, "ok": False,
                      "error": "vk_ads_users_lists_list: нет доступа", "no_access": True}]}
    ds = build_dataset(raw, *D, collect_log=log)
    assert ds["coverage"]["users_lists"] is False and ds["coverage"]["users_lists_no_access"] is True
    assert any("нет доступа к базам клиентов" in n.lower() for n in ds["notes"])
    c14 = status(checklist(ds), "C14")
    assert c14["status"] == "unknown" and "нет прав" in c14["reason"]
    assert found(ds, "stale_list") == []


def test_users_lists_not_collected_is_unknown_not_na(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], daily={1: day_rows(2)})
    ds = build_dataset(raw, *D)
    assert status(checklist(ds), "C14")["status"] == "unknown"


def test_empty_users_lists_collected_is_na(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], daily={1: day_rows(2)})
    write_call(raw, "vk_ads_users_lists_list", {"paginated": True}, {"items": []})
    assert status(checklist(build_dataset(raw, *D)), "C14")["status"] == "na"


def test_no_access_line_in_report_limitations(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], daily={1: day_rows(2)})
    log = {"calls": [{"tool": "vk_ads_users_lists_list", "arguments": {}, "ok": False,
                      "error": "x", "no_access": True}]}
    ds = build_dataset(raw, *D, collect_log=log)
    lines = limitations(ds, log, checklist(ds))
    assert any("нет доступа к базам клиентов" in x.lower() for x in lines)
    assert not any(x.startswith("Не удалось получить vk_ads_users_lists_list") for x in lines)


# ---- 10. сегменты без size и relations (живая форма) ----

def test_segments_without_size_and_relations(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1, targetings={"geo": {"regions": [1]}, "segments": [5]})], daily={1: day_rows(2)})
    write_call(raw, "vk_ads_remarketing_segments_list", {"paginated": True},
               {"items": [{"id": 5, "name": "LAL клиенты", "pass_condition": 1, "flags": [],
                           "created": "2026-01-01 00:00:00", "updated": "2026-02-01 00:00:00"}]})
    write_call(raw, "vk_ads_users_lists_list", {"paginated": True},
               {"items": [{"id": 9, "name": "Клиенты", "updated": "2025-01-01 00:00:00"}]})
    ds = build_dataset(raw, *D)
    assert any("не возвращает размер сегментов" in n for n in ds["notes"])
    cl = checklist(ds)
    assert status(cl, "C13")["status"] == "unknown" and status(cl, "C14")["status"] == "unknown"
    assert found(ds, "lal_small_source") == []
    assert {f["confidence"] for f in found(ds, "stale_list")} <= {"low"}    # связь база → сегмент неизвестна


def test_no_segment_note_when_size_is_present(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)])
    write_call(raw, "vk_ads_remarketing_segments_list", {}, {"items": [{"id": 5, "name": "s", "size": 10}]})
    assert not any("размер сегментов" in n for n in build_dataset(raw, *D)["notes"])


# ---- 11. живые формы ----

def test_null_call_to_action_is_unknown_for_e7(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], daily={1: day_rows(2)},
            banners_by_group={1: [{"id": 11, "status": "active", "call_to_action": None},
                                  {"id": 12, "status": "active", "call_to_action": None}]})
    ds = build_dataset(raw, *D)
    assert all(b["cta"] is None for b in ds["banners"])
    assert found(ds, "generic_button") == []
    assert status(checklist(ds), "E7")["status"] == "unknown"


def test_content_role_without_ratio_is_unknown_not_missing():
    f = content_features({"video_landscape_180s": {"id": 1}})
    assert f["has_video"] is True and f["ratios"] == [] and f["ratios_unknown"] is True
    assert content_features({"image_1080x1080": {"id": 1}})["ratios_unknown"] is False
    assert content_features({"icon_256x256": {"id": 1}})["ratios_unknown"] is False


def test_missing_ratios_silent_on_role_without_ratio():
    bs = [make_banner(11, 1, ratios=["1:1"], content_ids=[1]),
          make_banner(12, 1, has_video=True, content_ids=[2], ratios_unknown=True)]
    ds = make_ds([make_group(1)], bs)
    assert found(ds, "missing_ratios") == []
    assert status(checklist(ds), "E1")["status"] == "unknown"


def test_e1_unknown_for_live_video_only_cabinet(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], daily={1: day_rows(2)},
            banners_by_group={1: [{"id": 11, "status": "active", "content": {"video_landscape_180s": {"id": 5}}}]})
    ds = build_dataset(raw, *D)
    assert ds["banners"][0]["ratios_unknown"] is True
    assert found(ds, "missing_ratios") == []
    assert status(checklist(ds), "E1")["status"] == "unknown"


def test_e1_still_fails_for_known_groups_even_with_other_unknown():
    bs = [make_banner(11, 1, ratios=["1:1"], content_ids=[1]),
          make_banner(21, 2, has_video=True, content_ids=[2], ratios_unknown=True)]
    ds = make_ds([make_group(1), make_group(2)], bs)
    assert [f["entity"]["id"] for f in found(ds, "missing_ratios")] == [1]
    assert status(checklist(ds), "E1")["status"] == "fail"


def test_e5_ignores_banners_with_unknown_ratio():
    bs = [make_banner(11, 1, has_video=True, content_ids=[1], ratios_unknown=True),
          make_banner(12, 1, has_video=True, content_ids=[2], ratios_unknown=True)]
    assert status(checklist(make_ds([make_group(1)], bs)), "E5")["status"] == "unknown"


def test_archived_group_is_not_live(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1, budget_limit_day=100, issues=[{"code": "ARCHIVED"}]),
                  _active(2, budget_limit_day=100)], daily={1: day_rows(2), 2: day_rows(2)},
            banners_by_group={2: [{"id": 21, "status": "active"}, {"id": 22, "status": "active"}]})
    ds = build_dataset(raw, *D)
    assert ds["groups"][0]["issues"] == ["ARCHIVED"]
    low = {f["entity"]["id"] for f in found(ds, "low_budget")}
    assert low == {2}                                                  # у архивной группы 100 ₽ не жалоба
    assert 1 not in {f["entity"]["id"] for f in res(ds)["findings"]}
    assert ds["totals"]["spent"] == 400.0                              # расход архивной группы в итогах остаётся
