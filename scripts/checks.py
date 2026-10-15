"""
checks.py — чек-листы отчёта по блокам A–G (методика v1, формат агентского аудита).

Статусы: pass ✅, fail ❌, unknown — данных нет (поле не получено из API, путь A, нет Метрики),
na — неприменимо. unknown и na не входят в знаменатель балла; unknown уходит в «Ограничения».
Проверка с правилами: fail, если сработала хотя бы одна находка её правил.
"""
from __future__ import annotations

from collections import defaultdict

try:
    from scripts.diagnose import _active, _banner_groups, _feat, _layer, _live, _run_days
except ImportError:  # запуск напрямую
    from diagnose import _active, _banner_groups, _feat, _layer, _live, _run_days

BLOCKS = [("A", "Техническая настройка"), ("B", "Структура аккаунта"), ("C", "Таргетинг"),
          ("D", "Бюджет и стратегия"), ("E", "Объявления"), ("F", "Эффективность"),
          ("G", "Воронка после клика")]
FUNNELS = [("subscription", "подписка на сообщество"), ("leadform", "лид-форма"), ("retargeting", "ретаргетинг"),
           ("senler", "Senler"), ("site", "трафик на сайт")]
OK = (None, "")


def _unknown(label):
    return ("unknown", f"поле «{label}» не получено из API — проверится живым прогоном")


def _known_banner(banners, field, label):
    return OK if any(b.get(field) not in (None, [], "") for b in banners) else _unknown(label)


def _first(*gates):
    """Первый сработавший гейт (статус не None) или OK."""
    return next((g for g in gates if g[0]), OK)


def _known_feature(groups, key, label):
    return OK if any(_feat(g).get(key) is not None for g in groups) else _unknown(label)


