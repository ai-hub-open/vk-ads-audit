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
    from scripts.features import is_active_group, is_live_group, parse_dt, plural
except ImportError:  # запуск напрямую
    from _console import setup_console
    from _workspace import SKILL_ROOT, resolve_workspace
    from features import is_active_group, is_live_group, parse_dt, plural

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
    "stale_list": "C14", "manual_placements": "C15",
    "low_budget": "D1", "cap_too_early": "D2", "learning_starved": "D3", "blocking_issues": "D4",
    "missing_ratios": "E1", "no_video": "E2", "banners_per_group": "E3", "no_active_banners": "E3",
    "stale_creatives": "E4", "no_cta_text": "E6", "generic_button": "E7", "rejected_banners": "E8",
    "moderation_stuck": "E9",
    "low_ctr": "F1", "cpc_outlier": "F2", "burnout": "F3", "cpa_trend": "F4",
    "spend_no_conversions": "F6", "cpa_above_target": "F6",
    "high_bounce": "G1", "click_loss": "G2", "goals_without_vk_conversions": "G3",
}


def utm_unconfirmed_reason(n: int) -> str:
    return f"авто-UTM у {n} {plural(n, 'группы', 'групп', 'групп')} не подтверждена API — проверьте в кабинете"


def utm_unconfirmed_text(n: int) -> str:
    """Строка «Ограничений»: та же причина, что у A3 «unknown», с заглавной буквы."""
    r = utm_unconfirmed_reason(n)
    return r[0].upper() + r[1:] + "."


def _rub_text(v) -> str:
    return f"{v:,.0f} ₽".replace(",", " ")


def rule(fn):
    RULES.append(fn)
    return fn


def load_thresholds(path=None) -> dict:
    return json.loads(Path(path or DEFAULT_THRESHOLDS).read_text(encoding="utf-8"))


def _seen(ctx, rule_id, n=1):
    """Правило реально сравнило n сущностей с порогом (сработало или нет); молчаливые пропуски не считаются."""
    ctx["evaluated"][rule_id] += n


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
    """Группа реально откручивает: активна и её кампания не остановлена (features.is_active_group)."""
    return is_active_group(g, th["active_statuses"])


def _live(ds):
    return [g for g in ds["groups"] if is_live_group(g)]


def _banners(ds) -> list:
    """Объявления живых групп: объявления удалённых и архивных групп правилам и чек-листу не видны."""
    dead = {g["id"] for g in ds["groups"] if not is_live_group(g)}
    return [b for b in ds["banners"] if b["group_id"] not in dead]


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


def _is_manual(g, th) -> bool:
    """Ручная ставка (second_price_mean: ставка в price, фактический CPM — подтверждено 16.10.2026), не автостратегия."""
    return g["autobidding_mode"] in th.get("manual_bidding_modes", [])


def bidding_label(mode, th):
    """Режим ставки по-человечески; неизвестный режим — None (сырое значение API в отчёт не идёт)."""
    return th.get("bidding_mode_labels", {}).get(mode) if mode else None


def _run_days(ds, g) -> int:
    rows = g["daily"]
    return sum(1 for r in rows if r["shows"] > 0) if rows else ds["period"]["days"]


def _early(ds, g, th) -> bool:
    return _run_days(ds, g) < th["min_run_days"]


def _perf_entities(ds, th) -> list:
    """Объявления (если собраны и старше 7 дней) и группы (если по ним нет объявлений и работают 7+ дней)."""
    live = {g["id"]: g for g in _measured(ds)}
    covered = _banner_groups(ds)
    banners = [b for b in ds["banners"] if b["stats"] and b["status"] != "deleted" and b["group_id"] in live
               and b["group_id"] in covered]
    banner_level = {b["group_id"] for b in banners}
    out = [("banner", b, b["stats"]) for b in banners
           if b.get("age_days") is None or b["age_days"] >= th["min_run_days"]]
    out += [("group", g, g["stats"]) for g in live.values()
            if g["id"] not in banner_level and not _early(ds, g, th)]
    return out


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


def _guessed(g) -> bool:
    """Слой по догадке: тип сегмента по связям (relations) не определён — «не LAL — тёплый», низкая надёжность."""
    return bool(g.get("layer_guessed")) and g.get("layer") is not None


GUESSED_REASON = "тип сегментов не подтверждён связями (relations) — слой определён по догадке «не LAL — тёплый»"


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
        _seen(ctx, "no_active_banners")
        if not active or "NO_BANNERS_WITH_ACTIVE_STATUS" in g["issues"]:
            out.append(finding("no_active_banners", "orange", "group", g,
                               "Группа включена, но крутить нечего — нет активных объявлений",
                               {"banners_total": len(g["banner_ids"]), "banners_active": len(active)},
                               "Добавить или включить объявления — либо выключить группу"))
    return out


@rule
def rejected_banners(ds, th, ctx):
    out = []
    for b in _banners(ds):
        if b["status"] == "deleted":
            continue
        _seen(ctx, "rejected_banners")
        if b["moderation_status"] in th["rejected_moderation_statuses"]:
            out.append(finding("rejected_banners", "orange", "banner", b, "Объявление отклонено модерацией",
                               {"moderation_status": b["moderation_status"], "reasons": b["moderation_reasons"],
                                "can_remoderate": b["can_remoderate"]},
                               _reject_action(b, th)))
    return out


