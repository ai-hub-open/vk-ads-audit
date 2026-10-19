"""
features.py — признаки для методики v1 из сырых сущностей VK: таргетинг, объявления, сегменты.

Имена полей с пометкой «API?» взяты по документации и подтверждаются живым прогоном
(references/mcp-read-tools.md). Правило известности: поле считается известным, если его ключ
встретился хотя бы у одной сущности кабинета. Неизвестное поле → признак None → проверка «—».
"""
from __future__ import annotations

import json
import re
from datetime import datetime

INTEREST_KEYS = ("interests", "interests_soc_dem", "interests_stable")
COMMUNITY_KEYS = ("groups", "vk_groups", "ok_groups", "communities")                  # API?
KEYWORD_KEYS = ("keywords", "search_phrases", "vk_search_phrases", "context_phrases")  # API?
MINUS_KEYS = ("minus_phrases", "negative_phrases", "keywords_minus")                 # API?
KW_PERIOD_KEYS = ("search_period", "keywords_period", "phrases_period")              # API?
KW_EXPAND_KEYS = ("expand_keywords", "keywords_expansion", "phrases_expansion")      # API?
EXCLUDE_KEYS = ("segments_exclude", "exclude_segments", "excluded_segments")         # API?
AUTO_PADS_KEYS = ("auto_placements", "pads_auto")                                    # API?
DEVICE_ADJ_KEYS = ("device_bid_adjustments", "mobile_bid_adjustments")               # API?
DEMO_ADJ_KEYS = ("demography_bid_adjustments", "sex_age_bid_adjustments")            # API?
ERID_KEYS = ("ord_marker",)                                                          # подтверждено 16.10.2026 (объявление)
ORD_STATUS_KEYS = ("or_status",)              # подтверждено 16.10.2026 (объявление): смысл значений не расшифрован — справочно
AUTO_UTM_KEYS = ("enable_utm",)                                                     # подтверждено 16.10.2026 (группа)
PLAN_BUDGET_KEYS = ("budget_limit", "budget_limit_day")                              # API? (кампания)
SIZE_KEYS = ("size", "count", "entries_count", "audience_size")                      # API? (сегмент, база)
UPDATED_KEYS = ("updated", "last_upload", "uploaded")                                # API? (база)
SEGMENT_RELATION_KEYS = ("relations",)        # подтверждено 16.10.2026 (MCP 0.12.0, fields=…,relations,…)
RELATION_TYPE_KEYS = ("object_type",)         # подтверждено 16.10.2026: relations[{"id","object_type","object_id"}]
RELATION_ID_KEYS = ("object_id",)             # подтверждено 16.10.2026
USERS_LIST_TYPE_MARKERS = ("users_list", "user_list")      # подтверждено 16.10.2026: remarketing_users_list (база)
WARM_RELATION_TYPES = ("remarketing_counter",)              # подтверждено 16.10.2026: пиксель — тёплый слой
KEYWORD_RELATION_TYPES = ("remarketing_search_phrases",)    # подтверждено 16.10.2026: ключевые фразы — холодный слой
RATIOS = {"1:1": 1.0, "4:5": 0.8, "16:9": 16 / 9, "9:16": 9 / 16}
RATIO_TOL = 0.04
_LAL_RE = re.compile(r"lookalike|look-alike|\blal\b|похож", re.I)


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русская форма по числу: 1 группа, 2 группы, 5 групп (11–14 — «много»)."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else many


def is_live_group(g) -> bool:
    """Группа не удалена и не в архиве (VK отдаёт архивные с issues [{"code": "ARCHIVED"}] — в dataset это коды)."""
    issues = g.get("issues") or []
    codes = {i.get("code") if isinstance(i, dict) else i for i in issues}
    return g.get("status") != "deleted" and "ARCHIVED" not in codes


PLAN_STOPPED_STATUSES = ("blocked", "deleted")   # кампания (ad_plan) остановлена или удалена — группы в ней не крутятся
PLAN_STOPPED_ISSUE = "AD_PLAN_STOPPED"


