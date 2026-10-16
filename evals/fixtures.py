"""Синтетические кабинеты VK в формате raw/ (как отдаёт vk-ads-mcp 0.11.0, формы подтверждены живым прогоном 16.10.2026):
бюджеты — рубли, ctr — проценты, итоги периода — сумма statistics_day (по группам и объявлениям), statistics_summary —
без дат, за всё время (нужна только ради охвата и частоты групп, созданных внутри периода)."""
from __future__ import annotations

from pathlib import Path

from scripts.raw_store import write_call

P = {"date_from": "2026-09-01", "date_to": "2026-09-30", "metrics": "base"}
DAYS = [f"2026-09-{d:02d}" for d in range(1, 31)]


def block(spent, shows, clicks, goals, frequency=2.0):
    return {"base": {"spent": f"{spent:.2f}", "shows": shows, "clicks": clicks, "goals": goals,
                     "ctr": (clicks / shows * 100) if shows else 0, "vk": {"result": 0}},
            "uniques": {"reach": int(shows / frequency) if shows else 0, "frequency": frequency if shows else 0}}


def group(gid, regions, budget_rub=2000, status="active", created="2026-08-01 10:00:00", **kw):
    g = {"id": gid, "name": f"Группа {gid}", "status": status, "objective": "site_conversions",
         "package_id": 3217, "ad_plan_id": 1, "budget_limit_day": budget_rub, "issues": [],
         "created": created, "delivery": "delivering", "autobidding_mode": None,
         "targetings": {"geo": {"regions": regions}, "sex": ["male", "female"]}}
    g.update(kw)
    return g


def banners(gid, n=3, **kw):
    return [{"id": gid * 100 + i, "name": f"Объявление {gid}-{i}", "status": "active",
             "moderation_status": "allowed", **kw} for i in range(n)]


def write_cabinet(raw: Path, groups, banners_by_group, per_day, pixels=1, segments=(), plans=None,
                  segment_items=None, users_lists=()):
    """per_day[gid] — (spent, shows, clicks, goals) за каждый день или список из 30 таких кортежей; сводка — сумма."""
    write_call(raw, "vk_ads_ad_plans_list", {"paginated": True}, {"items": plans or [{"id": 1, "name": "План"}]})
    write_call(raw, "vk_ads_campaigns_list", {"paginated": True}, {"items": groups})
    for gid, bs in banners_by_group.items():
        write_call(raw, "vk_ads_banners_list", {"campaign_id": gid, "paginated": True}, {"items": bs})
    write_call(raw, "vk_ads_remarketing_segments_list", {"paginated": True},
               {"items": segment_items if segment_items is not None
                else [{"id": s, "name": f"Сегмент {s}"} for s in segments]})
    write_call(raw, "vk_ads_users_lists_list", {"paginated": True}, {"items": list(users_lists)})
    write_call(raw, "vk_ads_remarketing_pixels_list", {"paginated": True},
               {"items": [{"id": i, "name": "pixel"} for i in range(pixels)]})
    live = [g["id"] for g in groups if g["status"] != "deleted"]
    summary, daily, bdaily = [], [], []
    for gid in live:
        spec = per_day.get(gid, (0, 0, 0, 0))
        days = spec if isinstance(spec, list) else [spec] * len(DAYS)
        tot = [sum(d[i] for d in days) for i in range(4)]
        freq = 6.0 if gid in FREQ6 else 2.0
        summary.append({"id": gid, "total": block(*tot, frequency=freq)})
        daily.append({"id": gid, "rows": [{"date": d, **block(*v)} for d, v in zip(DAYS, days)]})
        bs = banners_by_group.get(gid, [])
        for b in bs:
            share = 1 / len(bs)
            bdaily.append({"id": b["id"], "rows": [
                {"date": d, **block(v[0] * share, int(v[1] * share), int(v[2] * share), int(v[3] * share),
                                    frequency=freq)} for d, v in zip(DAYS, days)]})
    write_call(raw, "vk_ads_statistics_summary", {"entity": "campaigns", "ids": ",".join(map(str, live)),
                                                  "metrics": "base,uniques"},          # сводка — без дат (всё время)
               {"items": summary})
    write_call(raw, "vk_ads_statistics_day", {"entity": "campaigns", "ids": ",".join(map(str, live)), **P},
               {"items": daily})
    write_call(raw, "vk_ads_statistics_day", {"entity": "banners", **P}, {"items": bdaily})


