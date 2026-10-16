"""
checks.py — чек-листы отчёта по блокам A–G (методика v1, формат агентского аудита).

Статусы: pass ✅, fail ❌, unknown — данных нет (поле не получено из API, путь A, нет Метрики),
na — неприменимо, ask ❓ — отсутствующую воронку (подписка / лид-форма / Senler) сначала уточняем у клиента,
potential 🟢 — клиент подтвердил потенциал роста. Всё кроме pass и fail не входит в знаменатель балла;
в «Ограничения» уходит только unknown.
Проверка с правилами: fail, если сработала хотя бы одна находка её правил.
Нет активных групп (или активных объявлений) — «na»: «нечего проверять: нет активных групп» («…объявлений»), а не
«поле не получено»; гейт «na» никогда не прячет сработавшую находку. Нет живых групп — общий балл не считается
(total None, idle: «кабинет не откручивает» или «нет живых групп — общий балл не считается»).
"""
from __future__ import annotations

from collections import defaultdict

try:
    from scripts.diagnose import (ASK_FUNNELS, FUNNEL_LABELS, _active, _banner_groups, _banners, _date, _feat, _layer,
                                  _live, _measured, _run_days, funnel_status, plural, utm_unconfirmed_groups,
                                  utm_unconfirmed_reason)
except ImportError:  # запуск напрямую
    from diagnose import (ASK_FUNNELS, FUNNEL_LABELS, _active, _banner_groups, _banners, _date, _feat, _layer, _live,
                          _measured, _run_days, funnel_status, plural, utm_unconfirmed_groups, utm_unconfirmed_reason)

BLOCKS = [("A", "Техническая настройка"), ("B", "Структура аккаунта"), ("C", "Таргетинг"),
          ("D", "Бюджет и стратегия"), ("E", "Объявления"), ("F", "Эффективность"),
          ("G", "Воронка после клика")]
FUNNELS = FUNNEL_LABELS
OK = (None, "")
NO_ACTIVE = ("na", "нечего проверять: нет активных групп")
NO_ACTIVE_BANNERS = ("na", "нечего проверять: нет активных объявлений")
IDLE = "кабинет не откручивает: нет живых групп"
IDLE_SPENT = "нет живых групп — общий балл не считается"   # расход был, но у архивных групп
NO_SPEND = ("na", "нет расхода за период")


def _unknown(label):
    return ("unknown", f"поле «{label}» не получено из API — проверится живым прогоном")


def _known_banner(banners, field, label):
    return OK if any(b.get(field) not in (None, [], "") for b in banners) else _unknown(label)


def _first(*gates):
    """Первый сработавший гейт (статус не None) или OK."""
    return next((g for g in gates if g[0]), OK)


def _known_feature(groups, key, label):
    return OK if any(_feat(g).get(key) is not None for g in groups) else _unknown(label)