def paused_by_plan(g) -> bool:
    """Группа со статусом active, но её кампания остановлена (третий живой прогон: кампании выключили в день сбора,
    у их групп status active и issue AD_PLAN_STOPPED). Такая группа на паузе, а не активна."""
    issues = g.get("issues") or []
    codes = {i.get("code") if isinstance(i, dict) else i for i in issues}
    return g.get("plan_status") in PLAN_STOPPED_STATUSES or PLAN_STOPPED_ISSUE in codes


def is_active_group(g, active_statuses) -> bool:
    """Единый помощник «группа реально откручивает»: статус из active_statuses и кампания не остановлена."""
    return g.get("status") in active_statuses and not paused_by_plan(g)


# Коды issues VK (группы, объявления, статус ОРД) → подписи в отчёте. Неизвестный код — «другая причина (КОД)»:
# сырой код в скобках, чтобы специалист нашёл его в кабинете или документации VK (решение третьего живого прогона).
ISSUE_LABELS = {
    "OUT_OF_DAY_LIMIT": "превышен дневной лимит",
    "OUT_OF_TOTAL_LIMIT": "исчерпан общий лимит",
    "AD_PLAN_STOPPED": "кампания остановлена",
    "ARCHIVED": "в архиве",
    "NO_BANNERS": "нет объявлений",
    "NO_BANNERS_WITH_ACTIVE_STATUS": "нет активных объявлений",
    "STOPPED": "остановлена",
    "PACKAGE_DELETED": "формат размещения удалён",
    "NO_MONEY": "закончились деньги на балансе",
}
ORD_STATUS_LABELS = {"ACTIVE": "активен"}


def issue_label(code) -> str:
    return ISSUE_LABELS.get(code) or f"другая причина ({code})"


def ord_status_label(v) -> str | None:
    """or_status объявления по-человечески: «активен; коды: превышен дневной лимит». Строка (старая форма) — как
    статус. None — статуса нет."""
    if isinstance(v, dict):
        st = v.get("status") or v.get("major_status")
        codes = [c for c in v.get("codes") or [] if c]
    elif isinstance(v, str) and v.strip():
        st, codes = v.strip(), []
    else:
        return None
    text = ORD_STATUS_LABELS.get(st) or (f"другой статус ({st})" if st else "статус не указан")
    if codes:
        text += "; коды: " + ", ".join(issue_label(c) for c in codes)
    return text


GROUP_STATUS_LABELS = {"active": "активна", "blocked": "на паузе", "deleted": "удалена"}


def group_status_label(g) -> str:
    """Статус группы по-человечески: сырое значение API в отчёт не идёт (неизвестное — «другой статус (…)»)."""
    if g.get("status") == "deleted":
        return "удалена"
    if not is_live_group(g):
        return "в архиве"
    if g.get("status") == "active" and paused_by_plan(g):
        return "на паузе (кампания остановлена)"
    st = g.get("status")
    return GROUP_STATUS_LABELS.get(st) or (f"другой статус ({st})" if st else "—")


def observed_keys(dicts) -> set:
    return {k for d in dicts if isinstance(d, dict) for k in d}


def _flag(t: dict, observed: set, keys) -> bool | None:
    if not any(k in observed for k in keys):
        return None
    return any(bool(t.get(k)) for k in keys)


def first_known(d: dict, keys, observed: set):
    """Значение первого известного ключа; None — если ни один ключ не встречался в кабинете."""
    if not any(k in observed for k in keys):
        return None
    return next((d[k] for k in keys if d.get(k) is not None), None)


