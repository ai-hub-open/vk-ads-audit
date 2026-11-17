"""Рекомендации аудита — только из того, что есть в VK Рекламе и в кабинете (сессия 06.11.2026).

`no_retargeting` советовал «вовлечённых в сообщество и досмотревших видео» любому кабинету — агент
запуска превратил это в несуществующие шаги интерфейса. `low_budget` писал «Дневной бюджет не ниже
X ₽ (≥ 2 × CPA)» — агент выдал ориентир за обязательный минимум и предлагал поднять бюджет сверх
лимита клиента.
"""
from __future__ import annotations

from scripts.diagnose import diagnose, load_thresholds
from tests.helpers import make_ds, make_group

TH = load_thresholds()


def _finding(ds, rule):
    got = [f for f in diagnose(ds, TH)["findings"] if f["rule"] == rule]
    assert got, f"нет находки {rule}"
    return got[0]


def _cold(gid, **kw):
    return make_group(gid, layer="cold", **kw)


def test_no_retargeting_never_suggests_engaged_or_video():
    action = _finding(make_ds([_cold(1), _cold(2)]), "no_retargeting")["action"]
    assert "вовлеч" not in action.lower() and "видео" not in action.lower()


def test_no_retargeting_names_pixel_only_when_it_exists():
    with_pixel = _finding(make_ds([_cold(1)]), "no_retargeting")["action"]
    assert "посетителей сайта по пикселю" in with_pixel
    no_pixel = _finding(make_ds([_cold(1)], pixels=[]), "no_retargeting")["action"]
    assert "посетителей сайта" not in no_pixel
    cov = {"path": "A", "banners": True, "summary": True, "stats": True, "pixels": False, "metrika": False}
    unknown = _finding(make_ds([_cold(1)], pixels=[], coverage=cov), "no_retargeting")["action"]
    assert "если в кабинете есть пиксель" in unknown


def test_no_retargeting_community_and_base_follow_cabinet():
    community = _finding(make_ds([_cold(1, objective="socialengagement")]), "no_retargeting")["action"]
    assert "подписчиков своего сообщества;" in community or community.endswith("подписчиков своего сообщества")
    other = _finding(make_ds([_cold(1)]), "no_retargeting")["action"]
    assert "подписчиков своего сообщества, если оно есть" in other
    assert "если клиент готов её загрузить" in other
    loaded = _finding(make_ds([_cold(1)], users_lists=[{"id": 5, "name": "Клиенты"}]), "no_retargeting")["action"]
    assert "загруженную базу клиентов" in loaded


def test_learning_budget_is_a_guideline_not_a_requirement():
    g = make_group(1, budget_day_rub=600.0, autobidding_mode="max_goals")
    f = [x for x in diagnose(make_ds([g], target_cpa=2000), TH)["findings"]
         if x["rule"] == "low_budget" and x["level"] == "orange"]
    assert f, "нет находки «бюджет на обучение»"
    action = f[0]["action"]
    assert action.startswith("Ориентир — от 4000 ₽ в день")
    assert "не требование VK" in action and "не гарантирует" in action
    assert "сократить число групп, а не поднимать бюджет" in action
    assert "не ниже" not in action
