"""Финальное ревью методики v1: ложные ❌/✅ — тесты исправлений."""
from __future__ import annotations

from scripts.checks import run_checks
from scripts.collect import collect
from scripts.diagnose import diagnose, load_thresholds
from scripts.mcp_client import McpError
from scripts.normalize import build_dataset
from scripts.raw_store import write_call
from scripts.render_report import limitations
from tests.helpers import make_banner, make_ds, make_group, stats
from tests.test_collect import FakeClient, RejectsExtendedFields, groups
from tests.test_normalize import cabinet

TH = load_thresholds()


def res(ds):
    return diagnose(ds, TH)


def found(ds, rule):
    return [f for f in res(ds)["findings"] if f["rule"] == rule]


def checklist(ds):
    r = res(ds)
    return run_checks(ds, TH, r["findings"], r["evaluated"])


def status(cl, cid):
    return next(c for b in cl["blocks"] for c in b["checks"] if c["id"] == cid)["status"]


def reason(cl, cid):
    return next(c for b in cl["blocks"] for c in b["checks"] if c["id"] == cid)["reason"]


BANNERS = [make_banner(11, 1), make_banner(12, 1)]


# ---- 1. C14 / unused_segments / stale_list ----

def test_normalize_collects_excluded_segment_ids(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [{"id": 1, "name": "G", "status": "active",
                   "targetings": {"segments": [1], "segments_exclude": [2]}}])
    g = build_dataset(raw, "2026-09-01", "2026-09-30")["groups"][0]
    assert g["segment_ids"] == [1] and g["excluded_segment_ids"] == [2]


def test_exclusion_only_segment_is_not_unused():
    g = make_group(1, segment_ids=[5], excluded_segment_ids=[6])
    ds = make_ds([g], segments=[{"id": 5, "name": "a"}, {"id": 6, "name": "минус"}, {"id": 7, "name": "никому"}])
    fs = found(ds, "unused_segments")
    assert [f["entity"]["id"] for f in fs] == [7]
    assert fs[0]["check"] is None


def test_normalize_segment_relations(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [])
    write_call(raw, "vk_ads_remarketing_segments_list", {"paginated": True}, {"items": [
        {"id": 5, "name": "из базы", "relations": [{"object_type": "remarketing_users_list", "object_id": 9}]},
        {"id": 6, "name": "из пикселя", "relations": [{"object_type": "remarketing_counter", "object_id": 3}]}]})
    ds = build_dataset(raw, "2026-09-01", "2026-09-30")
    assert ds["segment_relations_known"] is True
    assert [s["list_ids"] for s in ds["segments"]] == [[9], []]


def test_normalize_segment_relations_unknown(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [])
    write_call(raw, "vk_ads_remarketing_segments_list", {"paginated": True}, {"items": [{"id": 5, "name": "x"}]})
    ds = build_dataset(raw, "2026-09-01", "2026-09-30")
    assert ds["segment_relations_known"] is False and ds["segments"][0]["list_ids"] is None


OLD = {"id": 1, "name": "Клиенты", "size": 900, "updated": "2026-05-01 00:00:00"}


def _seg(list_ids):
    return {"id": 5, "name": "s", "lal": False, "size": None, "list_ids": list_ids}


def _c14_ds(lists, segs, groups_):
    known = any(s.get("list_ids") is not None for s in segs)
    return make_ds(groups_, BANNERS, users_lists=lists, segments=segs, segment_relations_known=known)


def test_c14_unknown_when_list_dates_unknown():
    ds = _c14_ds([{"id": 1, "name": "K", "size": 9, "updated": None}], [_seg([1])], [make_group(1, segment_ids=[5])])
    cl = checklist(ds)
    assert status(cl, "C14") == "unknown" and "даты обновления" in reason(cl, "C14")


def test_c14_unknown_when_relation_unknown_and_card_is_low_confidence():
    ds = _c14_ds([OLD], [_seg(None)], [make_group(1, segment_ids=[5])])
    fs = found(ds, "stale_list")
    assert len(fs) == 1 and fs[0]["confidence"] == "low"
    assert "если база используется в таргетинге" in fs[0]["title"]
    cl = checklist(ds)
    assert status(cl, "C14") == "unknown" and "связать" in reason(cl, "C14")