def targeting_features(t, observed: set) -> dict:
    t = t if isinstance(t, dict) else {}
    geo = t.get("geo") if isinstance(t.get("geo"), dict) else {}
    regions = [r for r in (geo.get("regions") or []) if isinstance(r, int)]
    age = t.get("age") if isinstance(t.get("age"), dict) else {}
    ages = [a for a in (age.get("age_list") or []) if isinstance(a, int)]
    period = first_known(t, KW_PERIOD_KEYS, observed)
    return {
        "keys": sorted(t.keys()),
        "regions": regions,
        "age_span": (max(ages) - min(ages)) if ages else None,
        "sex": list(t.get("sex") or []),
        "has_interests": any(bool(t.get(k)) for k in INTEREST_KEYS),
        "has_communities": _flag(t, observed, COMMUNITY_KEYS),
        "has_keywords": _flag(t, observed, KEYWORD_KEYS),
        "has_minus": _flag(t, observed, MINUS_KEYS),
        "kw_period": period if isinstance(period, int) and not isinstance(period, bool) else None,
        "kw_expand": _flag(t, observed, KW_EXPAND_KEYS),
        "has_exclusions": _flag(t, observed, EXCLUDE_KEYS),
        "auto_placements": _flag(t, observed, AUTO_PADS_KEYS),
        "device_adjustments": _flag(t, observed, DEVICE_ADJ_KEYS),
        "demo_adjustments": _flag(t, observed, DEMO_ADJ_KEYS),
    }


def _ratio_of(role: str):
    sizes = re.search(r"(\d{3,4})x(\d{3,4})", role)
    if sizes:
        return int(sizes.group(1)) / int(sizes.group(2))
    pair = re.search(r"(?<!\d)(\d{1,2})_(\d{1,2})(?!\d)", role)
    if pair:
        return int(pair.group(1)) / int(pair.group(2))
    return None


def content_features(content) -> dict:
    """ratios_unknown — есть роль image/video, из имени которой соотношение сторон не прочитать
    (например video_landscape_180s): форматы такого объявления неизвестны, а не «отсутствуют»."""
    ratios, video, ids, unknown = set(), False, [], False
    for role, item in (content.items() if isinstance(content, dict) else []):
        r = str(role).lower()
        video = video or r.startswith("video")
        if isinstance(item, dict) and item.get("id") is not None:
            ids.append(item["id"])
        if not r.startswith(("image", "video")):
            continue
        value = _ratio_of(r)
        if value is None:
            unknown = True
            continue
        for name, target in RATIOS.items():
            if abs(value - target) / target <= RATIO_TOL:
                ratios.add(name)
    return {"ratios": sorted(ratios), "has_video": video, "content_ids": ids, "ratios_unknown": unknown}


def url_strings(v) -> list:
    out: list = []
    if isinstance(v, dict):
        for k, x in v.items():
            if k == "url" and isinstance(x, str):
                out.append(x)
            else:
                out.extend(url_strings(x))
    elif isinstance(v, list):
        for x in v:
            out.extend(url_strings(x))
    return out


CTA_TEXTBLOCK = "cta_sites_full"   # подтверждено 16.10.2026: кнопка лежит в textblocks.cta_sites_full.text (learnMore …)


def _is_cta_block(key) -> bool:
    return str(key).startswith("cta_")


def banner_text(textblocks) -> str:
    """Текст объявления без блоков кнопки (cta_*): код кнопки (learnMore) — не призыв в тексте."""
    if not isinstance(textblocks, dict):
        return ""
    parts = [tb.get("text") for k, tb in textblocks.items() if isinstance(tb, dict) and not _is_cta_block(k)]
    return " ".join(p for p in parts if isinstance(p, str) and p.strip())


def banner_cta(banner) -> str | None:
    """Кнопка объявления: call_to_action, а если он пуст (живой прогон: null у всех) — textblocks.cta_sites_full.text,
    затем любой другой блок cta_*. None — кнопки в ответе нет."""
    cta = banner.get("call_to_action") if isinstance(banner, dict) else None
    if cta not in (None, ""):
        return str(cta)
    tbs = banner.get("textblocks") if isinstance(banner, dict) else None
    if not isinstance(tbs, dict):
        return None
    keys = [CTA_TEXTBLOCK] + sorted(k for k in tbs if _is_cta_block(k) and k != CTA_TEXTBLOCK)
    for k in keys:
        text = (tbs.get(k) or {}).get("text") if isinstance(tbs.get(k), dict) else None
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def is_lal(segment) -> bool:
    return bool(_LAL_RE.search(json.dumps(segment, ensure_ascii=False)))


