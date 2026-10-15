"""
diagnose.py — dataset.json + references/thresholds.json → findings.json.

Каждое правило — функция (ds, th, ctx) -> list[finding], зарегистрированная @rule.
Модель не считает: все числа находок — из dataset.json. Методичка меняет пороги
(thresholds.json) и правила здесь, контракт находки остаётся прежним.

    python -m scripts.diagnose --workspace <абс. путь> [--thresholds <path>]
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

try:
    from scripts._console import setup_console
    from scripts._workspace import SKILL_ROOT, resolve_workspace
except ImportError:  # запуск напрямую
    from _console import setup_console
    from _workspace import SKILL_ROOT, resolve_workspace

LEVEL_ORDER = {"red": 0, "orange": 1, "green": 2, "white": 3}
DEFAULT_THRESHOLDS = SKILL_ROOT / "references" / "thresholds.json"
RULES: list = []
ACCOUNT = {"id": None, "name": "кабинет"}
CHECK_OF = {
    "no_pixel": "A1", "ord_missing": "A2", "utm_missing": "A3",
    "no_retargeting": "B1.retargeting", "layer_missing": "B2", "layer_share": "B3", "mixed_plan": "B4",
    "fragmentation": "B5", "no_cbo": "B6", "naming": "B7", "autodelete_risk": "B8", "spend_concentration": "B9",
    "no_minus_phrases": "C7", "kw_period": "C8", "kw_expand_off": "C9", "no_exclusions": "C10",
    "too_broad": "C11", "duplicate_targeting": "C12", "creative_overlap": "C12", "lal_small_source": "C13",
    "stale_list": "C14", "unused_segments": "C14", "manual_placements": "C15",
    "low_budget": "D1", "cap_too_early": "D2", "learning_starved": "D3", "blocking_issues": "D4",
    "missing_ratios": "E1", "no_video": "E2", "banners_per_group": "E3", "no_active_banners": "E3",
    "stale_creatives": "E4", "no_cta_text": "E6", "generic_button": "E7", "rejected_banners": "E8",
    "moderation_stuck": "E9",
    "low_ctr": "F1", "cpc_outlier": "F2", "burnout": "F3", "cpa_trend": "F4",
    "spend_no_conversions": "F6", "cpa_above_target": "F6",
    "high_bounce": "G1", "click_loss": "G2", "goals_without_vk_conversions": "G3",
}


def rule(fn):
    RULES.append(fn)
    return fn


def load_thresholds(path=None) -> dict:
    return json.loads(Path(path or DEFAULT_THRESHOLDS).read_text(encoding="utf-8"))


def finding(rule_id, level, etype, ent, title, evidence, action, effect_rub=0.0, conf="high") -> dict:
    return {"rule": rule_id, "check": CHECK_OF.get(rule_id), "level": level,
            "entity": {"type": etype, "id": ent.get("id"), "name": ent.get("name", "")},
            "title": title, "evidence": evidence, "effect_rub": round(float(effect_rub), 2),
            "confidence": conf, "action": action}


def confidence(clicks: int, th) -> str:
    if clicks >= th["confidence_high_clicks"]:
        return "high"
    return "medium" if clicks >= th["confidence_medium_clicks"] else "low"


def _active(g, th) -> bool:
    return g["status"] in th["active_statuses"]


def _live(ds):
    return [g for g in ds["groups"] if g["status"] != "deleted"]


def _measured(ds):
    """Живые группы, по которым статистика получена: «нет данных» не равно «ноль»."""
    return [g for g in _live(ds) if not g.get("stats_missing")]


def _banner_groups(ds) -> set:
    """Группы, у которых объявления реально собраны. Старый dataset без banner_groups — все живые."""
    cov = ds["coverage"]
    if not cov.get("banners"):
        return set()
    if "banner_groups" in cov:
        return set(cov["banner_groups"])
    return {g["id"] for g in _live(ds)}


def _is_auto(g, th) -> bool:
    return g["autobidding_mode"] in th["auto_bidding_modes"]


def _run_days(ds, g) -> int:
    rows = g["daily"]
    return sum(1 for r in rows if r["shows"] > 0) if rows else ds["period"]["days"]


def reference_cpa(ds, th):
    if ds.get("target_cpa"):
        return float(ds["target_cpa"]), "target"
    cpas = [g["stats"]["cpa"] for g in _measured(ds)
            if g["stats"]["cpa"] is not None and g["stats"]["conversions"] >= th["min_conversions_for_cpa"]]
    if cpas:
        return round(float(statistics.median(cpas)), 2), "median"
    return None, "none"


def _banners_of(ds, g):
    ids = set(g["banner_ids"])
    return [b for b in ds["banners"] if b["id"] in ids]


def _feat(g) -> dict:
    return g.get("features") or {}


def _layer(g):
    return g.get("layer")  # None — слой не определён (targetings не получены)


def _active_banners(ds, g, th) -> list:
    return [b for b in _banners_of(ds, g) if b["status"] in th["active_statuses"]]


def _date(s):
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def _reject_action(b, th) -> str:
    reasons = json.dumps(b.get("moderation_reasons") or "", ensure_ascii=False).lower()
    if any(m in reasons for m in th["document_reason_markers"]):
        return "Загрузить документы в кабинете: Настройки → Документы для модерации — объявление запустится само"
    return ("Исправить по причине отказа и отправить на перемодерацию" if b["can_remoderate"]
            else "Исправить и создать заново: перемодерация недоступна")


# ---------- гигиена ----------

@rule
def no_active_banners(ds, th, ctx):
    covered = _banner_groups(ds)
    out = []
    for g in _live(ds):
        if not _active(g, th) or g["id"] not in covered:
            continue
        active = [b for b in _banners_of(ds, g) if b["status"] in th["active_statuses"]]
        if not active or "NO_BANNERS_WITH_ACTIVE_STATUS" in g["issues"]:
            out.append(finding("no_active_banners", "orange", "group", g,
                               "Группа включена, но крутить нечего — нет активных объявлений",
                               {"banners_total": len(g["banner_ids"]), "banners_active": len(active)},
                               "Добавить или включить объявления — либо выключить группу"))
    return out


@rule
def rejected_banners(ds, th, ctx):
    return [finding("rejected_banners", "orange", "banner", b, "Объявление отклонено модерацией",
                    {"moderation_status": b["moderation_status"], "reasons": b["moderation_reasons"],
                     "can_remoderate": b["can_remoderate"]},
                    _reject_action(b, th))
            for b in ds["banners"]
            if b["status"] != "deleted" and b["moderation_status"] in th["rejected_moderation_statuses"]]


@rule
def blocking_issues(ds, th, ctx):
    out = []
    for g in _live(ds):
        codes = [c for c in g["issues"] if c in th["blocking_issue_codes"]]
        if _active(g, th) and codes:
            out.append(finding("blocking_issues", "orange", "group", g, "VK сообщает, почему группа не крутится",
                               {"issues": codes}, "Устранить причину: " + ", ".join(codes)))
    return out


@rule
def autodelete_risk(ds, th, ctx):
    if not ds["coverage"]["stats"]:  # статистики не собирали — «нет показов» было бы выдумкой
        return []
    out = []
    end = date.fromisoformat(ds["period"]["to"])
    for g in _measured(ds):
        if not g.get("created") or g["stats"]["shows"]:
            continue
        age = (end - datetime.strptime(g["created"][:10], "%Y-%m-%d").date()).days
        if age >= th["autodelete_warn_days"]:
            out.append(finding("autodelete_risk", "white", "group", g,
                               "Нет показов за период, группа старая — VK удаляет группы без показов за 180 дней",
                               {"age_days": age, "shows": 0},
                               "Решить: перезапустить или осознанно выключить"))
    return out


@rule
def banners_per_group(ds, th, ctx):
    covered = _banner_groups(ds)
    out = []
    for g in _live(ds):
        if not _active(g, th) or g["id"] not in covered:
            continue
        n = len([b for b in _banners_of(ds, g) if b["status"] in th["active_statuses"]])
        if n < th["min_active_banners_per_group"] and n > 0:
            out.append(finding("banners_per_group", "white", "group", g,
                               "В группе одно объявление — алгоритму не из чего выбирать",
                               {"active_banners": n}, "Добавить варианты креатива: норма 2–5 в группе"))
        elif n > th["max_active_banners_per_group"]:
            out.append(finding("banners_per_group", "white", "group", g,
                               "Слишком много объявлений — бюджет размазан, выводы по каждому слабые",
                               {"active_banners": n}, "Оставить лучшие по результату"))
    return out


@rule
def low_budget(ds, th, ctx):
    out = []
    for g in _live(ds):
        b = g["budget_day_rub"]
        if not _active(g, th) or not b:  # None или 0 — дневного лимита нет, сравнивать не с чем
            continue
        if b < th["min_daily_budget_rub"]:
            out.append(finding("low_budget", "orange", "group", g, "Дневной бюджет ниже минимума VK",
                               {"budget_day_rub": b, "minimum_rub": th["min_daily_budget_rub"]},
                               f"Поднять дневной бюджет до {th['min_daily_budget_rub']} ₽ и выше"))
        elif _is_auto(g, th) and ctx["ref_cpa"] is not None and \
                b < ctx["ref_cpa"] * th["learning_budget_cpa_multiplier"]:
            need = ctx["ref_cpa"] * th["learning_budget_cpa_multiplier"]
            out.append(finding("low_budget", "orange", "group", g,
                               "Бюджета не хватает на обучение автостратегии",
                               {"budget_day_rub": b, "needed_rub": need, "ref_source": ctx["ref_source"]},
                               f"Дневной бюджет не ниже {need:.0f} ₽ (≥ {th['learning_budget_cpa_multiplier']} × CPA)"))
        elif b < th["recommended_daily_budget_rub"]:
            out.append(finding("low_budget", "white", "group", g, "Дневной бюджет на нижней границе",
                               {"budget_day_rub": b, "recommended_rub": th["recommended_daily_budget_rub"]},
                               f"Держать не ниже {th['recommended_daily_budget_rub']} ₽ в день, чтобы избежать резкого слива"))
    return out


@rule
def duplicate_targeting(ds, th, ctx):
    by_key = defaultdict(list)
    for g in _live(ds):
        if _active(g, th):
            by_key[(g["objective"], g["targeting_key"])].append(g)
    out = []
    for gs in by_key.values():
        if len(gs) > 1:
            out.append(finding("duplicate_targeting", "orange", "group", gs[0],
                               "Одинаковые таргетинги у активных групп — они конкурируют между собой",
                               {"groups": [g["id"] for g in gs]},
                               "Объединить группы или развести аудитории"))
    return out


@rule
def unused_segments(ds, th, ctx):
    used = {i for g in _live(ds) for i in g["segment_ids"]}
    return [finding("unused_segments", "white", "segment", s, "Сегмент не используется ни в одной группе",
                    {}, "Удалить или запустить на него группу")
            for s in ds["segments"] if s["id"] not in used]


@rule
def no_pixel(ds, th, ctx):
    if ds["pixels"] or not ds["coverage"]["pixels"]:  # пустой список при загруженном запросе — факт
        return []
    return [finding("no_pixel", "orange", "group", g, "Цель «конверсии на сайте», а пикселя в кабинете нет",
                    {"objective": g["objective"]}, "Установить пиксель VK и настроить события")
            for g in _live(ds) if _active(g, th) and g["objective"] in th["pixel_objectives"]]


# ---------- v1: техника и структура ----------

def _site_url(u: str, th) -> bool:
    if not u.startswith("http"):
        return False
    rest = re.sub(r"^https?://", "", u)
    host = re.split(r"[/?#]", rest, maxsplit=1)[0]   # путь, запрос, фрагмент
    host = host.rsplit("@", 1)[-1].split(":", 1)[0].lower()  # учётные данные и порт
    return not any(host == h or host.endswith("." + h) for h in th["internal_hosts"])


@rule
def ord_missing(ds, th, ctx):
    active = [b for b in ds["banners"] if b["status"] in th["active_statuses"]]
    if not active or all(b.get("erid") is None for b in active):  # поле не получено — «не проверено»
        return []
    bad = [b for b in active if b.get("erid") == ""]
    if not bad:
        return []
    return [finding("ord_missing", "orange", "account", ACCOUNT, "Объявления без маркировки ОРД (erid)",
                    {"banners_without_erid": len(bad), "banners_active": len(active),
                     "examples": [b["id"] for b in bad[:5]]},
                    "Включить автоматическую маркировку в кабинете и проверить данные рекламодателя")]


@rule
def utm_missing(ds, th, ctx):
    manual = {g["id"] for g in ds["groups"] if g.get("auto_utm") is False}  # None — не получено, молчим
    site = [b for b in ds["banners"] if b["status"] in th["active_statuses"] and b["group_id"] in manual
            and any(_site_url(u, th) for u in b.get("urls") or [])]
    bad = [b for b in site if not any("utm_" in u for u in b.get("urls") or [])]
    if not bad:
        return []
    return [finding("utm_missing", "orange", "account", ACCOUNT, "Ссылки на сайт без UTM-меток",
                    {"without_utm": len(bad), "site_banners": len(site), "examples": [b["id"] for b in bad[:5]]},
                    "Разметить все ссылки UTM — иначе не понять, какие объявления приводят целевой трафик")]


@rule
def no_retargeting(ds, th, ctx):
    live = [g for g in _live(ds) if _active(g, th) and _layer(g) is not None]
    if not live or any(_layer(g) == "warm" for g in live):
        return []
    return [finding("no_retargeting", "white", "account", ACCOUNT, "В кабинете нет ретаргетинга",
                    {"active_groups": len(live)},
                    "Добавить группы на посетителей сайта, вовлечённых в сообщество и досмотревших видео")]


@rule
def layer_missing(ds, th, ctx):
    live = [g for g in _live(ds) if _active(g, th) and _layer(g) is not None]
    if not live:
        return []
    have = {_layer(g) for g in live}
    missing = [x for x in ("lal", "cold") if x not in have]
    if not missing:
        return []
    names = {"lal": "похожие (LAL)", "cold": "холодный"}
    return [finding("layer_missing", "white", "account", ACCOUNT,
                    "Не хватает слоя аудиторий: " + ", ".join(names[m] for m in missing),
                    {"missing_layers": missing},
                    "Добавить слой: LAL на клиентов и лиды или холодный тест на интересах и контексте")]


@rule
def layer_share(ds, th, ctx):
    gs = [g for g in _measured(ds) if g["stats"]["spent"] > 0 and _layer(g) is not None]
    total = sum(g["stats"]["spent"] for g in gs)
    if not total:
        return []
    by = defaultdict(float)
    for g in gs:
        by[_layer(g)] += g["stats"]["spent"]
    month = total / ds["period"]["days"] * 30
    if month < th["small_budget_month_rub"]:
        target = th["layer_shares_small"]
        actual = {"warm_lal": (by["warm"] + by["lal"]) / total, "cold": by["cold"] / total}
    else:
        target = th["layer_shares"]
        actual = {k: by[k] / total for k in target}
    if all(abs(actual[k] - target[k]) <= th["layer_share_tolerance"] for k in target):
        return []
    return [finding("layer_share", "white", "account", ACCOUNT, "Доли бюджета по слоям далеки от рекомендованных",
                    {"actual": {k: round(v, 3) for k, v in actual.items()}, "target": target,
                     "month_spend_rub": round(month, 2)},
                    "Перераспределить бюджет между тёплым, LAL и холодным слоями")]


@rule
def mixed_plan(ds, th, ctx):
    by_plan = defaultdict(set)
    for g in _live(ds):
        if _active(g, th) and g.get("ad_plan_id") is not None and _layer(g) is not None:
            by_plan[g["ad_plan_id"]].add(_layer(g))
    plans = {p["id"]: p for p in ds["plans"]}
    return [finding("mixed_plan", "white", "plan", plans.get(pid, {"id": pid, "name": ""}),
                    "В одной кампании смешаны разные слои аудиторий", {"layers": sorted(layers)},
                    "Разнести: один сегмент — одна кампания")
            for pid, layers in by_plan.items() if len(layers) > 1]


@rule
def fragmentation(ds, th, ctx):
    small = [g for g in _live(ds) if _active(g, th) and g["budget_day_rub"]
             and g["budget_day_rub"] <= th["fragment_budget_rub"]]
    if len(small) < th["fragment_min_groups"]:
        return []
    return [finding("fragmentation", "orange", "account", ACCOUNT,
                    "Кабинет раздроблен на мелкие группы — алгоритму не хватает конверсий для обучения",
                    {"small_groups": len(small), "budget_max_rub": th["fragment_budget_rub"]},
                    "Укрупнить аудитории и объединить группы")]


@rule
def no_cbo(ds, th, ctx):
    count = defaultdict(int)
    for g in _live(ds):
        if _active(g, th):
            count[g.get("ad_plan_id")] += 1
    return [finding("no_cbo", "white", "plan", p, "Бюджет на уровне кампании (CBO) не включён",
                    {"groups": count[p["id"]]},
                    "Включить бюджет кампании — алгоритм сам распределит деньги между группами")
            for p in ds["plans"] if p.get("has_budget") is False and count[p["id"]] >= 2]


@rule
def naming(ds, th, ctx):
    pats = [re.compile(p, re.I) for p in th["default_name_patterns"]]
    names = ([p["name"] for p in ds["plans"] if p.get("status") != "deleted"]
             + [g["name"] for g in _live(ds)]
             + [b["name"] for b in ds["banners"] if b["status"] != "deleted"])
    names = [n.strip() for n in names if n and n.strip()]  # пустые названия не считаем ни плохими, ни в общем числе
    if not names:
        return []
    bad = [n for n in names if any(p.fullmatch(n) for p in pats)]
    share = len(bad) / len(names)
    if share < th["default_name_share"]:
        return []
    return [finding("naming", "white", "account", ACCOUNT, "Названия не говорят, что внутри",
                    {"default_names": len(bad), "total": len(names), "share": round(share, 3),
                     "examples": bad[:5]},
                    "Называть по схеме сегмент_гео_пол-возраст_устройство (например, «конкур_Кар_Ж20-45_моб»)")]


# ---------- v1: таргетинг и стратегия ----------

def _kw_groups(ds, th):
    return [g for g in _live(ds) if _active(g, th) and _feat(g).get("has_keywords")]


@rule
def no_minus_phrases(ds, th, ctx):
    return [finding("no_minus_phrases", "white", "group", g, "Ключевые фразы без минус-фраз", {},
                    "Добавить минус-фразы: нецелевые запросы съедают бюджет")
            for g in _kw_groups(ds, th) if _feat(g).get("has_minus") is False]


@rule
def kw_period(ds, th, ctx):
    lo, hi = th["kw_period_days"]
    return [finding("kw_period", "white", "group", g, f"Период поиска ключевых фраз вне {lo}–{hi} дней",
                    {"period_days": _feat(g)["kw_period"]}, f"Поставить период {lo}–{hi} дней")
            for g in _kw_groups(ds, th)
            if isinstance(_feat(g).get("kw_period"), int) and not lo <= _feat(g)["kw_period"] <= hi]


@rule
def kw_expand_off(ds, th, ctx):
    return [finding("kw_expand_off", "green", "group", g, "Не включено «Расширить список ключевых фраз»", {},
                    "Включить в блоке «Аудитории» группы: VK добавит близкие запросы и расширит охват")
            for g in _kw_groups(ds, th) if _feat(g).get("kw_expand") is False]


@rule
def no_exclusions(ds, th, ctx):
    return [finding("no_exclusions", "orange", "group", g,
                    "Нет минус-аудиторий: реклама показывается тем, кто уже купил", {"objective": g["objective"]},
                    "Исключить текущих клиентов и тех, кто уже совершил конверсию")
            for g in _live(ds) if _active(g, th) and g["objective"] in th["conversion_objectives"]
            and _feat(g).get("has_exclusions") is False]


@rule
def too_broad(ds, th, ctx):
    # Решение по сырым ключам таргетинга: неизвестное — молчим, а не считаем «широко».
    basic = {"geo", "local_geo", "age", "sex", "fulltime", "pads"}
    out = []
    for g in _live(ds):
        if not _active(g, th) or _layer(g) is None:  # targetings не получены — широта неизвестна
            continue
        f = _feat(g)
        keys = f.get("keys")
        if keys is None:
            continue
        narrowed = bool(f.get("has_interests") or f.get("has_keywords") or f.get("has_communities")
                        or g["segment_ids"])
        if any(k not in basic for k in keys) and not narrowed:
            continue  # неизвестный ключ может быть сужением, которое мы не читаем
        geo_keys = [k for k in ("geo", "local_geo") if k in keys]
        regions = f.get("regions") or []
        if geo_keys and not regions:
            continue  # формат геотаргетинга не распознан
        if "age" in keys and f.get("age_span") is None:
            continue  # формат возраста не распознан
        country = not geo_keys or set(regions) <= set(th["country_region_ids"])
        broad_age = "age" not in keys or f["age_span"] >= th["broad_age_span"]
        if country and broad_age and not narrowed:
            out.append(finding("too_broad", "orange", "group", g,
                               "Слишком широкий таргетинг: вся страна, широкий возраст, без интересов",
                               {"regions": regions, "age_span": f.get("age_span")},
                               "Сузить: интересы, ключевые фразы, сообщества или сегменты"))
    return out


@rule
def creative_overlap(ds, th, ctx):
    groups = {g["id"]: g for g in _live(ds) if _active(g, th)}
    by_content = defaultdict(set)
    for b in ds["banners"]:
        if b["status"] in th["active_statuses"] and b["group_id"] in groups:
            for cid in b.get("content_ids") or []:
                by_content[cid].add(b["group_id"])
    seen, out = set(), []
    for gids in by_content.values():
        gids = sorted(gids, key=str)
        if len(gids) < 2 or tuple(gids) in seen:
            continue
        shared = set.intersection(*(set(groups[i]["segment_ids"]) for i in gids))
        if shared:
            seen.add(tuple(gids))
            out.append(finding("creative_overlap", "orange", "group", groups[gids[0]],
                               "Один креатив на пересекающиеся аудитории — группы конкурируют",
                               {"groups": gids, "shared_segments": sorted(shared, key=str)},
                               "Развести аудитории или креативы, исключить пересечения"))
    return out


@rule
def lal_small_source(ds, th, ctx):
    return [finding("lal_small_source", "orange", "segment", s, "LAL собран на слишком маленькой базе",
                    {"segment_size": s["size"], "minimum": th["lal_min_source"]},
                    f"Проверить исходную базу LAL: нужна база от {th['lal_min_source']} целевых действий",
                    conf="low")
            for s in ds["segments"] if s.get("lal") and isinstance(s.get("size"), int)
            and s["size"] < th["lal_min_source"]]


@rule
def stale_list(ds, th, ctx):
    end = date.fromisoformat(ds["period"]["to"])
    out = []
    for lst in ds["users_lists"]:
        d = _date(lst.get("updated"))
        if d and (end - d).days > th["list_stale_days"]:
            out.append(finding("stale_list", "white", "list", lst, "База давно не обновлялась",
                               {"days_since_update": (end - d).days},
                               "Обновить базу или перейти на динамический ретаргетинг"))
    return out


@rule
def manual_placements(ds, th, ctx):
    return [finding("manual_placements", "white", "group", g, "Места размещения выбраны вручную", {},
                    "Включить автоматический выбор площадок — система покажет рекламу там, где она эффективнее")
            for g in _live(ds) if _active(g, th) and _feat(g).get("auto_placements") is False]


@rule
def cap_too_early(ds, th, ctx):
    out = []
    for g in _measured(ds):
        if not _active(g, th) or not g.get("max_price_rub") or g["objective"] not in th["conversion_objectives"]:
            continue
        w7 = (g.get("windows") or {}).get("7")
        if w7 is None:  # окно 7 дней не получено — без подмены периодом целиком
            continue
        conv7 = w7["conversions"]
        if conv7 < th["learning_min_conversions_per_week"]:
            out.append(finding("cap_too_early", "orange", "group", g,
                               "«Предельная цена» включена до накопления статистики",
                               {"max_price_rub": g["max_price_rub"], "conversions_7d": conv7,
                                "needed": th["learning_min_conversions_per_week"]},
                               "Вернуть «Минимальную цену», пока не будет 15+ конверсий за неделю"))
    return out


# ---------- эффективность ----------

@rule
def spend_no_conversions(ds, th, ctx):
    ref = ctx["ref_cpa"]
    limit = ref * th["no_conv_spend_cpa_multiplier"] if ref else th["no_conv_min_spend_rub"]
    out = []
    for g in _measured(ds):
        s = g["stats"]
        if s["conversions"] == 0 and s["spent"] >= limit:
            out.append(finding("spend_no_conversions", "red", "group", g, "Расход без конверсий",
                               {"spent": s["spent"], "clicks": s["clicks"], "limit_rub": round(limit, 2),
                                "ref_source": ctx["ref_source"]},
                               "Остановить или пересобрать: аудитория, креатив, посадочная, событие пикселя",
                               effect_rub=s["spent"], conf=confidence(s["clicks"], th)))
    return out


@rule
def cpa_above_target(ds, th, ctx):
    ref = ctx["ref_cpa"]
    if not ref:
        return []
    out = []
    for g in _measured(ds):
        s = g["stats"]
        if s["conversions"] >= th["min_conversions_for_cpa"] and s["cpa"] > ref * th["cpa_over_ratio"]:
            over = s["spent"] - s["conversions"] * ref
            out.append(finding("cpa_above_target", "red", "group", g, "CPA заметно выше опорного",
                               {"cpa": s["cpa"], "ref_cpa": ref, "ref_source": ctx["ref_source"],
                                "conversions": s["conversions"]},
                               "Сузить или сменить аудиторию, заменить слабые креативы, снизить ставку",
                               effect_rub=over, conf=confidence(s["clicks"], th)))
    return out


@rule
def low_ctr(ds, th, ctx):
    live_ids = {g["id"] for g in _live(ds)}
    with_stats = [b for b in ds["banners"]
                  if b["stats"] and b["status"] != "deleted" and b["group_id"] in live_ids
                  and b["group_id"] in _banner_groups(ds)]
    banner_level = {b["group_id"] for b in with_stats}
    entities = [("banner", b, b["stats"]) for b in with_stats]
    entities += [("group", g, g["stats"]) for g in _measured(ds) if g["id"] not in banner_level]
    return [finding("low_ctr", "orange", etype, ent, "Низкий CTR при достаточных показах",
                    {"ctr_pct": s["ctr_pct"], "shows": s["shows"], "threshold_pct": th["low_ctr_pct"]},
                    "Заменить креатив или проверить, той ли аудитории он показывается",
                    effect_rub=s["spent"], conf=confidence(s["clicks"], th))
            for etype, ent, s in entities
            if s["shows"] >= th["low_ctr_min_shows"] and s["ctr_pct"] < th["low_ctr_pct"]]


def _ctr(rows):
    shows = sum(r["shows"] for r in rows)
    return sum(r["clicks"] for r in rows) / shows * 100 if shows else 0.0


@rule
def burnout(ds, th, ctx):
    out = []
    for g in _measured(ds):
        rows = g["daily"]
        freq = g["stats"]["frequency"]
        if freq and freq > th["burnout_red_frequency"] and g["stats"]["shows"] >= th["burnout_min_shows"]:
            out.append(finding("burnout", "red", "group", g, "Частота показа выше 5 — аудитория выжжена",
                               {"frequency": freq}, "Срочно обновить креативы или расширить аудиторию",
                               effect_rub=g["stats"]["spent"], conf=confidence(g["stats"]["clicks"], th)))
            continue
        if len(rows) < 4 or not freq or freq < th["burnout_min_frequency"] \
                or g["stats"]["shows"] < th["burnout_min_shows"]:
            continue
        rows = sorted(rows, key=lambda r: r["date"] or "")
        half = len(rows) // 2
        first, second = _ctr(rows[:half]), _ctr(rows[half:])
        if first and (first - second) / first >= th["burnout_ctr_drop"]:
            out.append(finding("burnout", "orange", "group", g, "Выгорание: CTR падает, частота высокая",
                               {"ctr_first_half_pct": round(first, 3), "ctr_second_half_pct": round(second, 3),
                                "frequency": freq},
                               "Обновить креативы или расширить аудиторию",
                               effect_rub=sum(r["spent"] for r in rows[half:])))
    return out


@rule
def learning_starved(ds, th, ctx):
    out = []
    for g in _measured(ds):
        s = g["stats"]
        if not _active(g, th) or not _is_auto(g, th) or s["spent"] <= 0:
            continue
        weeks = max(_run_days(ds, g) / 7, 1)
        per_week = s["conversions"] / weeks
        if per_week < th["learning_min_conversions_per_week"]:
            out.append(finding("learning_starved", "orange", "group", g,
                               "Автостратегии не хватает конверсий для обучения",
                               {"conversions_per_week": round(per_week, 1),
                                "needed": th["learning_min_conversions_per_week"]},
                               "Объединить группы, расширить аудиторию или оптимизировать на более частое событие",
                               conf=confidence(s["clicks"], th)))
    return out


@rule
def spend_concentration(ds, th, ctx):
    live = [g for g in _measured(ds) if g["stats"]["spent"] > 0]
    total = sum(g["stats"]["spent"] for g in live)
    if len(live) < th["concentration_min_groups"] or not total:
        return []
    top = max(live, key=lambda g: g["stats"]["spent"])
    share = top["stats"]["spent"] / total
    if share < th["concentration_share"]:
        return []
    return [finding("spend_concentration", "white", "group", top, "Почти весь расход в одной группе",
                    {"share": round(share, 3), "groups_with_spend": len(live)},
                    "Проверить, не голодают ли остальные группы, и осознанно перераспределить бюджет")]


@rule
def scale_leader(ds, th, ctx):
    ref = ctx["ref_cpa"]
    if not ref:
        return []
    out = []
    for g in _measured(ds):
        s, b = g["stats"], g["budget_day_rub"]
        if not _active(g, th) or not b or not g["daily"] or s["conversions"] < th["min_conversions_for_cpa"]:
            continue
        spend_days = [r["spent"] for r in g["daily"] if r["spent"] > 0]
        if not spend_days:
            continue
        avg = sum(spend_days) / len(spend_days)
        if s["cpa"] <= ref * th["leader_cpa_ratio"] and avg >= b * th["leader_budget_use"]:
            out.append(finding("scale_leader", "green", "group", g, "Лидер упирается в бюджет",
                               {"cpa": s["cpa"], "ref_cpa": ref, "avg_daily_spent": round(avg, 2),
                                "budget_day_rub": b},
                               f"Поднять дневной бюджет на {int(th['leader_growth_share'] * 100)} % и следить за CPA",
                               effect_rub=avg * th["leader_growth_share"] * ds["period"]["days"],
                               conf=confidence(s["clicks"], th)))
    return out


# ---------- метрика ----------

def _with_metrika(ds, th):
    return [g for g in _measured(ds) if _active(g, th) and g.get("metrika")]


@rule
def high_bounce(ds, th, ctx):
    return [finding("high_bounce", "orange", "group", g, "Высокий отказ трафика из группы (Метрика)",
                    {"bounce_rate": g["metrika"]["bounce_rate"], "visits": g["metrika"]["visits"]},
                    "Проверить соответствие посадочной обещанию креатива и скорость загрузки",
                    effect_rub=g["stats"]["spent"] * g["metrika"]["bounce_rate"],
                    conf=confidence(g["metrika"]["visits"], th))
            for g in _with_metrika(ds, th)
            if g["metrika"]["visits"] >= th["metrika_min_visits"]
            and (g["metrika"]["bounce_rate"] or 0) >= th["high_bounce_rate"]]


@rule
def click_loss(ds, th, ctx):
    out = []
    for g in _with_metrika(ds, th):
        clicks, visits = g["stats"]["clicks"], g["metrika"]["visits"]
        if clicks >= th["click_loss_min_clicks"] and visits / clicks < th["click_loss_visit_ratio"]:
            out.append(finding("click_loss", "orange", "group", g, "Кликов в VK заметно больше, чем визитов в Метрике",
                               {"clicks": clicks, "visits": visits, "ratio": round(visits / clicks, 3)},
                               "Проверить скорость посадочной, редиректы и разметку UTM",
                               effect_rub=g["stats"]["spent"] * (1 - visits / clicks)))
    return out


@rule
def goals_without_vk_conversions(ds, th, ctx):
    return [finding("goals_without_vk_conversions", "orange", "group", g,
                    "Цели в Метрике есть, а конверсий в VK нет — пиксель или атрибуция",
                    {"metrika_goal_reaches": g["metrika"]["goal_reaches"], "vk_conversions": 0},
                    "Проверить пиксель VK и событие оптимизации группы")
            for g in _with_metrika(ds, th)
            if g["metrika"]["goal_reaches"] > 0 and g["stats"]["conversions"] == 0]


# ---------- запуск ----------

def diagnose(ds: dict, th: dict) -> dict:
    ref, source = reference_cpa(ds, th)
    ctx = {"ref_cpa": ref, "ref_source": source}
    out = []
    for fn in RULES:
        out.extend(fn(ds, th, ctx))
    out.sort(key=lambda f: (LEVEL_ORDER[f["level"]], -f["effect_rub"]))
    return {"ref_cpa": ref, "ref_source": source, "findings": out}


def main(argv=None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(description="dataset.json → findings.json")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--thresholds")
    args = parser.parse_args(argv)
    ws = resolve_workspace(args.workspace)
    if not (ws / "dataset.json").exists():
        print("нет dataset.json — сначала нормализация (python -m scripts.normalize ...)", file=sys.stderr)
        return 2
    ds = json.loads((ws / "dataset.json").read_text(encoding="utf-8"))
    result = diagnose(ds, load_thresholds(args.thresholds))
    (ws / "findings.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    counts = defaultdict(int)
    for f in result["findings"]:
        counts[f["level"]] += 1
    print(f"находок: {len(result['findings'])} ({dict(counts)}), опорный CPA: {result['ref_cpa']} "
          f"({result['ref_source']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
