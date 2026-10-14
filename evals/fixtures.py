"""Синтетические кабинеты VK в формате raw/ (как отдаёт vk-ads-mcp 0.11.0)."""
from __future__ import annotations

from pathlib import Path

from scripts.raw_store import write_call

P = {"date_from": "2026-09-01", "date_to": "2026-09-30", "metrics": "all"}
DAYS = [f"2026-09-{d:02d}" for d in range(1, 31)]


def block(spent, shows, clicks, goals, frequency=2.0):
    return {"base": {"spent": f"{spent:.2f}", "shows": shows, "clicks": clicks, "goals": goals,
                     "ctr": (clicks / shows) if shows else 0, "vk": {"result": 0}},
            "uniques": {"reach": int(shows / frequency) if shows else 0, "frequency": frequency if shows else 0}}


def group(gid, regions, budget_kop=200000, status="active", created="2026-08-01 10:00:00", **kw):
    g = {"id": gid, "name": f"Группа {gid}", "status": status, "objective": "site_conversions",
         "package_id": 3217, "ad_plan_id": 1, "budget_limit_day": budget_kop, "issues": [],
         "created": created, "delivery": "delivering", "autobidding_mode": None,
         "targetings": {"geo": {"regions": regions}, "sex": ["male", "female"]}}
    g.update(kw)
    return g


def banners(gid, n=3, **kw):
    return [{"id": gid * 100 + i, "name": f"Объявление {gid}-{i}", "status": "active",
             "moderation_status": "allowed", **kw} for i in range(n)]


def write_cabinet(raw: Path, groups, banners_by_group, per_day, per_banner_share=None, pixels=1, segments=()):
    """per_day[gid] = (spent, shows, clicks, goals) за каждый день; сводка — сумма."""
    write_call(raw, "vk_ads_ad_plans_list", {"paginated": True}, {"items": [{"id": 1, "name": "План"}]})
    write_call(raw, "vk_ads_campaigns_list", {"paginated": True}, {"items": groups})
    for gid, bs in banners_by_group.items():
        write_call(raw, "vk_ads_banners_list", {"campaign_id": gid, "paginated": True}, {"items": bs})
    write_call(raw, "vk_ads_remarketing_segments_list", {"paginated": True},
               {"items": [{"id": s, "name": f"Сегмент {s}"} for s in segments]})
    write_call(raw, "vk_ads_users_lists_list", {"paginated": True}, {"items": []})
    write_call(raw, "vk_ads_remarketing_pixels_list", {"paginated": True},
               {"items": [{"id": i, "name": "pixel"} for i in range(pixels)]})
    live = [g["id"] for g in groups if g["status"] != "deleted"]
    summary, daily, bsum = [], [], []
    for gid in live:
        s, sh, c, go = per_day.get(gid, (0, 0, 0, 0))
        n = len(DAYS)
        summary.append({"id": gid, "total": block(s * n, sh * n, c * n, go * n)})
        daily.append({"id": gid, "rows": [{"date": d, **block(s, sh, c, go)} for d in DAYS]})
        bs = banners_by_group.get(gid, [])
        for b in bs:
            share = 1 / len(bs)
            bsum.append({"id": b["id"], "total": block(s * n * share, int(sh * n * share),
                                                       int(c * n * share), int(go * n * share))})
    write_call(raw, "vk_ads_statistics_summary", {"entity": "campaigns", "ids": ",".join(map(str, live)), **P},
               {"items": summary})
    write_call(raw, "vk_ads_statistics_day", {"entity": "campaigns", "ids": ",".join(map(str, live)), **P},
               {"items": daily})
    write_call(raw, "vk_ads_statistics_summary", {"entity": "banners", **P}, {"items": bsum})


def healthy(raw: Path) -> dict:
    groups = [group(1, [1]), group(2, [2])]
    write_cabinet(raw, groups, {1: banners(1), 2: banners(2)},
                  {1: (500, 10000, 150, 2), 2: (600, 12000, 180, 2)})
    return {**P, "target_cpa": 300.0, "expected": set()}


def leaking(raw: Path) -> dict:
    groups = [group(1, [1]), group(2, [2]), group(3, [3])]
    write_cabinet(raw, groups, {1: banners(1), 2: banners(2), 3: banners(3)},
                  {1: (500, 10000, 150, 2),      # норма: CPA 250
                   2: (400, 10000, 100, 0),      # 12 000 ₽ без конверсий
                   3: (900, 10000, 120, 1)})     # CPA 900 > 300 × 1.5
    return {**P, "target_cpa": 300.0, "expected": {("spend_no_conversions", 2), ("cpa_above_target", 3)}}


def hygiene(raw: Path) -> dict:
    groups = [group(1, [1], budget_kop=20000),                       # 200 ₽ < 300
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
        ("banners_per_group", 5), ("unused_segments", 77)}}


def empty(raw: Path) -> dict:
    write_cabinet(raw, [], {}, {})
    return {**P, "target_cpa": None, "expected": set()}


def path_a_partial(raw: Path) -> dict:
    """Путь A: объявления собраны только у 5 групп из 7 — у остальных двух нет ложного «нет объявлений»."""
    groups = [group(i, [i]) for i in range(1, 8)]
    per_day = {i: (500, 10000, 150, 2) for i in range(1, 8)}
    write_cabinet(raw, groups, {i: banners(i) for i in range(1, 6)}, per_day)
    return {**P, "target_cpa": 300.0, "expected": set()}


CABINETS = {"healthy": healthy, "leaking": leaking, "hygiene": hygiene, "empty": empty,
            "path_a_partial": path_a_partial}