def relation_types(segment) -> list:
    """Типы объектов связей сегмента (relations[].object_type) в нижнем регистре; [] — связей нет или не пришли."""
    out: list = []
    for rel_key in SEGMENT_RELATION_KEYS:
        for rel in (segment.get(rel_key) or []) if isinstance(segment, dict) else []:
            if isinstance(rel, dict):
                kind = next((rel[k] for k in RELATION_TYPE_KEYS if isinstance(rel.get(k), str)), "")
                if kind:
                    out.append(kind.lower())
    return out


def _warm_type(t: str) -> bool:
    return t in WARM_RELATION_TYPES or any(m in t for m in USERS_LIST_TYPE_MARKERS)


def is_keyword_segment(segment) -> bool:
    """Сегмент ключевых фраз (relation remarketing_search_phrases): фразы хранятся в сегменте."""
    return any(t in KEYWORD_RELATION_TYPES for t in relation_types(segment))


def segment_kind(segment):
    """lal / warm (пиксель, база) / keywords (ключевые фразы — холодный слой); None — тип по связям не определить
    (связей нет, как в старых raw/, или тип незнакомый)."""
    if is_lal(segment):
        return "lal"
    types = relation_types(segment)
    if any(_warm_type(t) for t in types):   # несколько связей разных типов: хоть одна тёплая — тёплый
        return "warm"
    if types and all(t in KEYWORD_RELATION_TYPES for t in types):
        return "keywords"
    return None


def layer_info(segment_ids, segments_by_id: dict):
    """Слой группы по целевым сегментам: {"layer": cold / warm / lal, "guessed", "subtype"}; None — сегмент не найден
    в списке сегментов (или список не собран). guessed — тип хотя бы одного сегмента по связям не определён и слой
    взят по прежней логике «не LAL — тёплый» (низкая надёжность). subtype keywords — холодный слой ключевых фраз."""
    if not segment_ids:
        return {"layer": "cold", "guessed": False, "subtype": None}
    if any(sid in segments_by_id and is_lal(segments_by_id[sid]) for sid in segment_ids):
        return {"layer": "lal", "guessed": False, "subtype": None}
    if any(sid not in segments_by_id for sid in segment_ids):
        return None
    kinds = [segment_kind(segments_by_id[sid]) for sid in segment_ids]
    if "warm" in kinds:
        return {"layer": "warm", "guessed": False, "subtype": None}
    if all(k == "keywords" for k in kinds):
        return {"layer": "cold", "guessed": False, "subtype": "keywords"}
    return {"layer": "warm", "guessed": True, "subtype": None}


def layer_of(segment_ids, segments_by_id: dict):
    """cold / warm / lal; None — сегмент не найден в списке сегментов (или список не собран): слой неизвестен."""
    info = layer_info(segment_ids, segments_by_id)
    return info["layer"] if info else None


def users_list_ids(segment) -> list:
    """id пользовательских баз, на которые ссылается сегмент (связи relations)."""
    out: list = []
    for rel_key in SEGMENT_RELATION_KEYS:
        for rel in (segment.get(rel_key) or []) if isinstance(segment, dict) else []:
            if not isinstance(rel, dict):
                continue
            kind = next((rel[k] for k in RELATION_TYPE_KEYS if isinstance(rel.get(k), str)), "")
            oid = next((rel[k] for k in RELATION_ID_KEYS if rel.get(k) is not None), None)
            if oid is not None and any(m in kind.lower() for m in USERS_LIST_TYPE_MARKERS):
                out.append(oid)
    return out


def parse_dt(s):
    if not isinstance(s, str) or len(s) < 10:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19] if "T" in s or " " in s else s[:10], fmt)
        except ValueError:
            continue
    return None
