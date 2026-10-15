from __future__ import annotations

from scripts.diagnose import diagnose, load_thresholds
from tests.helpers import make_banner, make_ds, make_group, stats

TH = load_thresholds()


def found(ds, rule):
    return [f for f in diagnose(ds, TH)["findings"] if f["rule"] == rule]


KW = {"has_keywords": True, "has_minus": False, "kw_period": 30, "kw_expand": False}


def test_keyword_rules():
    ds = make_ds([make_group(1, features=KW)])
    assert len(found(ds, "no_minus_phrases")) == 1
    assert found(ds, "kw_period")[0]["evidence"]["period_days"] == 30
    assert found(ds, "kw_expand_off")[0]["level"] == "green"


def test_keyword_rules_silent_when_unknown():
    ds = make_ds([make_group(1, features={"has_keywords": True})])   # has_minus/kw_period/kw_expand = None
    assert found(ds, "no_minus_phrases") == [] and found(ds, "kw_period") == [] and found(ds, "kw_expand_off") == []


def test_no_exclusions():
    ds = make_ds([make_group(1, features={"has_exclusions": False}),
                  make_group(2, objective="socialengagement", features={"has_exclusions": False}),
                  make_group(3)])
    assert [f["entity"]["id"] for f in found(ds, "no_exclusions")] == [1]


def test_too_broad():
    broad = make_group(1, features={"keys": ["geo"], "regions": [188], "age_span": None})
    narrow_geo = make_group(2, features={"keys": ["geo"], "regions": [5506], "age_span": None})
    with_interests = make_group(3, features={"keys": ["geo", "interests"], "regions": [188], "has_interests": True})
    ds = make_ds([broad, narrow_geo, with_interests])
    assert [f["entity"]["id"] for f in found(ds, "too_broad")] == [1]


def test_too_broad_basic_keys_only_fires_and_carries_no_effect():
    g = make_group(1, features={"keys": ["geo", "sex"], "regions": [188], "sex": [1], "age_span": None})
    g["stats"] = stats(spent=900)
    f = found(make_ds([g]), "too_broad")
    assert len(f) == 1 and f[0]["effect_rub"] == 0


def test_too_broad_silent_on_unknown_key():
    g = make_group(1, features={"keys": ["geo", "some_new_key"], "regions": [188], "age_span": None})
    assert found(make_ds([g]), "too_broad") == []


def test_too_broad_silent_when_geo_unparsable():
    g = make_group(1, features={"keys": ["geo"], "regions": [], "age_span": None})
    assert found(make_ds([g]), "too_broad") == []


def test_too_broad_silent_when_age_unparsable():
    g = make_group(1, features={"keys": ["geo", "age"], "regions": [188], "age_span": None})
    assert found(make_ds([g]), "too_broad") == []


def test_too_broad_skips_group_without_received_targeting():
    # layer=None: targetings not received, so "no geo / no interests" is unknown, not broad
    g = make_group(1, layer=None, features={"regions": [], "age_span": None})
    assert found(make_ds([g]), "too_broad") == []


def test_creative_overlap():
    g1, g2 = make_group(1, segment_ids=[5]), make_group(2, segment_ids=[5, 6])
    bs = [make_banner(11, 1, content_ids=[900]), make_banner(21, 2, content_ids=[900])]
    f = found(make_ds([g1, g2], bs), "creative_overlap")[0]
    assert f["evidence"] == {"groups": [1, 2], "shared_segments": [5]}
    g3 = make_group(3, segment_ids=[7])
    assert found(make_ds([g1, g3], [make_banner(11, 1, content_ids=[900]), make_banner(31, 3, content_ids=[900])]),
                 "creative_overlap") == []


def test_lal_small_source():
    segs = [{"id": 1, "name": "LAL", "lal": True, "size": 20}, {"id": 2, "name": "LAL2", "lal": True, "size": None},
            {"id": 3, "name": "Посетители", "lal": False, "size": 10}]
    f = found(make_ds([make_group(1, segment_ids=[1, 2, 3])], segments=segs), "lal_small_source")
    assert [x["entity"]["id"] for x in f] == [1]
    assert f[0]["evidence"] == {"segment_size": 20, "minimum": 50} and f[0]["confidence"] == "low"
    assert "Проверить исходную базу LAL" in f[0]["action"]


def test_stale_list():
    lists = [{"id": 1, "name": "Клиенты", "size": 900, "updated": "2026-05-01 00:00:00"},
             {"id": 2, "name": "Свежие", "size": 900, "updated": "2026-09-20 00:00:00"},
             {"id": 3, "name": "Неизвестно", "size": None, "updated": None}]
    assert [f["entity"]["id"] for f in found(make_ds([make_group(1)], users_lists=lists), "stale_list")] == [1]


def test_manual_placements():
    ds = make_ds([make_group(1, features={"auto_placements": False}), make_group(2)])
    assert [f["entity"]["id"] for f in found(ds, "manual_placements")] == [1]


def test_cap_too_early():
    w7 = stats(700, 7000, 70, 5)
    g = make_group(1, max_price_rub=50.0, windows={"7": w7, "14": None, "30": None})
    assert found(make_ds([g]), "cap_too_early")[0]["evidence"]["conversions_7d"] == 5
    ok = make_group(2, max_price_rub=50.0, windows={"7": stats(700, 7000, 70, 20), "14": None, "30": None})
    assert found(make_ds([ok]), "cap_too_early") == []
    assert found(make_ds([make_group(3)]), "cap_too_early") == []


def test_cap_too_early_only_for_conversion_objectives():
    w7 = stats(700, 7000, 70, 0)
    g = make_group(1, objective="socialengagement", max_price_rub=50.0, windows={"7": w7, "14": None, "30": None})
    assert found(make_ds([g]), "cap_too_early") == []


def test_cap_too_early_silent_without_7day_window():
    g = make_group(1, max_price_rub=50.0, stats=stats(7000, 70000, 700, 3),
                   windows={"7": None, "14": None, "30": None})
    assert found(make_ds([g]), "cap_too_early") == []