def test_unused_old_list_is_not_stale():
    ds = _c14_ds([OLD], [_seg([2])], [make_group(1, segment_ids=[5])])
    assert found(ds, "stale_list") == []
    assert status(checklist(ds), "C14") == "na"


def test_referenced_old_list_is_stale_and_c14_fails():
    ds = _c14_ds([OLD], [_seg([1])], [make_group(1, segment_ids=[5])])
    fs = found(ds, "stale_list")
    assert len(fs) == 1 and fs[0]["confidence"] == "high"
    assert status(checklist(ds), "C14") == "fail"


def test_list_referenced_through_exclusion_counts_as_used():
    ds = _c14_ds([OLD], [_seg([1])], [make_group(1, segment_ids=[], excluded_segment_ids=[5])])
    assert len(found(ds, "stale_list")) == 1


# ---- 2. erid / auto_utm known per call ----

def test_erid_and_auto_utm_unknown_for_calls_without_the_field(tmp_path):
    raw = tmp_path / "raw"
    write_call(raw, "vk_ads_ad_plans_list", {"paginated": True}, {"items": []})
    write_call(raw, "vk_ads_campaigns_list", {"paginated": True, "fields": "id,name,enable_utm"},
               {"items": [{"id": 1, "name": "G1", "status": "active", "enable_utm": False}]})
    write_call(raw, "vk_ads_campaigns_list", {"paginated": True, "fields": "id,name"},
               {"items": [{"id": 2, "name": "G2", "status": "active"}]})
    write_call(raw, "vk_ads_banners_list", {"campaign_id": 1, "paginated": True, "fields": "id,erid,urls"},
               {"items": [{"id": 11, "status": "active", "erid": "abc", "urls": {"u": {"url": "https://s.ru/"}}}]})
    write_call(raw, "vk_ads_banners_list", {"campaign_id": 2, "paginated": True, "fields": "id,urls"},
               {"items": [{"id": 21, "status": "active", "urls": {"u": {"url": "https://s.ru/"}}}]})
    ds = build_dataset(raw, "2026-09-01", "2026-09-30")
    by = {b["id"]: b for b in ds["banners"]}
    assert by[11]["erid"] == "abc" and by[21]["erid"] is None
    g = {x["id"]: x for x in ds["groups"]}
    assert g[1]["auto_utm"] is False and g[2]["auto_utm"] is None
    assert found(ds, "ord_missing") == []
    assert status(checklist(ds), "A2") == "pass"


def test_unknown_erid_banner_is_not_evaluated():
    ds = make_ds([make_group(1)], [make_banner(11, 1, erid="abc"), make_banner(21, 1, erid=None)])
    assert found(ds, "ord_missing") == []
    assert res(ds)["evaluated"]["ord_missing"] == 1


# ---- 3. superseded fallback call ----

def test_failed_extended_call_marked_superseded_when_base_succeeds(tmp_path):
    log = collect(RejectsExtendedFields(groups(2)), tmp_path / "raw", "2026-09-01", "2026-09-30")
    sup = [c for c in log["calls"] if c.get("superseded")]
    assert {c["tool"] for c in sup} == {"vk_ads_campaigns_list", "vk_ads_banners_list"}
    assert all(not c["ok"] for c in sup)
    assert log["errors"] == 2


