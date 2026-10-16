from __future__ import annotations

from scripts.features import (banner_text, content_features, is_lal, layer_of, observed_keys,
                              targeting_features, url_strings)


def test_api_field_unknown_vs_unused():
    t_kw = {"geo": {"regions": [1]}, "keywords": ["купить велосипед"], "minus_phrases": []}
    t_plain = {"geo": {"regions": [1]}}
    observed = observed_keys([t_kw, t_plain])
    kw, plain = targeting_features(t_kw, observed), targeting_features(t_plain, observed)
    assert kw["has_keywords"] is True and kw["has_minus"] is False
    assert plain["has_keywords"] is False            # поле в кабинете встречается — значит, не используется
    assert plain["has_exclusions"] is None            # поле не встретилось нигде — неизвестно
    assert plain["kw_period"] is None


def test_targeting_basics():
    t = {"geo": {"regions": [188]}, "age": {"age_list": list(range(25, 46))}, "sex": ["female"],
         "interests": [1001], "search_period": 10}
    f = targeting_features(t, observed_keys([t]))
    assert f["regions"] == [188] and f["age_span"] == 20 and f["sex"] == ["female"]
    assert f["has_interests"] is True and f["kw_period"] == 10


def test_targeting_keys_recorded():
    assert targeting_features({"geo": {"regions": [188]}, "sex": ["female"]}, set())["keys"] == ["geo", "sex"]
    assert targeting_features(None, set())["keys"] == []


def test_no_age_is_none_span():
    assert targeting_features({}, set())["age_span"] is None


def test_content_ratios_and_video():
    c = {"image_1080x1080": {"id": 1}, "image_1080x1350": {"id": 2}, "image_1920x1080": {"id": 3},
         "video_portrait_9_16_30s": {"id": 4}, "icon_256x256": {"id": 5}}
    f = content_features(c)
    assert f["ratios"] == ["16:9", "1:1", "4:5", "9:16"]
    assert f["has_video"] is True and f["content_ids"] == [1, 2, 3, 4, 5]


def test_content_191_is_not_169():
    assert content_features({"image_1200x628": {"id": 1}})["ratios"] == []


def test_content_missing():
    assert content_features(None) == {"ratios": [], "has_video": False, "content_ids": [],
                                          "ratios_unknown": False}


def test_urls_and_text():
    assert url_strings({"primary": {"id": 5, "url": "https://site.ru/?utm_source=vk"}}) == \
        ["https://site.ru/?utm_source=vk"]
    assert banner_text({"title_40_vkads": {"text": "Велосипеды"}, "text_90": {"text": "Запишитесь"}}) == \
        "Велосипеды Запишитесь"


def test_layers():
    segs = {101: {"id": 101, "name": "Посетители сайта"}, 102: {"id": 102, "name": "LAL клиенты 3%"},
            103: {"id": 103, "name": "Похожие на покупателей"}}
    assert layer_of([], segs) == "cold"
    assert layer_of([101], segs) == "warm"
    assert layer_of([101, 102], segs) == "lal"
    assert is_lal(segs[103]) and not is_lal({"name": "Галактика"})
    assert layer_of([999], segs) is None   # сегмента нет в списке — слой неизвестен
    assert layer_of([101, 999], segs) is None and layer_of([102, 999], segs) == "lal"


def test_icons_do_not_count_as_ratios():
    f = content_features({"icon_256x256": {"id": 1}})
    assert f["ratios"] == [] and f["has_video"] is False and f["content_ids"] == [1]
    assert content_features({"icon_256x256": {"id": 1}, "image_1080x1350": {"id": 2}})["ratios"] == ["4:5"]


def test_first_known_skips_null():
    from scripts.features import ERID_KEYS, first_known
    d = {"ord_marker": None, "enable_utm": True}
    assert first_known(d, ERID_KEYS, observed_keys([d])) is None
    d = {"ord_marker": "2VtzquiRr5b"}
    assert first_known(d, ERID_KEYS, observed_keys([d])) == "2VtzquiRr5b"
    assert first_known(d, ERID_KEYS, set()) is None