@rule
def blocking_issues(ds, th, ctx):
    out = []
    for g in _live(ds):
        codes = [c for c in g["issues"] if c in th["blocking_issue_codes"]]
        if _active(g, th):
            _seen(ctx, "blocking_issues")
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
        if not g.get("created"):
            continue
        _seen(ctx, "autodelete_risk")
        if g["stats"]["shows"]:
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
        _seen(ctx, "banners_per_group")
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
        _seen(ctx, "low_budget")
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
        if _active(g, th) and _layer(g) is not None:   # targetings не получены — ключ «{}» ничего не значит
            _seen(ctx, "duplicate_targeting")
            by_key[(g["objective"], g["targeting_key"])].append(g)
    out = []
    for gs in by_key.values():
        if len(gs) > 1:
            out.append(finding("duplicate_targeting", "orange", "group", gs[0],
                               "Одинаковые таргетинги у активных групп — они конкурируют между собой",
                               {"groups": [g["id"] for g in gs]},
                               "Объединить группы или развести аудитории"))
    return out


def _segments_of(groups) -> set:
    # исключённый сегмент (минус-аудитория) тоже «используется»
    return {i for g in groups for i in list(g["segment_ids"]) + list(g.get("excluded_segment_ids") or [])}


@rule
def unused_segments(ds, th, ctx):
    """Одна сводная карточка на кабинет (второй живой прогон: 24 карточки по сегменту — шум)."""
    used = _segments_of(_live(ds))
    in_dead = _segments_of(g for g in ds["groups"] if not is_live_group(g))
    _seen(ctx, "unused_segments", len(ds["segments"]))
    unused = [s["id"] for s in ds["segments"] if s["id"] not in used]
    if not unused:
        return []
    n = len(unused)
    title = (f"{n} {plural(n, 'сегмент', 'сегмента', 'сегментов')} не "
             f"{plural(n, 'используется', 'используются', 'используются')} ни в одной живой группе")
    return [finding("unused_segments", "white", "account", ACCOUNT, title,
                    {"segments_unused": n, "only_in_deleted_or_archived_groups": sum(1 for i in unused if i in in_dead),
                     "examples": unused[:5]},
                    "Удалить лишние сегменты или запустить на них группы")]


@rule
def no_pixel(ds, th, ctx):
    if not ds["coverage"]["pixels"]:  # пустой список при загруженном запросе — факт
        return []
    out = []
    for g in _live(ds):
        if not _active(g, th) or g["objective"] not in th["pixel_objectives"]:
            continue
        _seen(ctx, "no_pixel")
        if not ds["pixels"]:
            out.append(finding("no_pixel", "orange", "group", g,
                               "Цель «конверсии на сайте», а пикселя в кабинете нет",
                               {"objective": g["objective"]}, "Установить пиксель VK и настроить события"))
    return out


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
    # проверяем только объявления, у которых поле получено: erid None — «не проверено»
    active = [b for b in _banners(ds) if b["status"] in th["active_statuses"] and b.get("erid") is not None]
    if not active:
        return []
    _seen(ctx, "ord_missing", len(active))
    bad = [b for b in active if b.get("erid") == ""]
    if not bad:
        return []
    return [finding("ord_missing", "orange", "account", ACCOUNT, "Объявления без маркировки ОРД (erid)",
                    {"banners_without_erid": len(bad), "banners_active": len(active),
                     "examples": [b["id"] for b in bad[:5]]},
                    "Включить автоматическую маркировку в кабинете и проверить данные рекламодателя")]


def _has_utm(b) -> bool:
    return any("utm_" in u for u in b.get("urls") or [])


def _site_banners(ds, th) -> list:
    """Активные объявления живых групп со ссылкой на сайт (не на VK и мессенджеры)."""
    return [b for b in _banners(ds) if b["status"] in th["active_statuses"]
            and any(_site_url(u, th) for u in b.get("urls") or [])]


def utm_unconfirmed_groups(ds, th) -> list:
    """id групп, где авто-UTM не подтверждена API (auto_utm None), а объявление на сайт без utm_ в ссылке:
    есть ли метки, не определить — проверить в кабинете."""
    auto = {g["id"]: g.get("auto_utm") for g in _live(ds) if _active(g, th)}   # паузные не в счёт (A3 — «—»)
    return sorted({b["group_id"] for b in _site_banners(ds, th)
                   if b["group_id"] in auto and auto[b["group_id"]] is None and not _has_utm(b)}, key=str)


@rule
def utm_missing(ds, th, ctx):
    auto = {g["id"]: g.get("auto_utm") for g in _live(ds)}
    site = [b for b in _site_banners(ds, th) if b["group_id"] in auto]
    # utm_ в ссылке — метки есть; авто-UTM включена — их ставит VK; None без utm_ — не подтверждено, молчим
    checked = [b for b in site if _has_utm(b) or auto[b["group_id"]] is not None]
    _seen(ctx, "utm_missing", len(checked))
    bad = [b for b in checked if auto[b["group_id"]] is False and not _has_utm(b)]
    if not bad:
        return []
    return [finding("utm_missing", "orange", "account", ACCOUNT, "Ссылки на сайт без UTM-меток",
                    {"without_utm": len(bad), "site_banners": len(checked), "examples": [b["id"] for b in bad[:5]]},
                    "Разметить все ссылки UTM — иначе не понять, какие объявления приводят целевой трафик")]