def run_checks(ds: dict, th: dict, findings: list, evaluated: dict | None = None,
               answers: dict | None = None) -> dict:
    fired = defaultdict(list)
    for f in findings:
        fired[f["rule"]].append(f)
    rows: list = []
    cov = ds["coverage"]
    all_live = _live(ds)
    live = [g for g in all_live if _active(g, th)]
    banners = [b for b in _banners(ds) if b["status"] in th["active_statuses"]]
    # Второй живой прогон: все группы удалены — объявлений и статистики не собирали, и это не «нет данных».
    no_live = NO_ACTIVE if not all_live else OK          # правила по всем живым группам (и паузным)
    no_active = NO_ACTIVE if not live else OK            # правила по активным группам
    no_banners = ("unknown", "объявления не собирались") if not cov.get("banners") else OK
    no_stats = ("unknown", "статистика не собрана") if not cov.get("stats", True) else OK
    banners_gate = NO_ACTIVE_BANNERS if not banners else OK   # объявления собраны, активных нет
    measured = _measured(ds)
    # расход живых групп известен и равен 0: правила по расходу проверять не на чем (как B3)
    no_spend = NO_SPEND if ds.get("zero_spend") or (measured and not sum(g["stats"]["spent"] for g in measured)) \
        else OK

    def add(cid, title, status, reason=""):
        rows.append({"id": cid, "block": cid[0], "title": title, "status": status, "reason": reason})

    def by_rules(cid, title, rules, gate=OK):
        hits = [f for r in rules for f in fired.get(r, [])]
        if gate[0] and not (gate[0] == "na" and hits):   # «нечего проверять» не прячет реальную находку
            return add(cid, title, gate[0], gate[1])
        if hits:
            return add(cid, title, "fail", f"находок: {len(hits)}")
        if evaluated is not None and sum(evaluated.get(r, 0) for r in rules) == 0:
            return add(cid, title, "na", "нечего проверять: ни одна сущность не подошла под условия проверки")
        add(cid, title, "pass")

    def present(cid, title, ok, gate=OK):
        if gate[0]:
            return add(cid, title, gate[0], gate[1])
        add(cid, title, "pass" if ok else "fail", "" if ok else "не используется")

    layer_gate = _first(no_active, OK if any(_layer(g) is not None for g in live) else (
        "unknown", "слои аудиторий не определены — таргетинги не получены"))

    covered = _banner_groups(ds)
    partial_gate = ("unknown", "объявления собраны не по всем группам") if any(
        g["id"] not in covered for g in live) else OK
    banner_base = _first(no_active, no_banners, partial_gate, banners_gate)   # свойства активных объявлений
    keys_gate = _known_feature(live, "keys", "ключи таргетинга")
    targeting_gate = _first(no_active, ("unknown", "таргетинги не получены") if keys_gate[0] else OK)

    # A
    if no_active[0]:
        pixel_gate = no_active
    elif not cov.get("pixels", True):
        pixel_gate = ("unknown", "пиксели не собирались")
    elif not any(g["objective"] in th["pixel_objectives"] for g in live):
        pixel_gate = ("na", "нет групп с целью «конверсии на сайте»")
    else:
        pixel_gate = OK
    by_rules("A1", "Пиксель установлен", ["no_pixel"], pixel_gate)
    by_rules("A2", "Креативы промаркированы (erid)", ["ord_missing"],
             _first(no_active, no_banners, banners_gate, OK if any(b.get("erid") is not None for b in banners)
                    else _unknown("erid / маркировка ОРД")))
    # Авто-UTM без ключа enable_utm — «не подтверждено» (None): реальная находка ❌ остаётся, иначе ✅ не ставим.
    a3_gate = _first(no_active, no_banners, banners_gate, _known_banner(banners, "urls", "ссылки объявлений"))
    unconfirmed = utm_unconfirmed_groups(ds, th)
    if not a3_gate[0] and not fired.get("utm_missing") and unconfirmed:
        a3_gate = ("unknown", utm_unconfirmed_reason(len(unconfirmed)))
    by_rules("A3", "UTM-метки на объявлениях", ["utm_missing"], a3_gate)

    # B
    funnels = funnel_status(ds, th)
    answers = answers or {}
    for key, title in FUNNELS:
        status, reason = funnels[key]
        if status == "fail" and key in ASK_FUNNELS:
            a = answers.get(key)
            if a and a["verdict"] == "na":
                status, reason = "na", ("не применимо: " + a["answer"] if a["answer"] else "не применимо")
            elif a:
                status, reason = "potential", ("потенциал роста: " + a["answer"] if a["answer"] else "потенциал роста")
            else:
                status, reason = "ask", "воронка не используется — уточнить у клиента"
        if status == "na" and no_active[0]:
            reason = no_active[1]
        add(f"B1.{key}", f"Воронка: {title}", status, reason)
    by_rules("B2", "Есть тёплый, LAL и холодный слои", ["layer_missing", "no_retargeting"], layer_gate)
    spend = sum(g["stats"]["spent"] for g in all_live)
    by_rules("B3", "Доли бюджета по слоям", ["layer_share"],   # правило — по всем живым группам с расходом
             _first(no_live, OK if any(_layer(g) is not None for g in all_live)
                    else ("unknown", "слои аудиторий не определены — таргетинги не получены"),
                    OK if spend else NO_SPEND))
    by_rules("B4", "1 сегмент = 1 кампания", ["mixed_plan"], layer_gate)
    by_rules("B5", "Нет дробления на мелкие группы", ["fragmentation"], no_active)
    plans_known = any(p.get("has_budget") is not None for p in ds["plans"])
    by_rules("B6", "Бюджет на уровне кампании (CBO)", ["no_cbo"],
             _first(no_active, OK if plans_known else _unknown("бюджет кампании")))
    by_rules("B7", "Говорящие названия", ["naming"], no_live)
    by_rules("B8", "Нет заброшенных групп", ["autodelete_risk"], _first(no_live, no_stats))
    by_rules("B9", "Расход распределён по группам", ["spend_concentration"], _first(no_live, no_stats))

    # C
    present("C1", "Регионы показа",
            any(_feat(g).get("regions") or "local_geo" in (_feat(g).get("keys") or []) for g in live),  # радиус — тоже гео
            targeting_gate)
    present("C2", "Демография (пол, возраст)",
            any(_feat(g).get("age_span") is not None or len(_feat(g).get("sex") or []) == 1 for g in live),
            targeting_gate)
    present("C3", "Интересы и поведение", any(_feat(g).get("has_interests") for g in live), targeting_gate)
    present("C4", "Сообщества", any(_feat(g).get("has_communities") for g in live),
            _first(no_active, _known_feature(live, "has_communities", "сообщества")))
    present("C5", "Пользовательские аудитории", any(g["segment_ids"] for g in live), targeting_gate)
    present("C6", "Ключевые фразы", any(_feat(g).get("has_keywords") for g in live),
            _first(no_active, _known_feature(live, "has_keywords", "ключевые фразы")))
    kw = [g for g in live if _feat(g).get("has_keywords")]
    kw_gate = _first(no_active, ("na", "ключевые фразы не используются") if not kw else OK)
    by_rules("C7", "Минус-фразы", ["no_minus_phrases"], kw_gate if kw_gate[0] else _known_feature(kw, "has_minus", "минус-фразы"))
    by_rules("C8", "Период поиска 7–12 дней", ["kw_period"], kw_gate if kw_gate[0] else _known_feature(kw, "kw_period", "период поиска"))
    by_rules("C9", "Расширение списка ключевых фраз", ["kw_expand_off"],
             kw_gate if kw_gate[0] else _known_feature(kw, "kw_expand", "расширение ключевых фраз"))
    by_rules("C10", "Минус-аудитории", ["no_exclusions"],
             _first(no_active, _known_feature(live, "has_exclusions", "исключения аудиторий")))
    by_rules("C11", "Таргетинг не слишком широкий", ["too_broad"],
             _first(no_active, _known_feature(live, "keys", "ключи таргетинга")))
    by_rules("C12", "Нет внутренней конкуренции", ["duplicate_targeting", "creative_overlap"], no_active)
    lal = [s for s in ds["segments"] if s.get("lal")]
    lal_gate = ("na", "LAL-сегментов нет") if not lal else (
        OK if any(s.get("size") is not None for s in lal) else _unknown("размер LAL-сегмента"))
    by_rules("C13", "LAL от качественных баз", ["lal_small_source"], lal_gate)
    lists = ds.get("users_lists") or []
    if no_active[0]:   # база важна, только если стоит в таргетинге активной группы
        c14_gate = no_active
    elif not cov.get("users_lists", True):
        c14_gate = ("unknown", "нет прав: нет доступа к базам клиентов" if cov.get("users_lists_no_access")
                    else "списки баз клиентов не получены")
    elif not lists:
        c14_gate = ("na", "пользовательских баз нет")
    elif not any(_date(u.get("updated")) for u in lists):
        c14_gate = ("unknown", "даты обновления баз не получены")
    elif not ds.get("segment_relations_known"):
        c14_gate = ("unknown", "не удалось связать базы с таргетингом")
    else:
        c14_gate = OK
    by_rules("C14", "Базы обновляются", ["stale_list"], c14_gate)
    by_rules("C15", "Автоматические места размещения", ["manual_placements"],
             _first(no_active, _known_feature(live, "auto_placements", "места размещения")))
    adj_known = any(_feat(g).get(k) is not None for g in live for k in ("device_adjustments", "demo_adjustments"))
    present("C16", "Корректировки по устройствам, полу и возрасту",
            any(_feat(g).get("device_adjustments") or _feat(g).get("demo_adjustments") for g in live),
            _first(no_active, OK if adj_known else _unknown("корректировки ставок")))

    # D
    by_rules("D1", "Дневной лимит не ниже 300–500 ₽", ["low_budget"], no_active)
    by_rules("D2", "Стратегия по объёму данных", ["cap_too_early"], _first(no_active, no_stats))
    by_rules("D3", "Автостратегии хватает конверсий", ["learning_starved"], _first(no_active, no_stats))
    by_rules("D4", "Группы не остановлены по причинам VK", ["blocking_issues"], no_active)

    # E
    content_gate = OK if any(b.get("content_ids") for b in banners) else _unknown("контент объявлений")
    classified = any(b.get("ratios") or b.get("has_video") for b in banners)
    e1_gate = _first(banner_base, content_gate, OK if classified else ("unknown", "форматы объявлений не определены"))
    unread = sum(1 for b in banners if b.get("ratios_unknown"))
    if not e1_gate[0] and unread and not fired.get("missing_ratios"):
        e1_gate = ("unknown", f"соотношение сторон не читается из роли контента у {unread} "
                   f"{plural(unread, 'объявления', 'объявлений', 'объявлений')}")
    by_rules("E1", "Все соотношения сторон 1:1, 4:5, 16:9, 9:16", ["missing_ratios"], e1_gate)
    by_rules("E2", "Видео используется", ["no_video"], _first(banner_base, content_gate))
    by_rules("E3", "Креативов в группе 2–5", ["banners_per_group", "no_active_banners"],
             _first(no_active, no_banners, partial_gate))   # нет активных объявлений — это и есть находка 🟠
    by_rules("E4", "Свежие креативы в топ-группах", ["stale_creatives"],
             _first(banner_base, ("unknown", "дата создания известна не у всех объявлений")
                    if any(b.get("age_days") is None for b in banners) else OK))
    classified_banners = [b for b in banners if b.get("ratios") or b.get("has_video") or b.get("ratios_unknown")]
    e5_gate = _first(banner_base, content_gate,
                     OK if len(classified_banners) >= 2 else ("unknown", "форматы определены меньше чем у двух объявлений"))
    if e5_gate[0]:
        add("E5", "Тестировались разные форматы", *e5_gate)
    else:
        # Подпись — только по читаемым соотношениям; у объявления с нечитаемой ролью известно лишь «видео или нет».
        sigs = {(tuple(b.get("ratios") or []), bool(b.get("has_video")))
                for b in classified_banners if not b.get("ratios_unknown")}
        unk_video = any(b.get("ratios_unknown") and b.get("has_video") for b in classified_banners)
        if len(sigs) > 1 or (unk_video and any(not video for _, video in sigs)):
            add("E5", "Тестировались разные форматы", "pass")
        elif any(b.get("ratios_unknown") for b in classified_banners):
            add("E5", "Тестировались разные форматы", "unknown",
                "соотношение сторон не читается из роли контента — одинаковы ли форматы, не определить")
        else:
            add("E5", "Тестировались разные форматы", "fail", "все объявления одного формата")
    by_rules("E6", "Призыв к действию в тексте", ["no_cta_text"],
             _first(banner_base, _known_banner(banners, "text", "текст объявления")))
    by_rules("E7", "Конкретная кнопка", ["generic_button"],
             _first(banner_base, OK if any(b.get("cta") for b in banners)
                    else ("unknown", "кнопка не задана в объявлениях (call_to_action пустой)")))
    by_rules("E8", "Нет отклонённых объявлений", ["rejected_banners"], _first(no_live, no_banners))
    by_rules("E9", "Модерация не затянулась", ["moderation_stuck"], _first(no_live, no_banners))

    # F
    by_rules("F1", "CTR не ниже 0,3 %", ["low_ctr"], _first(no_live, no_stats))
    by_rules("F2", "Нет CPC-выбросов", ["cpc_outlier"], _first(no_live, no_stats))
    # те же группы, что судит правило burnout: все живые с полученной статистикой (и паузные)
    eligible = [g for g in measured if _run_days(ds, g) >= th["min_run_days"]
                and g["stats"]["shows"] >= th["burnout_min_shows"]]
    if no_live[0] or no_stats[0] or fired.get("burnout"):
        by_rules("F3", "Нет выгорания (частота)", ["burnout"], _first(no_live, no_stats))  # находка не прячется
    elif any(g.get("stats_missing") for g in all_live):
        add("F3", "Нет выгорания (частота)", "unknown", "статистика получена не по всем группам")
    elif not eligible:
        add("F3", "Нет выгорания (частота)", "na", "нечего проверять: нет групп с достаточными показами")
    elif any(g["stats"].get("frequency") is None for g in eligible):
        add("F3", "Нет выгорания (частота)", "unknown",
            "частота за период не определена: охват и частота доступны только за всё время жизни группы")
    else:
        by_rules("F3", "Нет выгорания (частота)", ["burnout"])
    long_enough = ds["period"]["days"] >= 30
    by_rules("F4", "Динамика CPA за 7 / 30 дней", ["cpa_trend"],
             _first(no_active, no_stats, OK if long_enough else ("unknown", "период аудита короче 30 дней")))
    young_g = sum(1 for g in live if _run_days(ds, g) < th["min_run_days"])
    young_b = sum(1 for b in banners if b.get("age_days") is not None and b["age_days"] < th["min_run_days"])
    young = [f"{n} {plural(n, *forms)}" for n, forms in ((young_g, ("группа", "группы", "групп")),
                                                        (young_b, ("объявление", "объявления", "объявлений"))) if n]
    if no_active[0]:
        f5_reason = no_active[1]
    elif ds.get("zero_spend") or not any(g["stats"]["shows"] for g in live):
        f5_reason = "нет показов за период"   # «моложе 7 дней» по нулям было бы неправдой
    else:
        f5_reason = "рано судить: моложе 7 дней — " + " и ".join(young) if young else "все работают 7+ дней"
    add("F5", "Решения не раньше 7 дней работы", "na", f5_reason)
    by_rules("F6", "Расход приносит конверсии", ["spend_no_conversions", "cpa_above_target"],
             _first(no_live, no_stats, no_spend))

    # G
    metrika_gate = OK if cov.get("metrika") else ("unknown", "Метрика не подключена")
    if cov.get("metrika") and not cov.get("metrika_utm", True):
        metrika_gate = ("unknown", "Метрика без UTM — склейка по группам невозможна")
    metrika_gate = _first(no_active, metrika_gate)
    by_rules("G1", "Отказы трафика из VK в норме", ["high_bounce"], metrika_gate)
    by_rules("G2", "Клики доходят до сайта", ["click_loss"], metrika_gate)
    by_rules("G3", "Конверсии VK совпадают с целями Метрики", ["goals_without_vk_conversions"], metrika_gate)

    blocks = []
    for bid, title in BLOCKS:
        checks = [r for r in rows if r["block"] == bid]
        n = sum(1 for c in checks if c["status"] == "pass")
        m = sum(1 for c in checks if c["status"] in ("pass", "fail"))
        blocks.append({"id": bid, "title": title, "checks": checks, "score": [n, m]})
    out = {"blocks": blocks, "total": [sum(b["score"][0] for b in blocks), sum(b["score"][1] for b in blocks)]}
    if not all_live:   # нет живых групп — общий балл ничего не значит
        out["total"] = None
        out["idle"] = IDLE_SPENT if ds["totals"]["spent"] else IDLE
    return out
