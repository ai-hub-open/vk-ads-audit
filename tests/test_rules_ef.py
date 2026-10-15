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


def test_missing_ratios_silent_when_banner_has_no_content():
    bs = [make_banner(11, 1, ratios=["1:1"], content_ids=[1]),   # по нему видно бы недостающие форматы
          make_banner(12, 1, content_ids=[])]                    # без контента — группа не оценивается
    assert found(make_ds([make_group(1)], bs), "missing_ratios") == []


def test_missing_ratios_silent_when_banner_unclassified():
    bs = [make_banner(11, 1, ratios=["1:1"], content_ids=[1]),
          make_banner(12, 1, content_ids=[2])]               # контент есть, форматы и видео не классифицированы
    assert found(make_ds([make_group(1)], bs), "missing_ratios") == []


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


def test_no_video_silent_on_partial_coverage():
    bs = [make_banner(11, 1, content_ids=[1])]
    ds = make_ds([make_group(1), make_group(2)], bs,
                 coverage={"path": "A", "banners": True, "banner_groups": [1], "summary": True, "stats": True,
                           "pixels": True, "metrika": False})   # группа 2 активна, объявления по ней не собраны
    assert found(ds, "no_video") == []


def test_stale_creatives_levels():
    def ds_with(age):
        g = make_group(1, stats=stats(1000, 10000, 100, 5))
        return make_ds([g], [make_banner(11, 1, age_days=age), make_banner(12, 1, age_days=age + 3)])
    assert found(ds_with(10), "stale_creatives") == []
    assert found(ds_with(20), "stale_creatives")[0]["level"] == "white"
    assert found(ds_with(40), "stale_creatives")[0]["level"] == "orange"


def test_stale_creatives_silent_on_unknown_age():
    g = make_group(1, stats=stats(1000, 10000, 100, 5))
    ds = make_ds([g], [make_banner(11, 1, age_days=40), make_banner(12, 1, age_days=None)])
    assert found(ds, "stale_creatives") == []


def test_cta_and_button():
    bs = [make_banner(11, 1, text="Лучшие велосипеды", cta="learn_more"),
          make_banner(12, 1, text="Запишитесь на тест-драйв", cta="sign_up")]
    ds = make_ds([make_group(1)], bs)
    assert found(ds, "no_cta_text")[0]["evidence"]["banners"] == [11]
    assert found(ds, "generic_button")[0]["evidence"]["banners"] == [11]


def test_cta_silent_without_text():
    assert found(make_ds([make_group(1)], [make_banner(11, 1), make_banner(12, 1)]), "no_cta_text") == []


def test_generic_button_silent_without_cta():
    ds = make_ds([make_group(1)], [make_banner(11, 1, cta=None, text="Лучшие велосипеды")])
    assert found(ds, "generic_button") == []


def test_moderation_stuck():
    video = make_banner(11, 1, moderation_status="delayed", has_video=True, created="2026-09-30 18:00:00")
    image = make_banner(12, 1, moderation_status="delayed", created="2026-09-30 18:00:00")
    got = {f["entity"]["id"] for f in found(make_ds([make_group(1)], [video, image]), "moderation_stuck")}
    assert got == {11}                                       # as_of 2026-10-01T10:00: 16 ч > 8+3 для видео, < 24+3 для картинок


def test_moderation_measured_from_update():
    b = make_banner(11, 1, moderation_status="delayed", has_video=True, created="2026-09-30 00:00:00",
                    updated="2026-10-01 08:00:00")            # правка 2 ч назад — модерация идёт заново
    assert found(make_ds([make_group(1)], [b]), "moderation_stuck") == []


def test_moderation_silent_without_dates():
    b = make_banner(11, 1, moderation_status="delayed")       # created и updated не получены
    assert found(make_ds([make_group(1)], [b]), "moderation_stuck") == []