@rule
def no_retargeting(ds, th, ctx):
    live = [g for g in _live(ds) if _active(g, th) and _layer(g) is not None]
    if not live:
        return []
    _seen(ctx, "no_retargeting")
    if any(_layer(g) == "warm" for g in live):
        return []
    return [finding("no_retargeting", "white", "account", ACCOUNT, "В кабинете нет ретаргетинга",
                    {"active_groups": len(live)},
                    "Добавить группы на посетителей сайта, вовлечённых в сообщество и досмотревших видео")]


@rule
def layer_missing(ds, th, ctx):
    live = [g for g in _live(ds) if _active(g, th) and _layer(g) is not None]
    if not live:
        return []
    _seen(ctx, "layer_missing")
    have = {_layer(g) for g in live}
    missing = [x for x in ("lal", "cold") if x not in have]
    if not missing:
        return []
    names = {"lal": "похожие (LAL)", "cold": "холодный"}
    f = finding("layer_missing", "white", "account", ACCOUNT,
                "Не хватает слоя аудиторий: " + ", ".join(names[m] for m in missing),
                {"missing_layers": missing},
                "Добавить слой: LAL на клиентов и лиды или холодный тест на интересах и контексте")
    if "cold" in missing and any(_guessed(g) for g in live):   # «тёплая» по догадке группа может быть холодной
        f.update(confidence="low", tentative=True)
        f["title"] += " (слои части групп определены по догадке)"
    return [f]


@rule
def layer_share(ds, th, ctx):
    gs = [g for g in _measured(ds) if g["stats"]["spent"] > 0 and _layer(g) is not None]
    total = sum(g["stats"]["spent"] for g in gs)
    if not total:
        return []
    _seen(ctx, "layer_share")
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
    guessed = sum(g["stats"]["spent"] for g in gs if _guessed(g))
    f = finding("layer_share", "white", "account", ACCOUNT, "Доли бюджета по слоям далеки от рекомендованных",
                {"actual": {k: round(v, 3) for k, v in actual.items()}, "target": target,
                 "month_spend_rub": round(month, 2)},
                "Перераспределить бюджет между тёплым, LAL и холодным слоями")
    if guessed:   # как stale_list без связей: карточка низкой надёжности, B3 — «нет данных», а не ❌
        f.update(confidence="low", tentative=True)
        f["title"] += " (слои части групп определены по догадке)"
        f["evidence"]["guessed_share"] = round(guessed / total, 3)
    return [f]


@rule
def mixed_plan(ds, th, ctx):
    by_plan = defaultdict(set)
    guessed = set()
    for g in _live(ds):
        if _active(g, th) and g.get("ad_plan_id") is not None and _layer(g) is not None:
            by_plan[g["ad_plan_id"]].add(_layer(g))
            if _guessed(g):
                guessed.add(g["ad_plan_id"])
    plans = {p["id"]: p for p in ds["plans"]}
    _seen(ctx, "mixed_plan", len(by_plan))
    out = []
    for pid, layers in by_plan.items():
        if len(layers) < 2:
            continue
        f = finding("mixed_plan", "white", "plan", plans.get(pid, {"id": pid, "name": ""}),
                    "В одной кампании смешаны разные слои аудиторий", {"layers": sorted(layers)},
                    "Разнести: один сегмент — одна кампания")
        if pid in guessed:
            f.update(confidence="low", tentative=True)
            f["title"] += " (слои части групп определены по догадке)"
        out.append(f)
    return out


@rule
def fragmentation(ds, th, ctx):
    small = [g for g in _live(ds) if _active(g, th) and g["budget_day_rub"]
             and g["budget_day_rub"] <= th["fragment_budget_rub"]]
    if not any(_active(g, th) and g["budget_day_rub"] for g in _live(ds)):
        return []
    _seen(ctx, "fragmentation")
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
    _seen(ctx, "no_cbo", sum(1 for p in ds["plans"] if p.get("has_budget") is not None and count[p["id"]] >= 2))
    return [finding("no_cbo", "white", "plan", p, "Бюджет на уровне кампании (CBO) не включён",
                    {"groups": count[p["id"]]},
                    "Включить бюджет кампании — алгоритм сам распределит деньги между группами")
            for p in ds["plans"] if p.get("has_budget") is False and count[p["id"]] >= 2]


@rule
def naming(ds, th, ctx):
    pats = [re.compile(p, re.I) for p in th["default_name_patterns"]]
    live_plans = {g.get("ad_plan_id") for g in _live(ds)}   # кампании только с удалёнными группами — не в счёт
    names = ([p["name"] for p in ds["plans"] if p.get("status") != "deleted" and p.get("id") in live_plans]
             + [g["name"] for g in _live(ds)]
             + [b["name"] for b in _banners(ds) if b["status"] != "deleted"])
    names = [n.strip() for n in names if n and n.strip()]  # пустые названия не считаем ни плохими, ни в общем числе
    if not names:
        return []
    _seen(ctx, "naming")
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
    _seen(ctx, "no_minus_phrases", sum(1 for g in _kw_groups(ds, th) if _feat(g).get("has_minus") is not None))
    return [finding("no_minus_phrases", "white", "group", g, "Ключевые фразы без минус-фраз", {},
                    "Добавить минус-фразы: нецелевые запросы съедают бюджет")
            for g in _kw_groups(ds, th) if _feat(g).get("has_minus") is False]


