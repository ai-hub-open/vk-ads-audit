from __future__ import annotations

from scripts.diagnose import diagnose, load_thresholds
from tests.helpers import make_banner, make_ds, make_group, stats

TH = load_thresholds()


def found(ds, rule):
    return [f for f in diagnose(ds, TH)["findings"] if f["rule"] == rule]


def day(d, spent, shows, clicks, conv=0):
    return {"date": d, **stats(spent, shows, clicks, conv)}


def test_missing_ratios():
    bs = [make_banner(11, 1, ratios=["1:1"], content_ids=[1]), make_banner(12, 1, ratios=["4:5"], content_ids=[2])]
    f = found(make_ds([make_group(1)], bs), "missing_ratios")[0]
    assert f["evidence"]["missing"] == ["16:9", "9:16"]


def test_ratio_rules_skip_uncovered_groups():
    bs = [make_banner(11, 1, ratios=["1:1"], content_ids=[1]), make_banner(12, 1, ratios=["1:1"], content_ids=[2])]
    ds = make_ds([make_group(1), make_group(2)], bs,
                 coverage={"path": "A", "banners": True, "banner_groups": [1], "summary": True, "stats": True,
                           "pixels": True, "metrika": False})
    assert [f["entity"]["id"] for f in found(ds, "missing_ratios")] == [1]
    assert all(f["entity"]["id"] != 2 for f in found(ds, "banners_per_group"))


def test_ratio_rules_silent_without_content():
    assert found(make_ds([make_group(1)], [make_banner(11, 1), make_banner(12, 1)]), "missing_ratios") == []


def test_no_video():
    bs = [make_banner(11, 1, content_ids=[1]), make_banner(12, 1, content_ids=[2])]
    assert len(found(make_ds([make_group(1)], bs), "no_video")) == 1
    bs[0]["has_video"] = True
    assert found(make_ds([make_group(1)], bs), "no_video") == []


def test_stale_creatives_levels():
    def ds_with(age):
        g = make_group(1, stats=stats(1000, 10000, 100, 5))
        return make_ds([g], [make_banner(11, 1, age_days=age), make_banner(12, 1, age_days=age + 3)])
    assert found(ds_with(10), "stale_creatives") == []
    assert found(ds_with(20), "stale_creatives")[0]["level"] == "white"
    assert found(ds_with(40), "stale_creatives")[0]["level"] == "orange"


def test_cta_and_button():
    bs = [make_banner(11, 1, text="Лучшие велосипеды", cta="learn_more"),
          make_banner(12, 1, text="Запишитесь на тест-драйв", cta="sign_up")]
    ds = make_ds([make_group(1)], bs)
    assert found(ds, "no_cta_text")[0]["evidence"]["banners"] == [11]
    assert found(ds, "generic_button")[0]["evidence"]["banners"] == [11]


def test_cta_silent_without_text():
    assert found(make_ds([make_group(1)], [make_banner(11, 1), make_banner(12, 1)]), "no_cta_text") == []


def test_moderation_stuck():
    video = make_banner(11, 1, moderation_status="delayed", has_video=True, created="2026-10-01 00:00:00")
    image = make_banner(12, 1, moderation_status="delayed", created="2026-10-01 00:00:00")
    got = {f["entity"]["id"] for f in found(make_ds([make_group(1)], [video, image]), "moderation_stuck")}
    assert got == {11}                                       # as_of 2026-10-01T10:00: 10 ч > 8 для видео, < 24 для картинок


def test_cpc_outlier():
    gs = [make_group(1, stats=stats(1000, 20000, 200)), make_group(2, stats=stats(3000, 20000, 60))]
    ds = make_ds(gs, totals=stats(4000, 40000, 260))
    f = found(ds, "cpc_outlier")[0]
    assert f["entity"]["id"] == 2 and f["evidence"]["cpc"] == 50.0


def test_cpc_outlier_no_clicks():
    assert found(make_ds([make_group(1)], totals=stats()), "cpc_outlier") == []


def test_cpa_trend():
    w7, w30 = stats(1400, 7000, 70, 5), stats(3000, 30000, 300, 20)
    g = make_group(1, stats=w30, windows={"7": w7, "14": None, "30": w30})
    f = found(make_ds([g]), "cpa_trend")[0]
    assert f["evidence"]["cpa_7d"] == 280.0 and f["evidence"]["cpa_30d"] == 150.0


def test_early_groups_skipped():
    daily = [day(f"2026-09-{d:02d}", 0, 0, 0) for d in range(1, 26)] + \
            [day(f"2026-09-{d:02d}", 1000, 2000, 2) for d in range(26, 31)]          # 5 дней работы
    g = make_group(1, daily=daily, stats=stats(5000, 10000, 10))
    assert found(make_ds([g]), "low_ctr") == []


def test_early_banners_skipped():
    b = make_banner(11, 1, age_days=3, stats=stats(900, 10000, 20))
    assert found(make_ds([make_group(1)], [b, make_banner(12, 1)]), "low_ctr") == []