def test_cpc_outlier():
    gs = [make_group(1, stats=stats(1000, 20000, 200)), make_group(2, stats=stats(3000, 20000, 60))]
    ds = make_ds(gs, totals=stats(4000, 40000, 260))
    f = found(ds, "cpc_outlier")[0]
    assert f["entity"]["id"] == 2 and f["evidence"]["cpc"] == 50.0


def test_cpc_outlier_no_clicks():
    assert found(make_ds([make_group(1)], totals=stats()), "cpc_outlier") == []


def test_cpc_outlier_early_group_skipped():
    daily = [day(f"2026-09-{d:02d}", 0, 0, 0) for d in range(1, 26)] + \
            [day(f"2026-09-{d:02d}", 1000, 2000, 20) for d in range(26, 29)]    # 3 дня работы
    gs = [make_group(1, stats=stats(1000, 20000, 200)), make_group(2, stats=stats(3000, 20000, 60), daily=daily)]
    assert found(make_ds(gs, totals=stats(4000, 40000, 260)), "cpc_outlier") == []


def test_cpa_trend():
    w7, w30 = stats(1400, 7000, 70, 5), stats(3000, 30000, 300, 20)
    g = make_group(1, stats=w30, windows={"7": w7, "14": None, "30": w30})
    f = found(make_ds([g]), "cpa_trend")[0]
    assert f["evidence"]["cpa_7d"] == 280.0 and f["evidence"]["cpa_30d"] == 150.0


def test_cpa_trend_silent_on_few_week_conversions():
    w7, w30 = stats(1400, 7000, 70, 1), stats(3000, 30000, 300, 20)   # cpa_7d 1400 — по одной конверсии
    g = make_group(1, stats=w30, windows={"7": w7, "14": None, "30": w30})
    assert found(make_ds([g]), "cpa_trend") == []


def test_cpa_trend_silent_without_week_window():
    w30 = stats(3000, 30000, 300, 20)
    g = make_group(1, stats=w30, windows={"7": None, "14": None, "30": w30})
    assert found(make_ds([g]), "cpa_trend") == []


def test_cpa_trend_early_group_skipped():
    w7, w30 = stats(1400, 7000, 70, 5), stats(3000, 30000, 300, 20)
    daily = [day("2026-09-28", 0, 0, 0), day("2026-09-29", 500, 3000, 30, 2), day("2026-09-30", 900, 4000, 40, 3)]
    g = make_group(1, stats=w30, daily=daily, windows={"7": w7, "14": None, "30": w30})   # 2 дня работы
    assert found(make_ds([g]), "cpa_trend") == []


def test_early_groups_skipped():
    daily = [day(f"2026-09-{d:02d}", 0, 0, 0) for d in range(1, 26)] + \
            [day(f"2026-09-{d:02d}", 1000, 2000, 2) for d in range(26, 31)]          # 5 дней работы
    g = make_group(1, daily=daily, stats=stats(5000, 10000, 10))
    assert found(make_ds([g]), "low_ctr") == []


def test_early_banners_skipped():
    b = make_banner(11, 1, age_days=3, stats=stats(900, 10000, 20))
    assert found(make_ds([make_group(1)], [b, make_banner(12, 1)]), "low_ctr") == []


def test_old_banner_low_ctr_fires():
    b = make_banner(11, 1, age_days=10, stats=stats(900, 10000, 20))
    f = found(make_ds([make_group(1)], [b, make_banner(12, 1)]), "low_ctr")
    assert [(x["entity"]["type"], x["entity"]["id"]) for x in f] == [("banner", 11)]


def test_burnout_early_group_skipped():
    daily = [day("2026-09-28", 300, 3000, 30), day("2026-09-29", 300, 3000, 30), day("2026-09-30", 300, 3000, 30)]
    g = make_group(1, daily=daily, stats=stats(900, 12000, 90, 0, frequency=6))   # частота 6, но 3 дня работы
    assert found(make_ds([g]), "burnout") == []