@rule
def kw_period(ds, th, ctx):
    lo, hi = th["kw_period_days"]
    _seen(ctx, "kw_period", sum(1 for g in _kw_groups(ds, th) if isinstance(_feat(g).get("kw_period"), int)))
    return [finding("kw_period", "white", "group", g, f"Период поиска ключевых фраз вне {lo}–{hi} дней",
                    {"period_days": _feat(g)["kw_period"]}, f"Поставить период {lo}–{hi} дней")
            for g in _kw_groups(ds, th)
            if isinstance(_feat(g).get("kw_period"), int) and not lo <= _feat(g)["kw_period"] <= hi]


@rule
def kw_expand_off(ds, th, ctx):
    _seen(ctx, "kw_expand_off", sum(1 for g in _kw_groups(ds, th) if _feat(g).get("kw_expand") is not None))
    return [finding("kw_expand_off", "green", "group", g, "Не включено «Расширить список ключевых фраз»", {},
                    "Включить в блоке «Аудитории» группы: VK добавит близкие запросы и расширит охват")
            for g in _kw_groups(ds, th) if _feat(g).get("kw_expand") is False]


@rule
def no_exclusions(ds, th, ctx):
    _seen(ctx, "no_exclusions",
          sum(1 for g in _live(ds) if _active(g, th) and g["objective"] in th["conversion_objectives"]
              and _feat(g).get("has_exclusions") is not None))
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
        _seen(ctx, "too_broad")
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
    groups = {g["id"]: g for g in _live(ds) if _active(g, th) and _layer(g) is not None}  # без таргетингов — молчим
    by_content = defaultdict(set)
    for b in _banners(ds):
        if b["status"] in th["active_statuses"] and b["group_id"] in groups:
            for cid in b.get("content_ids") or []:
                by_content[cid].add(b["group_id"])
    _seen(ctx, "creative_overlap", len(by_content))
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
    _seen(ctx, "lal_small_source", sum(1 for s in ds["segments"] if s.get("lal") and isinstance(s.get("size"), int)))
    return [finding("lal_small_source", "orange", "segment", s, "LAL собран на слишком маленькой базе",
                    {"segment_size": s["size"], "minimum": th["lal_min_source"]},
                    f"Проверить исходную базу LAL: нужна база от {th['lal_min_source']} целевых действий",
                    conf="low")
            for s in ds["segments"] if s.get("lal") and isinstance(s.get("size"), int)
            and s["size"] < th["lal_min_source"]]


def _referenced_list_ids(ds, th):
    """id баз, на которые ссылаются сегменты живых активных групп (цель или исключение);
    None — связь «база → сегмент» в данных не получена."""
    active = [g for g in _live(ds) if _active(g, th)]
    if not active:   # нет активных групп — ни одна база ни в чьём таргетинге не стоит
        return set()
    if not ds.get("segment_relations_known"):
        return None
    used = {i for g in active
            for i in list(g["segment_ids"]) + list(g.get("excluded_segment_ids") or [])}
    return {lid for s in ds["segments"] if s["id"] in used for lid in (s.get("list_ids") or [])}


@rule
def stale_list(ds, th, ctx):
    end = date.fromisoformat(ds["period"]["to"])
    referenced = _referenced_list_ids(ds, th)
    out = []
    for lst in ds["users_lists"]:
        d = _date(lst.get("updated"))
        if not d or (referenced is not None and lst["id"] not in referenced):
            continue  # даты нет или база не в таргетинге — устаревание ничему не вредит
        _seen(ctx, "stale_list")
        if (end - d).days > th["list_stale_days"]:
            title = "База давно не обновлялась"
            if referenced is None:
                title += " (если база используется в таргетинге)"
            f = finding("stale_list", "white", "list", lst, title, {"days_since_update": (end - d).days},
                        "Обновить базу или перейти на динамический ретаргетинг",
                        conf="high" if referenced is not None else "low")
            if referenced is None:   # условная карточка: используется ли база, не известно — ❌ в C14 не ставит
                f["tentative"] = True
            out.append(f)
    return out


@rule
def manual_placements(ds, th, ctx):
    _seen(ctx, "manual_placements",
          sum(1 for g in _live(ds) if _active(g, th) and _feat(g).get("auto_placements") is not None))
    return [finding("manual_placements", "white", "group", g, "Места размещения выбраны вручную", {},
                    "Включить автоматический выбор площадок — система покажет рекламу там, где она эффективнее")
            for g in _live(ds) if _active(g, th) and _feat(g).get("auto_placements") is False]


@rule
def cap_too_early(ds, th, ctx):
    out = []
    for g in _measured(ds):
        if not _active(g, th) or not g.get("max_price_rub") or g["objective"] not in th["conversion_objectives"]:
            continue
        if _is_auto(g, th):  # при автостратегии «предельная цена» — не ручной кап
            continue
        if _is_manual(g, th):  # ручная ставка — другой режим, не «предельная цена»; на старте не ограничение (решение Лизы)
            continue
        w7 = (g.get("windows") or {}).get("7")
        if w7 is None:  # окно 7 дней не получено — без подмены периодом целиком
            continue
        _seen(ctx, "cap_too_early")
        conv7 = w7["conversions"]
        if conv7 < th["learning_min_conversions_per_week"]:
            out.append(finding("cap_too_early", "orange", "group", g,
                               "«Предельная цена» включена до накопления статистики",
                               {"max_price_rub": g["max_price_rub"], "conversions_7d": conv7,
                                "needed": th["learning_min_conversions_per_week"]},
                               "Вернуть «Минимальную цену», пока не будет 15+ конверсий за неделю"))
    return out