def test_failed_call_without_successful_retry_is_not_superseded(tmp_path):
    class FailGroup2(FakeClient):
        def paginate(self, tool, arguments=None, **kw):
            if tool == "vk_ads_banners_list" and arguments["campaign_id"] == 2:
                self._check(tool, arguments)
                raise McpError(f"{tool}: HTTP 500", status=500)
            return super().paginate(tool, arguments, **kw)
    log = collect(FailGroup2(groups(2)), tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert not any(c.get("superseded") for c in log["calls"])


def test_limitations_skip_superseded_and_add_one_line():
    ds = make_ds([make_group(1)])
    log = {"calls": [
        {"tool": "vk_ads_campaigns_list", "ok": False, "error": "HTTP 400", "superseded": True},
        {"tool": "vk_ads_banners_list", "ok": False, "error": "HTTP 400", "superseded": True},
        {"tool": "vk_ads_users_lists_list", "ok": False, "error": "HTTP 500"}], "errors": 3}
    lines = limitations(ds, log)
    assert not any("vk_ads_campaigns_list" in x or "vk_ads_banners_list" in x for x in lines)
    assert any("users_lists_list" in x for x in lines)
    line = "Расширенные поля не приняты сервером — часть проверок (маркировка, UTM, кнопки) не проверена"
    assert lines.count(line) == 1
    assert line not in limitations(ds, {"calls": [], "errors": 0})


# ---- 4. D3 young groups ----

def test_learning_starved_silent_for_young_group():
    daily = [{"date": f"2026-09-{d:02d}", **stats(100, 1000, 20, 1)} for d in range(28, 31)]
    g = make_group(1, autobidding_mode="max_goals", daily=daily, stats=stats(300, 3000, 60, 5))
    ds = make_ds([g], BANNERS)
    assert found(ds, "learning_starved") == []
    assert res(ds)["evaluated"].get("learning_starved", 0) == 0


# ---- 5. layer unknown when segments cannot be resolved ----

def test_layer_none_without_segments_list(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [{"id": 1, "name": "G", "status": "active", "objective": "site_conversions", "ad_plan_id": 7,
                   "targetings": {"segments": [5]}},
                  {"id": 2, "name": "H", "status": "active", "objective": "site_conversions", "ad_plan_id": 7,
                   "targetings": {"geo": {"regions": [1]}}}])
    g1, g2 = build_dataset(raw, "2026-09-01", "2026-09-30")["groups"]
    assert g1["layer"] is None and g2["layer"] == "cold"


def test_layer_none_for_segment_missing_from_list(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [{"id": 1, "name": "G", "status": "active", "targetings": {"segments": [5, 99]}},
                  {"id": 2, "name": "H", "status": "active", "targetings": {"segments": [5]}}])
    write_call(raw, "vk_ads_remarketing_segments_list", {"paginated": True},
               {"items": [{"id": 5, "name": "Посетители"}]})
    g1, g2 = build_dataset(raw, "2026-09-01", "2026-09-30")["groups"]
    assert g1["layer"] is None and g2["layer"] == "warm"


def test_layer_rules_silent_and_b2_unknown_when_layers_unknown():
    ds = make_ds([make_group(1, layer=None, segment_ids=[5])], BANNERS)
    assert found(ds, "layer_missing") == [] and found(ds, "no_retargeting") == []
    assert status(checklist(ds), "B2") == "unknown"


# ---- 6. cap_too_early with auto bidding ----

def test_cap_too_early_silent_for_auto_bidding():
    w7 = stats(700, 7000, 70, 5)
    g = make_group(1, autobidding_mode="max_goals", max_price_rub=50.0, windows={"7": w7, "14": None, "30": None})
    assert found(make_ds([g]), "cap_too_early") == []


# ---- 7. C1 radius geo ----

def test_c1_passes_with_local_geo():
    g = make_group(1, features={"regions": [], "keys": ["local_geo", "age"]})
    assert status(checklist(make_ds([g], BANNERS)), "C1") == "pass"
    g2 = make_group(1, features={"regions": [], "keys": ["age"]})
    assert status(checklist(make_ds([g2], BANNERS)), "C1") == "fail"


# ---- 8. A3 with auto-UTM everywhere ----

def test_a3_pass_when_all_site_groups_have_auto_utm():
    ds = make_ds([make_group(1, auto_utm=True)],
                 [make_banner(11, 1, urls=["https://site.ru/"]), make_banner(12, 1, urls=["https://site.ru/a"])])
    assert found(ds, "utm_missing") == []
    assert status(checklist(ds), "A3") == "pass"


# ---- 9. creative_overlap skips groups without a layer ----

def test_creative_overlap_skips_groups_without_targetings():
    g1, g2 = make_group(1, layer=None), make_group(2, layer=None)
    ds = make_ds([g1, g2], [make_banner(11, 1, content_ids=[900]), make_banner(21, 2, content_ids=[900])])
    assert found(ds, "creative_overlap") == []
    assert res(ds)["evaluated"].get("creative_overlap", 0) == 0


# ---- 10. B1.* na without live active groups ----

def test_b1_na_without_active_groups():
    cl = checklist(make_ds([make_group(1, status="blocked")], [make_banner(11, 1)]))
    for key in ("subscription", "leadform", "retargeting", "senler", "site"):
        assert status(cl, f"B1.{key}") == "na", key