FREQ6: set = set()   # группы с частотой 6 (красное выгорание) — заполняет кабинет expert_1


AGES = list(range(25, 46))


def _good_banners(gid, prefix):
    roles = [{"image_1080x1080": {"id": gid * 1000 + 1}},
             {"image_1080x1350": {"id": gid * 1000 + 2}, "image_1920x1080": {"id": gid * 1000 + 3}},
             {"video_portrait_9_16_15s": {"id": gid * 1000 + 4}}]
    return [{"id": gid * 100 + i, "name": f"{prefix}_креатив{i}_Мск", "status": "active", "moderation_status": "allowed",
             "created": "2026-09-20 10:00:00", "call_to_action": "sign_up", "ord_marker": f"2VtzqxABCD{gid * 100 + i}",
             "textblocks": {"text_90": {"text": "Запишитесь на бесплатную консультацию"}},
             "urls": {"primary": {"id": 7, "url": "https://site.ru/?utm_source=vk_ads&utm_campaign={{campaign_id}}"}},
             "content": roles[i]} for i in range(3)]


def healthy(raw: Path) -> dict:
    FREQ6.clear()
    plans = [{"id": 1, "name": "ретарг_сайт_Мск", "budget_limit_day": 3000},
             {"id": 2, "name": "LAL_клиенты_Мск", "budget_limit_day": 3000},
             {"id": 3, "name": "холод_интересы_Мск", "budget_limit_day": 3000}]
    segs = [{"id": 101, "name": "Посетители сайта 30 дней"}, {"id": 102, "name": "LAL на клиентов 3%", "size": 120},
            {"id": 601, "name": "Клиенты — исключение",
             "relations": [{"object_type": "remarketing_users_list", "object_id": 601}]}]
    # исключение клиентов и ретаргета; места размещения — автоматические
    base_t = {"geo": {"regions": [1]}, "age": {"age_list": AGES}, "auto_placements": True}
    groups = [group(1, [1], ad_plan_id=1, name="ретарг_сайт_Мск_Ж25-45_моб",
                    targetings={**base_t, "sex": ["female"], "segments": [101],
                                 "segments_exclude": [601]}),
              group(2, [1], ad_plan_id=2, name="LAL_клиенты_Мск_25-45",
                    targetings={**base_t, "segments": [102], "segments_exclude": [101, 601]}),
              group(3, [1], ad_plan_id=3, name="холод_велоспорт_Мск_25-45",
                    targetings={**base_t, "interests": [1001], "segments_exclude": [101, 601]})]
    for g in groups:
        g["enable_utm"] = False                                           # авто-UTM выключена, но метки есть в ссылках
    bs = {1: _good_banners(1, "ретарг"), 2: _good_banners(2, "LAL"), 3: _good_banners(3, "холод")}
    write_cabinet(raw, groups, bs, {1: (440, 20000, 300, 4), 2: (440, 20000, 300, 4), 3: (220, 10000, 150, 2)},
                  plans=plans, segment_items=segs,
                  users_lists=[{"id": 601, "name": "Клиенты", "entries_count": 300,
                                "updated": "2026-09-25 00:00:00"}])    # база клиентов — для исключения
    return {**P, "target_cpa": 300.0, "expected": set(), "scope": None, "as_of": "2026-10-01T10:00"}


LEAKING_SCOPE = {"spend_no_conversions", "cpa_above_target"}
HYGIENE_SCOPE = {"low_budget", "rejected_banners", "duplicate_targeting", "autodelete_risk", "banners_per_group",
                 "unused_segments"}