# ---------- v1: объявления ----------

def _content_groups(ds, th):
    covered = _banner_groups(ds)
    return [(g, _active_banners(ds, g, th)) for g in _live(ds) if _active(g, th) and g["id"] in covered]


@rule
def missing_ratios(ds, th, ctx):
    out = []
    for g, active in _content_groups(ds, th):
        if not active:   # активных объявлений нет — форматов нет вовсе, это находка no_active_banners, а не E1
            continue
        if any(not b.get("content_ids") or not (b.get("ratios") or b.get("has_video")) or b.get("ratios_unknown")
               for b in active):
            continue   # не все объявления с контентом и классифицированы (или соотношение не читается) — молчим
        _seen(ctx, "missing_ratios")
        have = set().union(*(set(b.get("ratios") or []) for b in active))
        missing = [r for r in th["required_ratios"] if r not in have]
        if missing:
            out.append(finding("missing_ratios", "orange", "group", g,
                               "Загружены не все форматы — часть площадок (Клипы, Истории) недоступна",
                               {"missing": missing, "present": sorted(have)},
                               "Добавить креативы в форматах: " + ", ".join(missing)))
    return out


@rule
def no_video(ds, th, ctx):
    covered = _banner_groups(ds)
    if any(g["id"] not in covered for g in _live(ds) if _active(g, th)):   # объявления собраны не по всем группам
        return []
    active = [b for g, bs in _content_groups(ds, th) for b in bs if b.get("content_ids")]
    if not active:
        return []
    _seen(ctx, "no_video")
    if any(b.get("has_video") for b in active):
        return []
    return [finding("no_video", "white", "account", ACCOUNT, "В кабинете нет видеообъявлений",
                    {"banners_with_content": len(active)},
                    "Добавить короткие видео (15–60 с, до 180 с) или автоматические видео из статики")]


def _top_groups(ds, th) -> list:
    gs = sorted([g for g in _measured(ds) if _active(g, th) and g["stats"]["spent"] > 0],
                key=lambda g: -g["stats"]["spent"])
    total, acc, out = sum(g["stats"]["spent"] for g in gs), 0.0, []
    for g in gs:
        out.append(g)
        acc += g["stats"]["spent"]
        if acc >= total * th["top_groups_spend_share"]:
            break
    return out


def edited_recently(b, ds, th) -> bool:
    """Объявление создано давно, но правилось внутри порога свежести (или после конца периода): VK не отдаёт дату
    смены креатива, поэтому «креатив старый» по нему утверждать нельзя (третий живой прогон)."""
    u = _date(b.get("updated"))
    end = date.fromisoformat(ds["period"]["to"])
    return (u is not None and b.get("age_days") is not None and b["age_days"] > th["creative_stale_days"]
            and (end - u).days <= th["creative_stale_days"])


def _stale_candidates(ds, th):
    """(группа, все активные объявления, честно датированные) для топ-групп с собранными объявлениями и
    известным возрастом каждого объявления."""
    covered = _banner_groups(ds)
    for g in _top_groups(ds, th):
        banners = _active_banners(ds, g, th)
        if g["id"] not in covered or not banners or any(b.get("age_days") is None for b in banners):
            continue   # возраст одного из объявлений не известен — свежесть группы не оценить
        yield g, banners, [b for b in banners if not edited_recently(b, ds, th)]


def stale_undetermined_groups(ds, th) -> list:
    """Топ-группы, где все активные объявления недавно правились: свежесть креатива не определить (E4 «unknown»)."""
    return [g["id"] for g, _, honest in _stale_candidates(ds, th) if not honest]


@rule
def stale_creatives(ds, th, ctx):
    out = []
    for g, banners, honest in _stale_candidates(ds, th):
        if not honest:   # все объявления правились недавно — дата смены креатива не видна, молчим
            continue
        _seen(ctx, "stale_creatives")
        newest = min(b["age_days"] for b in honest)   # находка — по честно старым; правленые — «нет данных»
        if newest > th["creative_stale_red_days"]:
            level = "orange"
        elif newest > th["creative_stale_days"]:
            level = "white"
        else:
            continue
        evidence = {"newest_creative_age_days": newest}
        if len(honest) < len(banners):
            evidence["edited_recently"] = len(banners) - len(honest)
        out.append(finding("stale_creatives", level, "group", g, "Креативы в топ-группе давно не обновлялись",
                           evidence, "Обновить угол подачи: новый сценарий, боль или оффер (не только дизайн)"))
    return out


@rule
def no_cta_text(ds, th, ctx):
    words = [w.lower() for w in th["cta_words"]]
    out = []
    for g, active in _content_groups(ds, th):
        _seen(ctx, "no_cta_text", sum(1 for b in active if (b.get("text") or "").strip()))
        bad = [b["id"] for b in active if (b.get("text") or "").strip()
               and not any(w in b["text"].lower() for w in words)]
        if bad:
            out.append(finding("no_cta_text", "white", "group", g, "В тексте объявлений нет призыва к действию",
                               {"banners": bad},
                               "Добавить призыв, совпадающий с посадочной: «Запишитесь», «Узнайте цену», «Получите расчёт»"))
    return out