def run_checks(ds: dict, th: dict, findings: list) -> dict:
    fired = defaultdict(list)
    for f in findings:
        fired[f["rule"]].append(f)
    rows: list = []
    cov = ds["coverage"]
    live = [g for g in _live(ds) if _active(g, th)]
    banners = [b for b in ds["banners"] if b["status"] in th["active_statuses"]]
    no_banners = ("unknown", "объявления не собирались") if not cov.get("banners") else OK
    no_stats = ("unknown", "статистика не собрана") if not cov.get("stats", True) else OK

    def add(cid, title, status, reason=""):
        rows.append({"id": cid, "block": cid[0], "title": title, "status": status, "reason": reason})

    def by_rules(cid, title, rules, gate=OK):
        if gate[0]:
            return add(cid, title, gate[0], gate[1])
        hits = [f for r in rules for f in fired.get(r, [])]
        add(cid, title, "fail" if hits else "pass", f"находок: {len(hits)}" if hits else "")

    def present(cid, title, ok, gate=OK):
        if gate[0]:
            return add(cid, title, gate[0], gate[1])
        add(cid, title, "pass" if ok else "fail", "" if ok else "не используется")

    layer_gate = OK if any(_layer(g) is not None for g in live) else (
        "unknown", "слои аудиторий не определены — таргетинги не получены")

    # A
    pixel_gate = OK
    if not cov.get("pixels", True):
        pixel_gate = ("unknown", "пиксели не собирались")
    elif not any(g["objective"] in th["pixel_objectives"] for g in live):
        pixel_gate = ("na", "нет групп с целью «конверсии на сайте»")
    by_rules("A1", "Пиксель установлен", ["no_pixel"], pixel_gate)
    by_rules("A2", "Креативы промаркированы (erid)", ["ord_missing"],
             no_banners if no_banners[0] else _known_banner(banners, "erid", "erid / маркировка ОРД"))
    by_rules("A3", "UTM-метки на объявлениях", ["utm_missing"],
             _first(no_banners, _known_banner(banners, "urls", "ссылки объявлений"),
                    OK if any(g.get("auto_utm") is not None for g in _live(ds))
                    else _unknown("авто-UTM группы")))

    # B
    objectives = {g["objective"] for g in live}
    funnel_ok = {k: bool(objectives & set(v)) for k, v in th["funnel_objectives"].items()}
    funnel_ok["retargeting"] = any(_layer(g) == "warm" for g in live)
    urls = [u.lower() for b in banners for u in b.get("urls") or []]
    for key, title in FUNNELS:
        if key == "senler":
            gate = OK if urls else ("unknown", "ссылки объявлений не получены")
            present(f"B1.{key}", f"Воронка: {title}", any(m in u for m in th["senler_markers"] for u in urls), gate)
        else:
            present(f"B1.{key}", f"Воронка: {title}", funnel_ok[key], layer_gate if key == "retargeting" else OK)
    by_rules("B2", "Есть тёплый, LAL и холодный слои", ["layer_missing", "no_retargeting"], layer_gate)
    spend = sum(g["stats"]["spent"] for g in _live(ds))
    by_rules("B3", "Доли бюджета по слоям", ["layer_share"], _first(layer_gate, OK if spend else ("na", "нет расхода за период")))
    by_rules("B4", "1 сегмент = 1 кампания", ["mixed_plan"], layer_gate)
    by_rules("B5", "Нет дробления на мелкие группы", ["fragmentation"])
    plans_known = any(p.get("has_budget") is not None for p in ds["plans"])
    by_rules("B6", "Бюджет на уровне кампании (CBO)", ["no_cbo"], OK if plans_known else _unknown("бюджет кампании"))
    by_rules("B7", "Говорящие названия", ["naming"])
    by_rules("B8", "Нет заброшенных групп", ["autodelete_risk"], no_stats)
    by_rules("B9", "Расход распределён по группам", ["spend_concentration"], no_stats)

    # C
    present("C1", "Регионы показа", any(_feat(g).get("regions") for g in live))
    present("C2", "Демография (пол, возраст)",
            any(_feat(g).get("age_span") is not None or len(_feat(g).get("sex") or []) == 1 for g in live))
    present("C3", "Интересы и поведение", any(_feat(g).get("has_interests") for g in live))
    present("C4", "Сообщества", any(_feat(g).get("has_communities") for g in live),
            _known_feature(live, "has_communities", "сообщества"))
    present("C5", "Пользовательские аудитории", any(g["segment_ids"] for g in live))
    present("C6", "Ключевые фразы", any(_feat(g).get("has_keywords") for g in live),
            _known_feature(live, "has_keywords", "ключевые фразы"))
    kw = [g for g in live if _feat(g).get("has_keywords")]
    kw_gate = ("na", "ключевые фразы не используются") if not kw else OK
    by_rules("C7", "Минус-фразы", ["no_minus_phrases"], kw_gate if kw_gate[0] else _known_feature(kw, "has_minus", "минус-фразы"))
    by_rules("C8", "Период поиска 7–12 дней", ["kw_period"], kw_gate if kw_gate[0] else _known_feature(kw, "kw_period", "период поиска"))
    by_rules("C9", "Расширение списка ключевых фраз", ["kw_expand_off"],
             kw_gate if kw_gate[0] else _known_feature(kw, "kw_expand", "расширение ключевых фраз"))
    by_rules("C10", "Минус-аудитории", ["no_exclusions"], _known_feature(live, "has_exclusions", "исключения аудиторий"))
    by_rules("C11", "Таргетинг не слишком широкий", ["too_broad"], _known_feature(live, "keys", "ключи таргетинга"))
    by_rules("C12", "Нет внутренней конкуренции", ["duplicate_targeting", "creative_overlap"])
    lal = [s for s in ds["segments"] if s.get("lal")]
    lal_gate = ("na", "LAL-сегментов нет") if not lal else (
        OK if any(s.get("size") is not None for s in lal) else _unknown("размер LAL-сегмента"))
    by_rules("C13", "LAL от качественных баз", ["lal_small_source"], lal_gate)
    by_rules("C14", "Базы обновляются и используются", ["stale_list", "unused_segments"])
    by_rules("C15", "Автоматические места размещения", ["manual_placements"],
             _known_feature(live, "auto_placements", "места размещения"))
    adj_known = any(_feat(g).get(k) is not None for g in live for k in ("device_adjustments", "demo_adjustments"))
    present("C16", "Корректировки по устройствам, полу и возрасту",
            any(_feat(g).get("device_adjustments") or _feat(g).get("demo_adjustments") for g in live),
            OK if adj_known else _unknown("корректировки ставок"))

    # D
    by_rules("D1", "Дневной лимит не ниже 300–500 ₽", ["low_budget"])
    by_rules("D2", "Стратегия по объёму данных", ["cap_too_early"], no_stats)
    by_rules("D3", "Автостратегии хватает конверсий", ["learning_starved"], no_stats)
    by_rules("D4", "Группы не остановлены по причинам VK", ["blocking_issues"])

    # E
    content_gate = no_banners if no_banners[0] else (
        OK if any(b.get("content_ids") for b in banners) else _unknown("контент объявлений"))
    classified = any(b.get("ratios") or b.get("has_video") for b in banners)
    covered = _banner_groups(ds)
    partial = any(g["id"] not in covered for g in live)
    by_rules("E1", "Все соотношения сторон 1:1, 4:5, 16:9, 9:16", ["missing_ratios"],
             _first(content_gate, OK if classified else ("unknown", "форматы объявлений не определены")))
    by_rules("E2", "Видео используется", ["no_video"],
             _first(content_gate, ("unknown", "объявления собраны не по всем активным группам") if partial else OK))
    by_rules("E3", "Креативов в группе 2–5", ["banners_per_group", "no_active_banners"], no_banners)
    by_rules("E4", "Свежие креативы в топ-группах", ["stale_creatives"],
             _first(no_banners, ("unknown", "дата создания известна не у всех объявлений")
                    if any(b.get("age_days") is None for b in banners) else OK))
    if content_gate[0]:
        add("E5", "Тестировались разные форматы", *content_gate)
    else:
        sigs = {(tuple(b.get("ratios") or []), bool(b.get("has_video"))) for b in banners if b.get("content_ids")}
        add("E5", "Тестировались разные форматы", "pass" if len(sigs) > 1 else "fail",
            "" if len(sigs) > 1 else "все объявления одного формата")
    by_rules("E6", "Призыв к действию в тексте", ["no_cta_text"],
             no_banners if no_banners[0] else _known_banner(banners, "text", "текст объявления"))
    by_rules("E7", "Конкретная кнопка", ["generic_button"],
             no_banners if no_banners[0] else _known_banner(banners, "cta", "кнопка объявления"))
    by_rules("E8", "Нет отклонённых объявлений", ["rejected_banners"], no_banners)
    by_rules("E9", "Модерация не затянулась", ["moderation_stuck"], no_banners)

    # F
    by_rules("F1", "CTR не ниже 0,3 %", ["low_ctr"], no_stats)
    by_rules("F2", "Нет CPC-выбросов", ["cpc_outlier"], no_stats)
    by_rules("F3", "Нет выгорания (частота)", ["burnout"], no_stats)
    long_enough = ds["period"]["days"] >= 30
    by_rules("F4", "Динамика CPA за 7 / 30 дней", ["cpa_trend"],
             no_stats if no_stats[0] else (OK if long_enough else ("unknown", "период аудита короче 30 дней")))
    early = sum(1 for g in live if _run_days(ds, g) < th["min_run_days"]) + \
        sum(1 for b in banners if b.get("age_days") is not None and b["age_days"] < th["min_run_days"])
    add("F5", "Решения не раньше 7 дней работы", "na",
        f"рано судить: {early} групп и объявлений моложе 7 дней" if early else "все работают 7+ дней")
    by_rules("F6", "Расход приносит конверсии", ["spend_no_conversions", "cpa_above_target"], no_stats)

    # G
    metrika_gate = OK if cov.get("metrika") else ("unknown", "Метрика не подключена")
    if cov.get("metrika") and not cov.get("metrika_utm", True):
        metrika_gate = ("unknown", "Метрика без UTM — склейка по группам невозможна")
    by_rules("G1", "Отказы трафика из VK в норме", ["high_bounce"], metrika_gate)
    by_rules("G2", "Клики доходят до сайта", ["click_loss"], metrika_gate)
    by_rules("G3", "Конверсии VK совпадают с целями Метрики", ["goals_without_vk_conversions"], metrika_gate)

    blocks = []
    for bid, title in BLOCKS:
        checks = [r for r in rows if r["block"] == bid]
        n = sum(1 for c in checks if c["status"] == "pass")
        m = sum(1 for c in checks if c["status"] in ("pass", "fail"))
        blocks.append({"id": bid, "title": title, "checks": checks, "score": [n, m]})
    return {"blocks": blocks, "total": [sum(b["score"][0] for b in blocks), sum(b["score"][1] for b in blocks)]}