def leaking(raw: Path) -> dict:
    FREQ6.clear()
    groups = [group(1, [1]), group(2, [2]), group(3, [3])]
    write_cabinet(raw, groups, {1: banners(1), 2: banners(2), 3: banners(3)},
                  {1: (500, 10000, 150, 2),      # норма: CPA 250
                   2: (400, 10000, 100, 0),      # 12 000 ₽ без конверсий
                   3: (900, 10000, 120, 1)})     # CPA 900 > 300 × 1.5
    return {**P, "target_cpa": 300.0, "expected": {("spend_no_conversions", 2), ("cpa_above_target", 3)},
            "scope": LEAKING_SCOPE, "as_of": "2026-10-01T10:00"}


def hygiene(raw: Path) -> dict:
    FREQ6.clear()
    groups = [group(1, [1], budget_rub=200),                         # 200 ₽ < 300
              group(2, [5]), group(3, [5]),                          # одинаковый таргетинг
              group(4, [4], created="2026-01-10 00:00:00"),          # старая, без показов
              group(5, [6])]                                         # одно объявление
    bs = {1: banners(1), 2: banners(2), 3: banners(3), 4: banners(4),
          5: banners(5, n=1)}
    bs[1][0]["moderation_status"] = "banned"
    write_cabinet(raw, groups, bs, {1: (100, 3000, 30, 1), 2: (100, 3000, 30, 1), 3: (100, 3000, 30, 1),
                                    5: (100, 3000, 30, 1)},
                  segments=(77,))
    return {**P, "target_cpa": None, "expected": {
        ("low_budget", 1), ("rejected_banners", 100), ("duplicate_targeting", 2), ("autodelete_risk", 4),
        ("banners_per_group", 5), ("unused_segments", 77)},
        "scope": HYGIENE_SCOPE, "as_of": "2026-10-01T10:00"}


def empty(raw: Path) -> dict:
    FREQ6.clear()
    write_cabinet(raw, [], {}, {})
    return {**P, "target_cpa": None, "expected": set(), "scope": None, "as_of": "2026-10-01T10:00"}


def path_a_partial(raw: Path) -> dict:
    """Путь A: объявления собраны только у 5 групп из 7 — у остальных двух нет ложного «нет объявлений»."""
    FREQ6.clear()
    groups = [group(i, [i]) for i in range(1, 8)]
    per_day = {i: (500, 10000, 150, 2) for i in range(1, 8)}
    write_cabinet(raw, groups, {i: banners(i) for i in range(1, 6)}, per_day)
    return {**P, "target_cpa": 300.0, "expected": set(), "as_of": "2026-10-01T10:00",
            "scope": {"no_active_banners", "banners_per_group", "missing_ratios", "stale_creatives"}}