@rule
def generic_button(ds, th, ctx):
    generic = {c.lower() for c in th["generic_cta"]}
    out = []
    for g, active in _content_groups(ds, th):
        _seen(ctx, "generic_button", sum(1 for b in active if b.get("cta")))
        bad = [b["id"] for b in active if (b.get("cta") or "").lower() in generic]
        if bad:
            out.append(finding("generic_button", "white", "group", g, "Кнопка «Подробнее» вместо конкретного действия",
                               {"banners": bad}, "Поставить конкретную кнопку: «Записаться», «Узнать цену», «Купить»"))
    return out


@rule
def moderation_stuck(ds, th, ctx):
    as_of = parse_dt(ds.get("as_of"))
    if not as_of:
        return []
    out = []
    for b in _banners(ds):
        stamps = [s for s in (parse_dt(b.get("created")), parse_dt(b.get("updated"))) if s]
        if b["status"] == "deleted" or b["moderation_status"] not in th["moderating_statuses"] or not stamps:
            continue
        _seen(ctx, "moderation_stuck")
        # мерим от последнего события: после правки модерация начинается заново
        since = max(stamps)
        hours = (as_of - since).total_seconds() / 3600
        norm = th["moderation_video_hours"] if b.get("has_video") else th["moderation_hours"]
        if hours > norm + th["moderation_tz_tolerance_hours"]:
            out.append(finding("moderation_stuck", "orange", "banner", b, "Модерация затянулась",
                               {"hours": round(hours, 1), "norm_hours": norm},
                               "Проверить, не нужны ли документы (Настройки → Документы для модерации), иначе — в поддержку"))
    return out


# ---------- эффективность ----------

@rule
def spend_no_conversions(ds, th, ctx):
    ref = ctx["ref_cpa"]
    limit = ref * th["no_conv_spend_cpa_multiplier"] if ref else th["no_conv_min_spend_rub"]
    out = []
    for g in _measured(ds):
        s = g["stats"]
        _seen(ctx, "spend_no_conversions")
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
        if s["conversions"] >= th["min_conversions_for_cpa"]:
            _seen(ctx, "cpa_above_target")
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
    _seen(ctx, "low_ctr", sum(1 for _, _, s in _perf_entities(ds, th) if s["shows"] >= th["low_ctr_min_shows"]))
    return [finding("low_ctr", "orange", etype, ent, "Низкий CTR при достаточных показах",
                    {"ctr_pct": s["ctr_pct"], "shows": s["shows"], "threshold_pct": th["low_ctr_pct"]},
                    "Заменить креатив или проверить, той ли аудитории он показывается",
                    effect_rub=s["spent"], conf=confidence(s["clicks"], th))
            for etype, ent, s in _perf_entities(ds, th)
            if s["shows"] >= th["low_ctr_min_shows"] and s["ctr_pct"] < th["low_ctr_pct"]]


def _ctr(rows):
    shows = sum(r["shows"] for r in rows)
    return sum(r["clicks"] for r in rows) / shows * 100 if shows else 0.0


@rule
def burnout(ds, th, ctx):
    out = []
    for g in _measured(ds):
        if _early(ds, g, th):
            continue
        rows = g["daily"]
        freq = g["stats"]["frequency"]
        if freq and g["stats"]["shows"] >= th["burnout_min_shows"]:
            _seen(ctx, "burnout")
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
def cpc_outlier(ds, th, ctx):
    avg = ds["totals"].get("cpc")
    if not avg:
        return []
    out = []
    for etype, ent, s in _perf_entities(ds, th):
        if s["clicks"] >= th["cpc_outlier_min_clicks"] and s["cpc"]:
            _seen(ctx, "cpc_outlier")
        if s["clicks"] >= th["cpc_outlier_min_clicks"] and s["cpc"] and s["cpc"] >= avg * th["cpc_outlier_ratio"] \
                and s["ctr_pct"] < th["ctr_norm_min_pct"]:
            out.append(finding("cpc_outlier", "orange", etype, ent, "CPC выше среднего по кабинету при низком CTR",
                               {"cpc": s["cpc"], "avg_cpc": avg, "ctr_pct": s["ctr_pct"]},
                               "Отключить или заменить креатив",
                               effect_rub=max(s["spent"] - s["clicks"] * avg, 0), conf=confidence(s["clicks"], th)))
    return out