def expert_1(raw: Path) -> dict:
    """Кабинет, где методика v1 находит почти всё: каждое новое правило срабатывает хотя бы раз."""
    FREQ6.clear()
    FREQ6.add(9)
    plans = [{"id": 1, "name": "Кампания 1", "budget_limit_day": 0},
             {"id": 2, "name": "Новая кампания", "budget_limit_day": 0}]
    segs = [{"id": 501, "name": "LAL клиенты", "size": 20,
              "relations": [{"object_type": "remarketing_users_list", "object_id": 601}]}]
    users = [{"id": 601, "name": "Клиенты 2025", "entries_count": 300, "updated": "2026-01-15 00:00:00"}]
    kw_t = {"geo": {"regions": [5506]}, "keywords": ["купить велосипед"], "minus_phrases": [], "search_period": 30,
            "expand_keywords": False, "segments_exclude": [], "auto_placements": False}
    groups = [group(1, [5506], ad_plan_id=1, name="Группа 1", targetings=kw_t),
              group(2, [188], ad_plan_id=1, name="Группа 2", targetings={"geo": {"regions": [188]}}),   # широкий
              group(3, [5506], ad_plan_id=2, name="Группа 3", targetings={"geo": {"regions": [5506]}, "segments": [501]}),
              group(4, [5506], ad_plan_id=2, name="Группа 4", targetings={"geo": {"regions": [5506]}, "segments": [501]},
                    max_price="50.00"),
              group(9, [5506], ad_plan_id=2, name="Группа 9", created="2026-09-01 08:00:00",    # жизнь целиком в периоде → частота известна
                    targetings={"geo": {"regions": [5506]}, "interests": [7]})]
    groups += [group(20 + i, [5506 + i], ad_plan_id=2, name=f"Группа {20 + i}", budget_rub=150,
                     targetings={"geo": {"regions": [5506 + i]}, "interests": [i]}) for i in range(10)]
    for g in groups:
        g["enable_utm"] = False                                           # авто-UTM выключена, в ссылках меток нет
    old = "2026-08-01 10:00:00"
    common = {"status": "active", "created": old, "call_to_action": "learn_more", "ord_marker": "",
              "textblocks": {"text_90": {"text": "Лучшие велосипеды города"}},
              "urls": {"primary": {"id": 1, "url": "https://bike.ru/catalog"}}}
    bs = {1: [{"id": 101, "name": "Объявление 1", "moderation_status": "allowed", **common,
               "content": {"image_1080x1080": {"id": 9001}}},
              {"id": 102, "name": "Объявление 2", "moderation_status": "banned",
               "moderation_reasons": ["Нужны документы на услугу"], **common, "content": {"image_1080x1080": {"id": 9002}}}],
          3: [{"id": 301, "name": "Объявление 3", "moderation_status": "allowed", **common,
               "content": {"image_1080x1080": {"id": 9100}}},
              {"id": 302, "name": "Объявление 4", "moderation_status": "delayed", **common,
               "created": "2026-09-30 00:00:00", "content": {"image_1080x1080": {"id": 9101}}}],
          4: [{"id": 401, "name": "Объявление 5", "moderation_status": "allowed", **common,
               "content": {"image_1080x1080": {"id": 9100}}},
              {"id": 402, "name": "Объявление 6", "moderation_status": "allowed", **common,
               "content": {"image_1080x1080": {"id": 9102}}}]}
    for gid in [2, 9] + [20 + i for i in range(10)]:    # объявления собраны по всем группам — иначе «нет видео» молчит
        bs[gid] = [{"id": gid * 100 + 1, "name": f"Объявление г{gid}", "moderation_status": "allowed", **common,
                    "content": {"image_1080x1080": {"id": 9500 + gid}}}]
    rising = [(100, 5000, 100, 2)] * 23 + [(300, 5000, 100, 1)] * 7          # CPA растёт в последнюю неделю
    per_day = {1: (100, 5000, 100, 2), 2: (100, 5000, 100, 2), 3: (100, 5000, 100, 2), 4: (100, 5000, 100, 0),
               9: rising}
    per_day.update({20 + i: (50, 5000, 2, 0) for i in range(10)})          # дорогие клики, CTR 0,04 %
    write_cabinet(raw, groups, bs, per_day, plans=plans, segment_items=segs, users_lists=users)
    return {**P, "target_cpa": None, "as_of": "2026-10-01T10:00",
            "expected_rules": {"ord_missing", "utm_missing", "no_retargeting", "layer_share", "mixed_plan",
                               "fragmentation", "no_cbo", "naming", "no_minus_phrases", "kw_period", "kw_expand_off",
                               "no_exclusions", "too_broad", "creative_overlap", "lal_small_source", "stale_list",
                               "manual_placements", "cap_too_early", "missing_ratios", "no_video", "stale_creatives",
                               "no_cta_text", "generic_button", "moderation_stuck", "cpc_outlier", "cpa_trend",
                               "burnout", "rejected_banners"}}


def expert_2(raw: Path) -> dict:
    """Только холодный слой — нет LAL и ретаргета."""
    FREQ6.clear()
    groups = [group(1, [5506], name="холод_интересы_Мск", targetings={"geo": {"regions": [5506]}, "interests": [1]})]
    write_cabinet(raw, groups, {}, {1: (300, 10000, 150, 2)})
    return {**P, "target_cpa": None, "as_of": "2026-10-01T10:00",
            "expected_rules": {"layer_missing", "no_retargeting"}}


CABINETS = {"healthy": healthy, "leaking": leaking, "hygiene": hygiene, "empty": empty,
            "path_a_partial": path_a_partial, "expert_1": expert_1, "expert_2": expert_2}