@rule
def cpa_trend(ds, th, ctx):
    out = []
    for g in _measured(ds):
        if not _active(g, th) or _early(ds, g, th):
            continue
        w = g.get("windows") or {}
        w7, w30 = w.get("7"), w.get("30")
        if not w7 or not w30 or not w30["cpa"] or w30["conversions"] < th["min_conversions_for_cpa"]:
            continue
        if w7["conversions"] == 0:  # неделя без конверсий: ждали хотя бы одну, раз потрачено на CPA и больше
            if w7["spent"] >= w30["cpa"]:
                _seen(ctx, "cpa_trend")
                out.append(finding("cpa_trend", "red", "group", g, "За последнюю неделю нет конверсий",
                                   {"spent_7d": w7["spent"], "cpa_30d": w30["cpa"],
                                    "conversions_30d": w30["conversions"]},
                                   "Проверить пиксель и событие оптимизации, остановить слив или обновить креативы",
                                   effect_rub=w7["spent"], conf=confidence(w7["clicks"], th)))
            continue
        if not w7["cpa"] or w7["conversions"] < th["min_conversions_for_cpa"]:
            continue
        _seen(ctx, "cpa_trend")
        if w7["cpa"] >= w30["cpa"] * (1 + th["cpa_trend_rise"]):
            out.append(finding("cpa_trend", "orange", "group", g, "CPA за последнюю неделю заметно выше, чем за месяц",
                               {"cpa_7d": w7["cpa"], "cpa_30d": w30["cpa"], "frequency": g["stats"]["frequency"]},
                               "Проверить выгорание по частоте, обновить креативы или аудиторию",
                               effect_rub=max(w7["spent"] - w7["conversions"] * w30["cpa"], 0),
                               conf=confidence(w7["clicks"], th)))
    return out


@rule
def learning_starved(ds, th, ctx):
    out = []
    for g in _measured(ds):
        s = g["stats"]
        if not _active(g, th) or not _is_auto(g, th) or s["spent"] <= 0:
            continue
        if _early(ds, g, th):  # моложе 7 дней — обучение не оценивается
            continue
        _seen(ctx, "learning_starved")
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
    _seen(ctx, "spend_concentration")
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
        _seen(ctx, "scale_leader")
        avg = sum(spend_days) / len(spend_days)
        if s["cpa"] > ref * th["leader_cpa_ratio"] or avg < b * th["leader_budget_use"]:
            continue
        if g.get("changed_after_period") and b < avg:
            # Третий живой прогон: бюджет урезали в день сбора — «+50 %» и «упирается» были бы советом по чужим цифрам.
            out.append(finding("leader_budget_changed", "white", "group", g,
                               f"Бюджет менялся после периода: сейчас {_rub_text(b)} при расходе {_rub_text(avg)} "
                               "в день — проверьте актуальность",
                               {"cpa": s["cpa"], "ref_cpa": ref, "avg_daily_spent": round(avg, 2),
                                "budget_day_rub": b},
                               "Сверить текущий дневной бюджет с планом; решение о масштабировании — после сверки",
                               conf=confidence(s["clicks"], th)))
        else:
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
    _seen(ctx, "high_bounce",
          sum(1 for g in _with_metrika(ds, th) if g["metrika"]["visits"] >= th["metrika_min_visits"]))
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
        if clicks >= th["click_loss_min_clicks"]:
            _seen(ctx, "click_loss")
        if clicks >= th["click_loss_min_clicks"] and visits / clicks < th["click_loss_visit_ratio"]:
            out.append(finding("click_loss", "orange", "group", g, "Кликов в VK заметно больше, чем визитов в Метрике",
                               {"clicks": clicks, "visits": visits, "ratio": round(visits / clicks, 3)},
                               "Проверить скорость посадочной, редиректы и разметку UTM",
                               effect_rub=g["stats"]["spent"] * (1 - visits / clicks)))
    return out


@rule
def goals_without_vk_conversions(ds, th, ctx):
    _seen(ctx, "goals_without_vk_conversions", len(_with_metrika(ds, th)))
    return [finding("goals_without_vk_conversions", "orange", "group", g,
                    "Цели в Метрике есть, а конверсий в VK нет — пиксель или атрибуция",
                    {"metrika_goal_reaches": g["metrika"]["goal_reaches"], "vk_conversions": 0},
                    "Проверить пиксель VK и событие оптимизации группы")
            for g in _with_metrika(ds, th)
            if g["metrika"]["goal_reaches"] > 0 and g["stats"]["conversions"] == 0]


# ---------- воронки, о которых спрашиваем клиента ----------

FUNNEL_LABELS = [("subscription", "подписка на сообщество"), ("leadform", "лид-форма"),
                 ("retargeting", "ретаргетинг"), ("senler", "Senler"), ("site", "трафик на сайт")]
# Отсутствие этих воронок — не ошибка кабинета: сначала спрашиваем клиента (статус «ask»).
# Ретаргетинг и сайт сюда не входят — их отсутствие остаётся ❌.
ASK_FUNNELS = {
    "subscription": {
        "label": "подписка на сообщество",
        "question": "Почему не используете подписку на сообщество?",
        "why": "Подписка собирает аудиторию сообщества, которую потом можно использовать в таргетинге и ретаргете: "
               "аудитории подписчиков относятся к пользовательским аудиториям.",
    },
    "leadform": {
        "label": "лид-форма",
        "question": "Почему не используете лид-формы?",
        "why": "Лид-форма — самостоятельная воронка: чем меньше в ней полей, тем выше конверсия, а с уточняющим "
               "вопросом и быстрым обзвоном она даёт заявки без перехода на сайт.",
    },
    "senler": {
        "label": "Senler",
        "question": "Почему не используете Senler?",
        "why": "Senler — рассылки и чат-боты в VK; через него можно собрать подписчиков и довести их до заявки "
               "сообщениями.",
    },
}
ANSWER_VERDICTS = ("na", "potential")


def funnel_status(ds: dict, th: dict) -> dict:
    """Воронка → (статус, причина): pass / fail / unknown / na. Единый источник для чек-листа, вопросов и находок."""
    live = [g for g in _live(ds) if _active(g, th)]
    if not live:
        return {k: ("na", "нет активных групп") for k, _ in FUNNEL_LABELS}
    banners = [b for b in _banners(ds) if b["status"] in th["active_statuses"]]
    objectives = {g["objective"] for g in live}
    urls = [u.lower() for b in banners for u in b.get("urls") or []]
    layers_known = any(_layer(g) is not None for g in live)
    out = {}
    for key, _ in FUNNEL_LABELS:
        if key == "retargeting":
            if not layers_known:
                out[key] = ("unknown", "слои аудиторий не определены — таргетинги не получены")
                continue
            ok = any(_layer(g) == "warm" and not _guessed(g) for g in live)
            if not ok and any(_layer(g) == "warm" for g in live):   # тёплый только по догадке — не ✅ и не ❌
                out[key] = ("unknown", "ретаргетинг не подтверждён: " + GUESSED_REASON)
                continue
        elif key == "senler":   # единственная воронка по объявлениям; остальные — по цели и слоям групп
            if not ds["coverage"].get("banners"):
                out[key] = ("unknown", "объявления не собирались")
                continue
            if not banners and not any(g["id"] not in _banner_groups(ds) for g in live):
                out[key] = ("na", "нечего проверять: нет активных объявлений")
                continue
            if not urls:
                out[key] = ("unknown", "ссылки объявлений не получены")
                continue
            ok = any(m in u for m in th["senler_markers"] for u in urls)
        else:
            ok = bool(objectives & set(th["funnel_objectives"][key]))
        out[key] = ("pass", "") if ok else ("fail", "не используется")
    return out


def absent_ask_funnels(ds: dict, th: dict) -> list:
    """Ключи воронок из ASK_FUNNELS, которых нет среди живых активных групп."""
    st = funnel_status(ds, th)
    return [k for k in ASK_FUNNELS if st[k][0] == "fail"]


def load_client_answers(ws) -> dict:
    """client_answers.json → {ключ воронки: {verdict, answer}}. Нет файла — {}; мусор игнорируется с предупреждением."""
    path = Path(ws) / "client_answers.json"
    if not path.is_file():
        return {}

    def warn(msg):
        print(f"client_answers.json: {msg} — игнорирую", file=sys.stderr)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        warn(f"не прочитан ({e})")
        return {}
    if not isinstance(raw, dict):
        warn("ожидался объект вида {\"funnel_leadform\": {...}}")
        return {}
    out = {}
    for name, val in raw.items():
        key = name[len("funnel_"):] if isinstance(name, str) and name.startswith("funnel_") else None
        if key not in ASK_FUNNELS:
            warn(f"неизвестный ключ «{name}»")
        elif not isinstance(val, dict) or val.get("verdict") not in ANSWER_VERDICTS:
            warn(f"«{name}»: verdict должен быть «na» или «potential»")
        else:
            answer = val.get("answer")
            out[key] = {"verdict": val["verdict"], "answer": answer.strip() if isinstance(answer, str) else ""}
    return out


def funnel_potential_findings(ds: dict, th: dict, answers: dict | None) -> list:
    out = []
    absent = set(absent_ask_funnels(ds, th))
    for key, a in (answers or {}).items():
        if a["verdict"] != "potential" or key not in absent:
            continue
        f = ASK_FUNNELS[key]
        fnd = finding("funnel_potential", "green", "account", ACCOUNT, f"Потенциал роста: {f['label']}",
                      {"potential": f["why"], "client_answer": a["answer"]},
                      f"Обсудить с клиентом запуск воронки «{f['label']}»", conf="medium")
        fnd["check"] = f"B1.{key}"
        out.append(fnd)
    return out


# ---------- запуск ----------

def diagnose(ds: dict, th: dict, answers: dict | None = None) -> dict:
    ref, source = reference_cpa(ds, th)
    ctx = {"ref_cpa": ref, "ref_source": source, "evaluated": defaultdict(int)}
    out = []
    for fn in RULES:
        out.extend(fn(ds, th, ctx))
    out.extend(funnel_potential_findings(ds, th, answers))
    out.sort(key=lambda f: (LEVEL_ORDER[f["level"]], -f["effect_rub"]))
    return {"ref_cpa": ref, "ref_source": source, "findings": out, "evaluated": dict(ctx["evaluated"])}


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
    try:  # здесь, а не наверху модуля: checks и questions импортируют diagnose
        from scripts.checks import run_checks
        from scripts.questions import build_questions
    except ImportError:  # запуск напрямую
        from checks import run_checks
        from questions import build_questions
    th = load_thresholds(args.thresholds)
    answers = load_client_answers(ws)
    result = diagnose(ds, th, answers)
    result["checklist"] = run_checks(ds, th, result["findings"], result["evaluated"], answers)
    result["questions"] = build_questions(ds, th, result["findings"], answers)
    (ws / "findings.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    counts = defaultdict(int)
    for f in result["findings"]:
        counts[f["level"]] += 1
    total = result["checklist"]["total"]
    score = result["checklist"].get("idle") if total is None else f"балл {total[0]} / {total[1]}"
    print(f"находок: {len(result['findings'])} ({dict(counts)}), опорный CPA: {result['ref_cpa']} "
          f"({result['ref_source']}), {score}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
