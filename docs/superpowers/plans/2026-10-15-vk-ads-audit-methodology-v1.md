# vk-ads-audit — методика v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заменить черновую диагностику экспертной методикой v1: ~25 новых правил, правка 6 существующих, чек-листы по блокам A–G с баллом «n / m», раздел «Вопросы к клиенту».

**Architecture:** `features.py` извлекает из сырых сущностей признаки (таргетинг, объявления, сегменты); `normalize.py` кладёт их в `dataset.json` вместе с окнами 7/14/30 дней. Новые правила — в `diagnose.py` по прежнему контракту находки (+ поле `check`). `checks.py` строит чек-лист по блокам из `dataset` и находок, `questions.py` — вопросы к клиенту; `render_report.py` выводит оба раздела. Поля «API?» неизвестны до живого прогона: признак `None` → проверка «— не проверено», а не ❌.

**Tech Stack:** Python 3.9+ stdlib, pytest. Репозиторий `D:\vk-ads-audit`, ветка `feat/methodology-v1` (поверх `feat/0.1.0`).

**Spec:** `docs/superpowers/specs/2026-10-15-vk-ads-audit-methodology-v1.md` (дополняет `2026-10-14-vk-ads-audit-design.md`; раздел 6 основной спеки заменён).

## Global Constraints

- Скрипты — только stdlib, Python 3.9+; `from __future__ import annotations` в каждом модуле.
- Запуск — `python -m scripts.<имя>` из папки скилла; первой строкой `main()` — `setup_console()`.
- Только чтение: любой вызов MCP — через `McpClient.call`, вне `ALLOWED_TOOLS` — `ForbiddenToolError` до сети.
- Единицы денег/процентов приводятся только в `scripts/normalize.py`.
- Пороги и словари методики — только в `references/thresholds.json`; у каждого ключа — источник в `references/diagnostic-rules.md`.
- Признак, поле которого не получено из API, — `None`; правило по `None` молчит, проверка получает статус `unknown` («—»), а не `fail`.
- Решения Лизы (спека, раздел 4): свежесть ⚪ > 14 дней, 🟠 > 28; частота 🟠 ≥ 3 при падении CTR, 🔴 > 5; креативов в группе 2–5; дневной бюджет 🟠 < 300 ₽, ⚪ 300–500 ₽; выводы по объявлениям не раньше 7 дней работы; обучение — < 15 конверсий за 7 дней работы.
- Тексты пользователю — на русском. `SKILL.md` ≤ 400 строк.
- Сеть в тестах закрыта.

## Review Focus

1. Поле «API?» не пришло ни у одной сущности кабинета → признак `None`, правило молчит, проверка `unknown`; пришло у другой сущности → у этой `False` (Task 1, `test_api_field_unknown_vs_unused`).
2. Старый `dataset.json` без новых полей (0.1) → диагностика и чек-лист не падают (Task 6, `test_old_dataset_without_features`).
3. Период короче 30 дней → окна 14/30 `None`, `cpa_trend` молчит, F4 = `unknown` (Task 1, `test_windows_short_period`; Task 6, `test_f4_unknown_on_short_period`).
4. Кабинет без расхода → правила долей и CPC не делят на ноль (Task 3, `test_layer_share_zero_spend`; Task 5, `test_cpc_outlier_no_clicks`).
5. Путь A: объявления собраны не у всех групп → правила объявлений (E1, E3, E4) только по покрытым группам (Task 5, `test_ratio_rules_skip_uncovered_groups`).

---

## File Structure

```
scripts/features.py        НОВЫЙ — признаки из сырых сущностей + имена полей «API?»     (Task 1)
scripts/normalize.py       + признаки, слои, окна, поля объявлений/кампаний/баз, as_of   (Task 1)
scripts/diagnose.py        + CHECK_OF, правки правил, ~25 новых правил                  (Tasks 2–5)
scripts/checks.py          НОВЫЙ — чек-лист по блокам A–G, баллы                        (Task 6)
scripts/questions.py       НОВЫЙ — вопросы к клиенту                                    (Task 6)
scripts/render_report.py   + чек-лист, вопросы, «не проверено» в ограничениях           (Task 7)
scripts/collect.py         + поля объявлений и групп                                    (Task 8)
references/thresholds.json v1-ключи                                                    (Tasks 2–5)
references/diagnostic-rules.md, report-template.md, client-questions.md (новый),
references/mcp-read-tools.md, SKILL.md                                                  (Task 8)
tests/helpers.py           + умолчания новых полей                                      (Task 1)
evals/fixtures.py, evals/run_evals.py, tests/test_evals.py                              (Task 9)
```

---

### Task 1: Признаки, слои и окна в dataset

**Files:**
- Create: `scripts/features.py`, `tests/test_features.py`
- Modify: `scripts/normalize.py` (`build_dataset`, `main`), `tests/helpers.py`, `tests/test_normalize.py`

**Interfaces:**
- Consumes: `normalize.money`, `normalize.aggregate`, `normalize._segment_ids`, `raw_store.items_of/read_calls`.
- Produces (`scripts/features.py`): константы имён полей (`KEYWORD_KEYS`, `MINUS_KEYS`, `KW_PERIOD_KEYS`, `KW_EXPAND_KEYS`, `EXCLUDE_KEYS`, `COMMUNITY_KEYS`, `AUTO_PADS_KEYS`, `DEVICE_ADJ_KEYS`, `DEMO_ADJ_KEYS`, `ERID_KEYS`, `AUTO_UTM_KEYS`, `PLAN_BUDGET_KEYS`, `SIZE_KEYS`, `UPDATED_KEYS`, `INTEREST_KEYS`), `observed_keys(dicts) -> set`, `targeting_features(t, observed) -> dict`, `content_features(content) -> dict` (`ratios: list[str]`, `has_video: bool`, `content_ids: list`), `url_strings(v) -> list[str]`, `banner_text(textblocks) -> str`, `is_lal(segment) -> bool`, `layer_of(segment_ids, segments_by_id) -> "warm"|"lal"|"cold"`, `first_known(d, keys, observed)`, `parse_dt(s) -> datetime|None`.
- Produces (`dataset.json`, новые поля):
  - группа: `features` (dict из `targeting_features`), `layer`, `max_price_rub` (float|None), `auto_utm` (bool|None), `windows` `{"7","14","30": stats|None}`;
  - объявление: `cta` (str|None), `text` (str), `urls` (list[str]), `ratios`, `has_video`, `content_ids`, `created` (str|None), `age_days` (int|None), `erid` (str|None: `None` — поле неизвестно, `""` — пусто);
  - кампания: `budget_day_rub`, `budget_total_rub`, `has_budget` (bool|None);
  - сегмент: `lal` (bool), `size` (int|None); база (`users_lists`): `size`, `updated` (str|None);
  - верхний уровень: `as_of` (ISO, `build_dataset(..., as_of=None)` → сейчас), `totals_windows`.
- `tests/helpers.py`: `make_group`/`make_banner`/`make_ds` получают умолчания всех новых полей.

- [ ] **Step 1: Тест признаков**

`tests/test_features.py`:
```python
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
    assert content_features(None) == {"ratios": [], "has_video": False, "content_ids": []}


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
    assert layer_of([999], segs) == "warm"   # сегмента нет в списке — считаем ретаргетом
```

- [ ] **Step 2: Убедиться, что падает**

Run: `python -m pytest tests/test_features.py -v` — Expected: FAIL (`ModuleNotFoundError: scripts.features`).

- [ ] **Step 3: `scripts/features.py`**

```python
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
ERID_KEYS = ("erid", "ord_token", "ord_erid")                                        # API? (объявление)
AUTO_UTM_KEYS = ("enable_utm", "auto_utm")                                           # API? (группа)
PLAN_BUDGET_KEYS = ("budget_limit", "budget_limit_day")                              # API? (кампания)
SIZE_KEYS = ("size", "count", "entries_count", "audience_size")                      # API? (сегмент, база)
UPDATED_KEYS = ("updated", "last_upload", "uploaded")                                # API? (база)
RATIOS = {"1:1": 1.0, "4:5": 0.8, "16:9": 16 / 9, "9:16": 9 / 16}
RATIO_TOL = 0.04
_LAL_RE = re.compile(r"lookalike|look-alike|\blal\b|похож", re.I)


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
    return next((d[k] for k in keys if k in d), None)


def targeting_features(t, observed: set) -> dict:
    t = t if isinstance(t, dict) else {}
    geo = t.get("geo") if isinstance(t.get("geo"), dict) else {}
    regions = [r for r in (geo.get("regions") or []) if isinstance(r, int)]
    age = t.get("age") if isinstance(t.get("age"), dict) else {}
    ages = [a for a in (age.get("age_list") or []) if isinstance(a, int)]
    period = first_known(t, KW_PERIOD_KEYS, observed)
    return {
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
    ratios, video, ids = set(), False, []
    for role, item in (content.items() if isinstance(content, dict) else []):
        r = str(role).lower()
        video = video or r.startswith("video")
        if isinstance(item, dict) and item.get("id") is not None:
            ids.append(item["id"])
        value = _ratio_of(r)
        if value is None:
            continue
        for name, target in RATIOS.items():
            if abs(value - target) / target <= RATIO_TOL:
                ratios.add(name)
    return {"ratios": sorted(ratios), "has_video": video, "content_ids": ids}


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


def banner_text(textblocks) -> str:
    if not isinstance(textblocks, dict):
        return ""
    parts = [tb.get("text") for tb in textblocks.values() if isinstance(tb, dict)]
    return " ".join(p for p in parts if isinstance(p, str) and p.strip())


def is_lal(segment) -> bool:
    return bool(_LAL_RE.search(json.dumps(segment, ensure_ascii=False)))


def layer_of(segment_ids, segments_by_id: dict) -> str:
    if not segment_ids:
        return "cold"
    if any(sid in segments_by_id and is_lal(segments_by_id[sid]) for sid in segment_ids):
        return "lal"
    return "warm"


def parse_dt(s):
    if not isinstance(s, str) or len(s) < 10:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19] if "T" in s or " " in s else s[:10], fmt)
        except ValueError:
            continue
    return None
```

- [ ] **Step 4: Прогнать** `python -m pytest tests/test_features.py -v` — Expected: 8 passed.

- [ ] **Step 5: Тест обогащения dataset**

Добавить в `tests/test_normalize.py`:
```python
def test_v1_fields(tmp_path):
    raw = tmp_path / "raw"
    groups = [{"id": 1, "name": "G1", "status": "active", "objective": "site_conversions", "ad_plan_id": 7,
               "max_price": "50.00", "enable_utm": True,
               "targetings": {"geo": {"regions": [1]}, "segments": [102], "keywords": ["велосипед"]}},
              {"id": 2, "name": "G2", "status": "active", "objective": "site_conversions", "ad_plan_id": 7,
               "targetings": {"geo": {"regions": [1]}}}]
    cabinet(raw, groups, banners_by_group={1: [{"id": 11, "status": "active", "created": "2026-09-20 10:00:00",
                                                "call_to_action": "sign_up", "erid": "Kra23",
                                                "textblocks": {"text_90": {"text": "Запишитесь"}},
                                                "urls": {"primary": {"id": 3, "url": "https://s.ru/?utm_source=vk"}},
                                                "content": {"image_1080x1080": {"id": 50}}}]},
            summary={1: base(spent="100", shows=1000, clicks=10)})
    write_call(raw, "vk_ads_remarketing_segments_list", {"paginated": True},
               {"items": [{"id": 102, "name": "LAL клиенты", "size": 40}]})
    write_call(raw, "vk_ads_users_lists_list", {"paginated": True},
               {"items": [{"id": 5, "name": "Клиенты", "entries_count": 900, "updated": "2026-05-01 00:00:00"}]})
    ds = build_dataset(raw, "2026-09-01", "2026-09-30", as_of="2026-10-01T10:00")
    g1, g2 = ds["groups"]
    assert g1["layer"] == "lal" and g2["layer"] == "cold"
    assert g1["features"]["has_keywords"] is True and g2["features"]["has_keywords"] is False
    assert g1["max_price_rub"] == 50.0 and g2["max_price_rub"] is None
    assert g1["auto_utm"] is True and g2["auto_utm"] is False
    b = ds["banners"][0]
    assert b["cta"] == "sign_up" and b["text"] == "Запишитесь" and b["erid"] == "Kra23"
    assert b["urls"] == ["https://s.ru/?utm_source=vk"] and b["ratios"] == ["1:1"] and b["content_ids"] == [50]
    assert b["age_days"] == 10
    assert ds["segments"][0]["lal"] is True and ds["segments"][0]["size"] == 40
    assert ds["users_lists"][0] == {"id": 5, "name": "Клиенты", "size": 900, "updated": "2026-05-01 00:00:00"}
    assert ds["as_of"] == "2026-10-01T10:00"


def test_erid_unknown_when_field_absent(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [{"id": 1, "name": "G1", "status": "active"}],
            banners_by_group={1: [{"id": 11, "status": "active"}]}, summary={})
    assert build_dataset(raw, "2026-09-01", "2026-09-30")["banners"][0]["erid"] is None


def test_plan_budget_known(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [])
    write_call(raw, "vk_ads_ad_plans_list", {"paginated": True},
               {"items": [{"id": 8, "name": "P", "budget_limit_day": 300000}, {"id": 9, "name": "Q"}]})
    plans = {p["id"]: p for p in build_dataset(raw, "2026-09-01", "2026-09-30")["plans"]}
    assert plans[8]["has_budget"] is True and plans[8]["budget_day_rub"] == 3000.0
    assert plans[9]["has_budget"] is False


def test_windows(tmp_path):
    raw = tmp_path / "raw"
    rows = [{"date": f"2026-09-{d:02d}", **base(spent="100", shows=1000, clicks=10, goals=1)} for d in range(1, 31)]
    cabinet(raw, [{"id": 1, "name": "G1", "status": "active"}], daily={1: rows})
    g = build_dataset(raw, "2026-09-01", "2026-09-30")["groups"][0]
    assert g["windows"]["7"]["spent"] == 700.0 and g["windows"]["30"]["spent"] == 3000.0


def test_windows_short_period(tmp_path):
    raw = tmp_path / "raw"
    P10 = {"date_from": "2026-09-21", "date_to": "2026-09-30", "metrics": "all"}
    write_call(raw, "vk_ads_campaigns_list", {"paginated": True}, {"items": [{"id": 1, "name": "G", "status": "active"}]})
    write_call(raw, "vk_ads_statistics_day", {"entity": "campaigns", **P10},
               {"items": [{"id": 1, "rows": [{"date": f"2026-09-{d}", **base(spent="10", shows=100, clicks=1)}
                                             for d in range(21, 31)]}]})
    g = build_dataset(raw, "2026-09-21", "2026-09-30")["groups"][0]
    assert g["windows"]["7"] is not None and g["windows"]["14"] is None and g["windows"]["30"] is None
```
(В `test_normalize.py` уже есть хелперы `cabinet`, `base`, `P`; `write_call` уже импортирован.)

- [ ] **Step 6: Убедиться, что падает** — `python -m pytest tests/test_normalize.py -v` — новые тесты FAIL (`as_of` / `KeyError: 'layer'`).

- [ ] **Step 7: Правки `scripts/normalize.py`**

Импорт (в оба блока `try/except ImportError` — `scripts.features` и `features`):
```python
    from scripts.features import (AUTO_UTM_KEYS, ERID_KEYS, EXCLUDE_KEYS, PLAN_BUDGET_KEYS, SIZE_KEYS, UPDATED_KEYS,
                                  banner_text, content_features, first_known, is_lal, layer_of,
                                  observed_keys, parse_dt, targeting_features, url_strings)
```
и `from datetime import date, datetime, timedelta`.

Новая функция над `build_dataset`:
```python
WINDOWS = (7, 14, 30)


def windows_of(daily: list, d_from: date, d_to: date) -> dict:
    """Окна последних N дней периода; окно длиннее периода — None (сравнивать не с чем)."""
    out: dict = {}
    for n in WINDOWS:
        start = d_to - timedelta(days=n - 1)
        if start < d_from:
            out[str(n)] = None
            continue
        rows = [r for r in daily if r.get("date") and start.isoformat() <= r["date"] <= d_to.isoformat()]
        out[str(n)] = aggregate(rows)
    return out
```

В `build_dataset`: сигнатура `build_dataset(raw_dir, date_from, date_to, target_cpa=None, path="B", as_of=None)`.
Перед циклом групп:
```python
    raw_groups = _list_items(raw_dir, "vk_ads_campaigns_list")
    observed_t = observed_keys(g.get("targetings") for g in raw_groups)
    observed_g = observed_keys(raw_groups)
    seg_items = _list_items(raw_dir, "vk_ads_remarketing_segments_list")
    segments_by_id = {s.get("id"): s for s in seg_items}
```
Цикл `for g in _list_items(raw_dir, "vk_ads_campaigns_list"):` заменить на `for g in raw_groups:`; в словарь группы добавить:
```python
            "features": targeting_features(g.get("targetings"), observed_t),
            "layer": layer_of(_segment_ids(g.get("targetings")), segments_by_id),
            "max_price_rub": round(money(g.get("max_price")), 2) or None if g.get("max_price") not in (None, "") else None,
            "auto_utm": (lambda v: None if v is None and not any(k in observed_g for k in AUTO_UTM_KEYS)
                         else bool(v))(first_known(g, AUTO_UTM_KEYS, observed_g)),
            "windows": windows_of(daily, d_from, d_to),
```
Для объявлений: перед циклом `banner_out` — `observed_b = observed_keys(banners)`; в словарь объявления добавить:
```python
            "cta": (str(b["call_to_action"]) if b.get("call_to_action") not in (None, "") else None),
            "text": banner_text(b.get("textblocks")),
            "urls": url_strings(b.get("urls")),
            **content_features(b.get("content")),
            "created": b.get("created"),
            "age_days": (d_to - parse_dt(b["created"]).date()).days if parse_dt(b.get("created")) else None,
            "erid": (None if not any(k in observed_b for k in ERID_KEYS)
                     else str(first_known(b, ERID_KEYS, observed_b) or "")),
```
Кампании, сегменты, базы и верх:
```python
    raw_plans = _list_items(raw_dir, "vk_ads_ad_plans_list")
    observed_p = observed_keys(raw_plans)
    plan_budget_known = any(k in observed_p for k in PLAN_BUDGET_KEYS)

    def plan_out(p):
        day, total = kopecks_to_rub(p.get("budget_limit_day")), kopecks_to_rub(p.get("budget_limit"))
        return {"id": p.get("id"), "name": p.get("name") or "", "status": p.get("status"),
                "budget_day_rub": day, "budget_total_rub": total,
                "has_budget": bool(day or total) if plan_budget_known else None}

    observed_s = observed_keys(seg_items)
    users_items = _list_items(raw_dir, "vk_ads_users_lists_list")
    observed_u = observed_keys(users_items)

    def size_of(x, observed):
        v = first_known(x, SIZE_KEYS, observed)
        return v if isinstance(v, int) and not isinstance(v, bool) else None
```
и в `return`:
```python
        "plans": [plan_out(p) for p in raw_plans],
        "segments": [{"id": s.get("id"), "name": s.get("name") or "", "lal": is_lal(s),
                      "size": size_of(s, observed_s)} for s in seg_items],
        "users_lists": [{"id": u.get("id"), "name": u.get("name") or "", "size": size_of(u, observed_u),
                         "updated": first_known(u, UPDATED_KEYS, observed_u)} for u in users_items],
        "as_of": as_of or datetime.now().isoformat(timespec="minutes"),
        "totals_windows": windows_of([r for g in live_groups for r in g["daily"]], d_from, d_to),
```
(`named("vk_ads_remarketing_segments_list")` и `named("vk_ads_users_lists_list")` заменены; `named` остаётся для пикселей.)

**Исключённые сегменты не считаются таргетингом.** `_segment_ids` собирает числа под любым ключом со словом
«segment» — значит, и под `segments_exclude`, и группа с одними исключениями стала бы «тёплой». В `_segment_ids`
пропускать ключи из `EXCLUDE_KEYS` (импорт из `features`):
```python
            for k, x in v.items():
                if k in EXCLUDE_KEYS:
                    continue
                walk(x, inside or "segment" in k)
```
Тест в `tests/test_normalize.py`:
```python
def test_excluded_segments_are_not_targeting():
    from scripts.normalize import _segment_ids
    assert _segment_ids({"segments": [1], "segments_exclude": [2]}) == [1]
```

Упростить строку `max_price_rub` до читаемой вспомогательной функции, если реализатору так понятнее:
```python
def _price(v):
    if v in (None, ""):
        return None
    p = round(money(v), 2)
    return p or None
```
и `"max_price_rub": _price(g.get("max_price"))`. Аналогично `auto_utm`:
```python
def _auto_utm(g, observed):
    if not any(k in observed for k in AUTO_UTM_KEYS):
        return None
    return bool(first_known(g, AUTO_UTM_KEYS, observed))
```
Использовать именно эти две функции (лямбда выше — только иллюстрация, в код её не переносить).

- [ ] **Step 8: Умолчания в `tests/helpers.py`**

```python
FEATURES = {"regions": [], "age_span": None, "sex": [], "has_interests": False, "has_communities": None,
            "has_keywords": None, "has_minus": None, "kw_period": None, "kw_expand": None,
            "has_exclusions": None, "auto_placements": None, "device_adjustments": None,
            "demo_adjustments": None}
```
`make_group`: добавить `"features": {**FEATURES, "regions": [id]}, "layer": "cold", "max_price_rub": None, "auto_utm": None, "windows": {"7": None, "14": None, "30": None}`; если в `kw` передан `features` как частичный dict — слить с умолчаниями:
```python
    feats = kw.pop("features", {})
    g = {...}
    g["features"] = {**FEATURES, "regions": [id], **feats}
```
`make_banner`: `"cta": None, "text": "", "urls": [], "ratios": [], "has_video": False, "content_ids": [], "created": None, "age_days": None, "erid": None`.
`make_ds`: план `{"id": 7, "name": "План", "status": "active", "budget_day_rub": None, "budget_total_rub": None, "has_budget": None}`, `"as_of": "2026-10-01T10:00"`, `"totals_windows": {"7": None, "14": None, "30": None}`.

- [ ] **Step 9: Прогнать всё** `python -m pytest -q` — Expected: всё зелёное (существующие тесты не меняют поведение).

- [ ] **Step 10: Commit**

```bash
git add scripts/features.py scripts/normalize.py tests/test_features.py tests/test_normalize.py tests/helpers.py
git commit -m "features: признаки таргетинга, объявлений, слои и окна 7/14/30 в dataset"
```

---

### Task 2: Пороги v1, ссылка находки на проверку, правки существующих правил

**Files:**
- Modify: `references/thresholds.json`, `scripts/diagnose.py`, `tests/test_diagnose_hygiene.py`, `tests/test_diagnose_performance.py`

**Interfaces:**
- Consumes: dataset из Task 1.
- Produces: `diagnose.CHECK_OF: dict[str, str]` (rule → check id), поле `check` в каждой находке; `diagnose.ACCOUNT = {"id": None, "name": "кабинет"}`; хелперы `_feat(g) -> dict`, `_layer(g) -> str`, `_active_banners(ds, g) -> list`, `_date(s) -> date|None`.

- [ ] **Step 1: `references/thresholds.json` — заменить целиком**

```json
{
  "_source": "Методика v1 (экспертный слепок, утверждён 2026-10-15). Источник каждого ключа — references/diagnostic-rules.md",
  "active_statuses": ["active"],
  "min_daily_budget_rub": 300,
  "recommended_daily_budget_rub": 500,
  "learning_budget_cpa_multiplier": 2,
  "auto_bidding_modes": ["max_goals"],
  "blocking_issue_codes": ["NO_MONEY", "AD_PLAN_STOPPED"],
  "rejected_moderation_statuses": ["banned", "rejected"],
  "document_reason_markers": ["документ", "document", "лиценз", "сертификат"],
  "moderating_statuses": ["new", "delayed", "moderating"],
  "moderation_hours": 24,
  "moderation_video_hours": 8,
  "autodelete_warn_days": 150,
  "min_active_banners_per_group": 2,
  "max_active_banners_per_group": 5,
  "pixel_objectives": ["site_conversions"],
  "conversion_objectives": ["site_conversions", "leadads"],
  "min_conversions_for_cpa": 5,
  "confidence_high_clicks": 300,
  "confidence_medium_clicks": 100,
  "no_conv_spend_cpa_multiplier": 2,
  "no_conv_min_spend_rub": 3000,
  "cpa_over_ratio": 1.5,
  "low_ctr_pct": 0.3,
  "low_ctr_min_shows": 5000,
  "ctr_norm_min_pct": 0.5,
  "cpc_outlier_ratio": 1.5,
  "cpc_outlier_min_clicks": 30,
  "burnout_ctr_drop": 0.3,
  "burnout_min_frequency": 3,
  "burnout_red_frequency": 5,
  "burnout_min_shows": 10000,
  "learning_min_conversions_per_week": 15,
  "min_run_days": 7,
  "cpa_trend_rise": 0.3,
  "concentration_share": 0.7,
  "concentration_min_groups": 3,
  "leader_cpa_ratio": 0.7,
  "leader_budget_use": 0.9,
  "leader_growth_share": 0.5,
  "layer_shares": {"warm": 0.4, "lal": 0.4, "cold": 0.2},
  "layer_shares_small": {"warm_lal": 0.7, "cold": 0.3},
  "layer_share_tolerance": 0.2,
  "small_budget_month_rub": 30000,
  "fragment_budget_rub": 200,
  "fragment_min_groups": 10,
  "default_name_patterns": ["(кампания|группа|группа объявлений|объявление|новая кампания|новая группа|новое объявление|campaign|ad group|ad|banner)[\\s№#\\d.\\-–_]*"],
  "default_name_share": 0.2,
  "kw_period_days": [7, 12],
  "country_region_ids": [188],
  "broad_age_span": 40,
  "lal_min_source": 50,
  "list_stale_days": 90,
  "required_ratios": ["1:1", "4:5", "16:9", "9:16"],
  "creative_stale_days": 14,
  "creative_stale_red_days": 28,
  "top_groups_spend_share": 0.8,
  "cta_words": ["запиш", "узна", "получ", "закаж", "купи", "оставь", "напиш", "скача", "звон", "подпиш", "перейд", "жми", "нажми", "выбер", "забер", "приход", "регистр", "попробу", "присоедин", "успей"],
  "generic_cta": ["learn_more", "more", "read_more", "подробнее"],
  "internal_hosts": ["vk.com", "vk.ru", "vk.me", "ok.ru", "senler.ru"],
  "funnel_objectives": {"subscription": ["socialengagement", "socialengagement_profile", "vk_channel", "max_channel"], "leadform": ["leadads"], "site": ["site_conversions"]},
  "senler_markers": ["senler"],
  "metrika_min_visits": 100,
  "high_bounce_rate": 0.6,
  "click_loss_min_clicks": 200,
  "click_loss_visit_ratio": 0.6
}
```

- [ ] **Step 2: Тест правок**

В `tests/test_diagnose_hygiene.py` вверху заменить `rules()` на скоуп старых правил (новые правила этого файла не касаются):
```python
HYGIENE = {"no_active_banners", "rejected_banners", "blocking_issues", "autodelete_risk", "banners_per_group",
           "low_budget", "duplicate_targeting", "unused_segments", "no_pixel"}


def rules(ds):
    return [(f["rule"], f["entity"]["id"]) for f in diagnose(ds, TH)["findings"] if f["rule"] in HYGIENE]
```
Добавить:
```python
def test_finding_has_check_id():
    f = diagnose(make_ds([make_group(1, budget_day_rub=200.0)], healthy_banners(1)), TH)["findings"]
    assert {x["rule"]: x["check"] for x in f}["low_budget"] == "D1"


def test_low_budget_white_band():
    f = [x for x in diagnose(make_ds([make_group(1, budget_day_rub=400.0)], healthy_banners(1)), TH)["findings"]
         if x["rule"] == "low_budget"]
    assert [x["level"] for x in f] == ["white"]


def test_banners_per_group_norm_2_to_5():
    assert ("banners_per_group", 2) in rules(make_ds([make_group(2)], healthy_banners(2, n=6)))
    assert ("banners_per_group", 3) not in rules(make_ds([make_group(3)], healthy_banners(3, n=5)))


def test_rejected_for_documents_points_to_settings():
    b = make_banner(19, 1, moderation_status="banned", moderation_reasons=["Нужны документы на услугу"])
    f = [x for x in diagnose(make_ds([make_group(1)], healthy_banners(1) + [b]), TH)["findings"]
         if x["rule"] == "rejected_banners"][0]
    assert "Документы для модерации" in f["action"]
```
В `tests/test_diagnose_performance.py`:
- `test_learning_uses_run_days_not_period`: заменить 25 конверсий на 35 (35 / 2 недели = 17,5 ≥ 15), комментарий — новый порог 15.
- добавить:
```python
def test_burnout_red_above_five():
    daily = [day(f"2026-09-{d:02d}", 100, 1000, 20) for d in range(1, 31)]
    g = make_group(1, daily=daily, stats=stats(3000, 30000, 600, 0, frequency=6.0))
    f = found(make_ds([g], banners(1)))[("burnout", 1)]
    assert f["level"] == "red"
```

- [ ] **Step 3: Убедиться, что падает** — `python -m pytest tests/test_diagnose_hygiene.py tests/test_diagnose_performance.py -v` — новые тесты FAIL.

- [ ] **Step 4: Правки `scripts/diagnose.py`**

После `RULES: list = []`:
```python
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
```
`finding()`: добавить в возвращаемый dict `"check": CHECK_OF.get(rule_id)`.
Хелперы после `_banners_of`:
```python
def _feat(g) -> dict:
    return g.get("features") or {}


def _layer(g) -> str:
    return g.get("layer") or "cold"


def _active_banners(ds, g, th) -> list:
    return [b for b in _banners_of(ds, g) if b["status"] in th["active_statuses"]]


def _date(s):
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None
```
`banners_per_group`: `n == 1` → `n < th["min_active_banners_per_group"] and n > 0`; текст действия — «Добавить варианты креатива: норма 2–5 в группе»; `n > max` — без изменений (max теперь 5).
`low_budget`: после ветки `b < min_daily_budget_rub` добавить:
```python
        elif b < th["recommended_daily_budget_rub"]:
            out.append(finding("low_budget", "white", "group", g, "Дневной бюджет на нижней границе",
                               {"budget_day_rub": b, "recommended_rub": th["recommended_daily_budget_rub"]},
                               f"Держать не ниже {th['recommended_daily_budget_rub']} ₽ в день, чтобы избежать резкого слива"))
```
(ветка автостратегии остаётся последней `elif`).
`rejected_banners`: действие:
```python
def _reject_action(b, th) -> str:
    reasons = json.dumps(b.get("moderation_reasons") or "", ensure_ascii=False).lower()
    if any(m in reasons for m in th["document_reason_markers"]):
        return "Загрузить документы в кабинете: Настройки → Документы для модерации — объявление запустится само"
    return ("Исправить по причине отказа и отправить на перемодерацию" if b["can_remoderate"]
            else "Исправить и создать заново: перемодерация недоступна")
```
`burnout`: в начале цикла после проверки частоты — красная ветка:
```python
        if freq and freq > th["burnout_red_frequency"] and g["stats"]["shows"] >= th["burnout_min_shows"]:
            out.append(finding("burnout", "red", "group", g, "Частота показа выше 5 — аудитория выжжена",
                               {"frequency": freq}, "Срочно обновить креативы или расширить аудиторию",
                               effect_rub=g["stats"]["spent"], conf=confidence(g["stats"]["clicks"], th)))
            continue
```
(оранжевая ветка — без изменений; условие `len(rows) < 4` проверять после красной ветки.)

- [ ] **Step 5: Прогнать** `python -m pytest -q` — Expected: всё зелёное. Эталоны (`evals`) могут разойтись — их чинит Task 9; если `tests/test_evals.py` падает только на новых находках, пометить в отчёте и не править эталоны здесь.

- [ ] **Step 6: Commit**

```bash
git add references/thresholds.json scripts/diagnose.py tests/test_diagnose_hygiene.py tests/test_diagnose_performance.py
git commit -m "diagnose: пороги методики v1, check у находки, правки бюджета, креативов, выгорания, модерации"
```

---

### Task 3: Правила блоков A и B (техника и структура)

**Files:**
- Modify: `scripts/diagnose.py` (блок `# ---------- v1: техника и структура ----------` перед `# ---------- эффективность ----------`)
- Test: `tests/test_rules_ab.py`

**Interfaces:**
- Consumes: `finding`, `rule`, `_live`, `_measured`, `_active`, `_feat`, `_layer`, `ACCOUNT`, хелперы тестов.
- Produces: правила `ord_missing` 🟠, `utm_missing` 🟠, `no_retargeting` ⚪, `layer_missing` ⚪, `layer_share` ⚪, `mixed_plan` ⚪, `fragmentation` 🟠, `no_cbo` ⚪, `naming` ⚪.

- [ ] **Step 1: Тест**

`tests/test_rules_ab.py`:
```python
from __future__ import annotations

from scripts.diagnose import diagnose, load_thresholds
from tests.helpers import make_banner, make_ds, make_group, stats

TH = load_thresholds()


def found(ds, rule):
    return [f for f in diagnose(ds, TH)["findings"] if f["rule"] == rule]


def test_ord_missing_only_when_field_known():
    known = make_ds([make_group(1)], [make_banner(11, 1, erid=""), make_banner(12, 1, erid="Kra")])
    assert found(known, "ord_missing")[0]["evidence"]["banners_without_erid"] == 1
    assert found(make_ds([make_group(1)], [make_banner(11, 1)]), "ord_missing") == []


def test_utm_missing():
    bs = [make_banner(11, 1, urls=["https://site.ru/"]), make_banner(12, 1, urls=["https://site.ru/?utm_source=vk"]),
          make_banner(13, 1, urls=["https://vk.com/club1"])]
    f = found(make_ds([make_group(1)], bs), "utm_missing")[0]
    assert f["evidence"]["without_utm"] == 1 and f["evidence"]["site_banners"] == 2


def test_utm_auto_on_group_is_ok():
    g = make_group(1, auto_utm=True)
    assert found(make_ds([g], [make_banner(11, 1, urls=["https://site.ru/"])]), "utm_missing") == []


def test_no_retargeting_and_layers():
    ds = make_ds([make_group(1, layer="cold"), make_group(2, layer="cold")])
    assert len(found(ds, "no_retargeting")) == 1
    assert found(ds, "layer_missing")[0]["evidence"]["missing_layers"] == ["lal"]
    full = make_ds([make_group(1, layer="warm"), make_group(2, layer="lal"), make_group(3, layer="cold")])
    assert found(full, "no_retargeting") == [] and found(full, "layer_missing") == []


def test_layer_share_big_budget():
    gs = [make_group(1, layer="warm", stats=stats(30000, 1000, 10)), make_group(2, layer="lal", stats=stats(5000, 1000, 10)),
          make_group(3, layer="cold", stats=stats(5000, 1000, 10))]
    f = found(make_ds(gs), "layer_share")[0]
    assert f["evidence"]["actual"]["warm"] == 0.75


def test_layer_share_small_budget_70_30():
    gs = [make_group(1, layer="warm", stats=stats(7000, 1000, 10)), make_group(3, layer="cold", stats=stats(3000, 1000, 10))]
    assert found(make_ds(gs), "layer_share") == []        # 10 000 ₽ за 30 дней — малый бюджет, 70/30 соблюдено


def test_layer_share_zero_spend():
    assert found(make_ds([make_group(1, layer="warm")]), "layer_share") == []


def test_mixed_plan():
    ds = make_ds([make_group(1, layer="warm", ad_plan_id=7), make_group(2, layer="cold", ad_plan_id=7)])
    assert found(ds, "mixed_plan")[0]["evidence"]["layers"] == ["cold", "warm"]


def test_fragmentation():
    gs = [make_group(i, budget_day_rub=150.0) for i in range(1, 11)]
    assert len(found(make_ds(gs), "fragmentation")) == 1
    assert found(make_ds(gs[:9]), "fragmentation") == []


def test_no_cbo_only_when_plan_budget_known():
    plans = [{"id": 7, "name": "P", "status": "active", "budget_day_rub": None, "budget_total_rub": None, "has_budget": False}]
    ds = make_ds([make_group(1), make_group(2)], plans=plans)
    assert len(found(ds, "no_cbo")) == 1
    assert found(make_ds([make_group(1), make_group(2)]), "no_cbo") == []     # has_budget None — неизвестно


def test_naming():
    ds = make_ds([make_group(1, name="Группа 1"), make_group(2, name="Группа объявлений 2")],
                 [make_banner(11, 1, name="Объявление 1"), make_banner(12, 2, name="ретарг_Мск_Ж25-45")])
    f = found(ds, "naming")[0]
    assert f["evidence"]["default_names"] == 3
    good = make_ds([make_group(1, name="ретарг_Мск_Ж25-45_моб")], [make_banner(11, 1, name="видео_9x16_оффер")],
                   plans=[{"id": 7, "name": "ретаргет_Мск", "status": "active", "budget_day_rub": None,
                           "budget_total_rub": None, "has_budget": None}])
    assert found(good, "naming") == []
```

- [ ] **Step 2: Убедиться, что падает** — `python -m pytest tests/test_rules_ab.py -v` — FAIL (правил нет).

- [ ] **Step 3: Реализация** — в `scripts/diagnose.py` добавить `import re` и блок перед `# ---------- эффективность ----------`:

```python
# ---------- v1: техника и структура ----------

def _site_url(u: str, th) -> bool:
    if not u.startswith("http"):
        return False
    host = re.sub(r"^https?://", "", u).split("/")[0].lower()
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
    auto = {g["id"] for g in ds["groups"] if g.get("auto_utm")}
    site = [b for b in ds["banners"] if b["status"] in th["active_statuses"]
            and any(_site_url(u, th) for u in b.get("urls") or [])]
    bad = [b for b in site if b["group_id"] not in auto
           and not any("utm_" in u for u in b.get("urls") or [])]
    if not bad:
        return []
    return [finding("utm_missing", "orange", "account", ACCOUNT, "Ссылки на сайт без UTM-меток",
                    {"without_utm": len(bad), "site_banners": len(site), "examples": [b["id"] for b in bad[:5]]},
                    "Разметить все ссылки UTM — иначе не понять, какие объявления приводят целевой трафик")]


@rule
def no_retargeting(ds, th, ctx):
    live = [g for g in _live(ds) if _active(g, th)]
    if not live or any(_layer(g) == "warm" for g in live):
        return []
    return [finding("no_retargeting", "white", "account", ACCOUNT, "В кабинете нет ретаргетинга",
                    {"active_groups": len(live)},
                    "Добавить группы на посетителей сайта, вовлечённых в сообщество и досмотревших видео")]


@rule
def layer_missing(ds, th, ctx):
    live = [g for g in _live(ds) if _active(g, th)]
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
    gs = [g for g in _measured(ds) if g["stats"]["spent"] > 0]
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
        if _active(g, th) and g.get("ad_plan_id") is not None:
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
    if not names:
        return []
    bad = [n for n in names if not n.strip() or any(p.fullmatch(n.strip()) for p in pats)]
    share = len(bad) / len(names)
    if share < th["default_name_share"]:
        return []
    return [finding("naming", "white", "account", ACCOUNT, "Названия не говорят, что внутри",
                    {"default_names": len(bad), "total": len(names), "share": round(share, 3),
                     "examples": bad[:5]},
                    "Называть по схеме сегмент_гео_пол-возраст_устройство (например, «конкур_Кар_Ж20-45_моб»)")]
```

- [ ] **Step 4: Прогнать** `python -m pytest tests/test_rules_ab.py -v` и затем `python -m pytest -q` — Expected: новые 12 passed; прочие тесты зелёные, кроме, возможно, `tests/test_evals.py` (чинит Task 9 — отметить в отчёте).

- [ ] **Step 5: Commit**

```bash
git add scripts/diagnose.py tests/test_rules_ab.py
git commit -m "diagnose: правила v1 — ОРД, UTM, ретаргет и слои, доли бюджета, CBO, дробление, названия"
```

---

### Task 4: Правила блоков C и D (таргетинг и стратегия)

**Files:**
- Modify: `scripts/diagnose.py` (блок `# ---------- v1: таргетинг и стратегия ----------` после блока Task 3)
- Test: `tests/test_rules_cd.py`

**Interfaces:**
- Consumes: Task 2–3 хелперы.
- Produces: `no_minus_phrases` ⚪, `kw_period` ⚪, `kw_expand_off` 🟢, `no_exclusions` 🟠, `too_broad` 🟠, `creative_overlap` 🟠, `lal_small_source` 🟠, `stale_list` ⚪, `manual_placements` ⚪, `cap_too_early` 🟠.

- [ ] **Step 1: Тест**

`tests/test_rules_cd.py`:
```python
from __future__ import annotations

from scripts.diagnose import diagnose, load_thresholds
from tests.helpers import make_banner, make_ds, make_group, stats

TH = load_thresholds()


def found(ds, rule):
    return [f for f in diagnose(ds, TH)["findings"] if f["rule"] == rule]


KW = {"has_keywords": True, "has_minus": False, "kw_period": 30, "kw_expand": False}


def test_keyword_rules():
    ds = make_ds([make_group(1, features=KW)])
    assert len(found(ds, "no_minus_phrases")) == 1
    assert found(ds, "kw_period")[0]["evidence"]["period_days"] == 30
    assert found(ds, "kw_expand_off")[0]["level"] == "green"


def test_keyword_rules_silent_when_unknown():
    ds = make_ds([make_group(1, features={"has_keywords": True})])   # has_minus/kw_period/kw_expand = None
    assert found(ds, "no_minus_phrases") == [] and found(ds, "kw_period") == [] and found(ds, "kw_expand_off") == []


def test_no_exclusions():
    ds = make_ds([make_group(1, features={"has_exclusions": False}),
                  make_group(2, objective="socialengagement", features={"has_exclusions": False}),
                  make_group(3)])
    assert [f["entity"]["id"] for f in found(ds, "no_exclusions")] == [1]


def test_too_broad():
    broad = make_group(1, features={"regions": [188], "age_span": None})
    narrow_geo = make_group(2, features={"regions": [5506], "age_span": None})
    with_interests = make_group(3, features={"regions": [188], "has_interests": True})
    ds = make_ds([broad, narrow_geo, with_interests])
    assert [f["entity"]["id"] for f in found(ds, "too_broad")] == [1]


def test_creative_overlap():
    g1, g2 = make_group(1, segment_ids=[5]), make_group(2, segment_ids=[5, 6])
    bs = [make_banner(11, 1, content_ids=[900]), make_banner(21, 2, content_ids=[900])]
    f = found(make_ds([g1, g2], bs), "creative_overlap")[0]
    assert f["evidence"] == {"groups": [1, 2], "shared_segments": [5]}
    g3 = make_group(3, segment_ids=[7])
    assert found(make_ds([g1, g3], [make_banner(11, 1, content_ids=[900]), make_banner(31, 3, content_ids=[900])]),
                 "creative_overlap") == []


def test_lal_small_source():
    segs = [{"id": 1, "name": "LAL", "lal": True, "size": 20}, {"id": 2, "name": "LAL2", "lal": True, "size": None},
            {"id": 3, "name": "Посетители", "lal": False, "size": 10}]
    assert [f["entity"]["id"] for f in found(make_ds([make_group(1, segment_ids=[1, 2, 3])], segments=segs),
                                           "lal_small_source")] == [1]


def test_stale_list():
    lists = [{"id": 1, "name": "Клиенты", "size": 900, "updated": "2026-05-01 00:00:00"},
             {"id": 2, "name": "Свежие", "size": 900, "updated": "2026-09-20 00:00:00"},
             {"id": 3, "name": "Неизвестно", "size": None, "updated": None}]
    assert [f["entity"]["id"] for f in found(make_ds([make_group(1)], users_lists=lists), "stale_list")] == [1]


def test_manual_placements():
    ds = make_ds([make_group(1, features={"auto_placements": False}), make_group(2)])
    assert [f["entity"]["id"] for f in found(ds, "manual_placements")] == [1]


def test_cap_too_early():
    w7 = stats(700, 7000, 70, 5)
    g = make_group(1, max_price_rub=50.0, windows={"7": w7, "14": None, "30": None})
    assert found(make_ds([g]), "cap_too_early")[0]["evidence"]["conversions_7d"] == 5
    ok = make_group(2, max_price_rub=50.0, windows={"7": stats(700, 7000, 70, 20), "14": None, "30": None})
    assert found(make_ds([ok]), "cap_too_early") == []
    assert found(make_ds([make_group(3)]), "cap_too_early") == []
```

- [ ] **Step 2: Убедиться, что падает** — `python -m pytest tests/test_rules_cd.py -v` — FAIL.

- [ ] **Step 3: Реализация** — блок после блока Task 3:

```python
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
    out = []
    for g in _live(ds):
        if not _active(g, th):
            continue
        f = _feat(g)
        regions = f.get("regions") or []
        country = not regions or set(regions) <= set(th["country_region_ids"])
        span = f.get("age_span") if f.get("age_span") is not None else 100
        narrowed = f.get("has_interests") or f.get("has_keywords") or g["segment_ids"] or f.get("has_communities")
        if country and span >= th["broad_age_span"] and not narrowed:
            out.append(finding("too_broad", "orange", "group", g,
                               "Слишком широкий таргетинг: вся страна, широкий возраст, без интересов",
                               {"regions": regions, "age_span": f.get("age_span")},
                               "Сузить: интересы, ключевые фразы, сообщества или сегменты",
                               effect_rub=0 if g.get("stats_missing") else g["stats"]["spent"]))
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
                    {"source_size": s["size"], "minimum": th["lal_min_source"]},
                    f"Пересобрать LAL на базе от {th['lal_min_source']} целевых действий")
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
        if not _active(g, th) or not g.get("max_price_rub"):
            continue
        w7 = (g.get("windows") or {}).get("7")
        conv7 = w7["conversions"] if w7 else g["stats"]["conversions"]
        if conv7 < th["learning_min_conversions_per_week"]:
            out.append(finding("cap_too_early", "orange", "group", g,
                               "«Предельная цена» включена до накопления статистики",
                               {"max_price_rub": g["max_price_rub"], "conversions_7d": conv7,
                                "needed": th["learning_min_conversions_per_week"]},
                               "Вернуть «Минимальную цену», пока не будет 15+ конверсий за неделю"))
    return out
```

- [ ] **Step 4: Прогнать** `python -m pytest tests/test_rules_cd.py -v` и `python -m pytest -q` — Expected: 10 passed; прочие зелёные (эталоны — Task 9).

- [ ] **Step 5: Commit**

```bash
git add scripts/diagnose.py tests/test_rules_cd.py
git commit -m "diagnose: правила v1 — ключевые и минус-фразы, исключения, широта, пересечения, LAL, базы, площадки, предельная цена"
```

---

### Task 5: Правила блоков E и F (объявления, эффективность) и окно 7 дней

**Files:**
- Modify: `scripts/diagnose.py` (блок `# ---------- v1: объявления ----------` после Task 4; рефакторинг `low_ctr`, `burnout`; новые `cpc_outlier`, `cpa_trend` в блоке эффективности)
- Test: `tests/test_rules_ef.py`

**Interfaces:**
- Consumes: Task 2–4 хелперы, `features.parse_dt`.
- Produces: `missing_ratios` 🟠, `no_video` ⚪, `stale_creatives` ⚪/🟠, `no_cta_text` ⚪, `generic_button` ⚪, `moderation_stuck` 🟠, `cpc_outlier` 🟠, `cpa_trend` 🟠; хелперы `_perf_entities(ds, th) -> list[(etype, ent, stats)]`, `_early(ds, g, th) -> bool`, `_top_groups(ds, th) -> list`.

- [ ] **Step 1: Тест**

`tests/test_rules_ef.py`:
```python
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


def test_stale_creatives_levels():
    def ds_with(age):
        g = make_group(1, stats=stats(1000, 10000, 100, 5))
        return make_ds([g], [make_banner(11, 1, age_days=age), make_banner(12, 1, age_days=age + 3)])
    assert found(ds_with(10), "stale_creatives") == []
    assert found(ds_with(20), "stale_creatives")[0]["level"] == "white"
    assert found(ds_with(40), "stale_creatives")[0]["level"] == "orange"


def test_cta_and_button():
    bs = [make_banner(11, 1, text="Лучшие велосипеды", cta="learn_more"),
          make_banner(12, 1, text="Запишитесь на тест-драйв", cta="sign_up")]
    ds = make_ds([make_group(1)], bs)
    assert found(ds, "no_cta_text")[0]["evidence"]["banners"] == [11]
    assert found(ds, "generic_button")[0]["evidence"]["banners"] == [11]


def test_cta_silent_without_text():
    assert found(make_ds([make_group(1)], [make_banner(11, 1), make_banner(12, 1)]), "no_cta_text") == []


def test_moderation_stuck():
    video = make_banner(11, 1, moderation_status="delayed", has_video=True, created="2026-10-01 00:00:00")
    image = make_banner(12, 1, moderation_status="delayed", created="2026-10-01 00:00:00")
    got = {f["entity"]["id"] for f in found(make_ds([make_group(1)], [video, image]), "moderation_stuck")}
    assert got == {11}                                       # as_of 2026-10-01T10:00: 10 ч > 8 для видео, < 24 для картинок


def test_cpc_outlier():
    gs = [make_group(1, stats=stats(1000, 20000, 200)), make_group(2, stats=stats(3000, 20000, 60))]
    ds = make_ds(gs, totals=stats(4000, 40000, 260))
    f = found(ds, "cpc_outlier")[0]
    assert f["entity"]["id"] == 2 and f["evidence"]["cpc"] == 50.0


def test_cpc_outlier_no_clicks():
    assert found(make_ds([make_group(1)], totals=stats()), "cpc_outlier") == []


def test_cpa_trend():
    w7, w30 = stats(1400, 7000, 70, 5), stats(3000, 30000, 300, 20)
    g = make_group(1, stats=w30, windows={"7": w7, "14": None, "30": w30})
    f = found(make_ds([g]), "cpa_trend")[0]
    assert f["evidence"]["cpa_7d"] == 280.0 and f["evidence"]["cpa_30d"] == 150.0


def test_early_groups_skipped():
    daily = [day(f"2026-09-{d:02d}", 0, 0, 0) for d in range(1, 26)] + \
            [day(f"2026-09-{d:02d}", 1000, 2000, 2) for d in range(26, 31)]          # 5 дней работы
    g = make_group(1, daily=daily, stats=stats(5000, 10000, 10))
    assert found(make_ds([g]), "low_ctr") == []


def test_early_banners_skipped():
    b = make_banner(11, 1, age_days=3, stats=stats(900, 10000, 20))
    assert found(make_ds([make_group(1)], [b, make_banner(12, 1)]), "low_ctr") == []
```

- [ ] **Step 2: Убедиться, что падает** — `python -m pytest tests/test_rules_ef.py -v` — FAIL.

- [ ] **Step 3: Реализация**

Импорт в `diagnose.py` (оба блока `try`): `from scripts.features import parse_dt` / `from features import parse_dt`.
Хелперы рядом с `_run_days`:
```python
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
```
`low_ctr` переписать на `_perf_entities`:
```python
@rule
def low_ctr(ds, th, ctx):
    return [finding("low_ctr", "orange", etype, ent, "Низкий CTR при достаточных показах",
                    {"ctr_pct": s["ctr_pct"], "shows": s["shows"], "threshold_pct": th["low_ctr_pct"]},
                    "Заменить креатив или проверить, той ли аудитории он показывается",
                    effect_rub=s["spent"], conf=confidence(s["clicks"], th))
            for etype, ent, s in _perf_entities(ds, th)
            if s["shows"] >= th["low_ctr_min_shows"] and s["ctr_pct"] < th["low_ctr_pct"]]
```
`burnout`: в начале цикла `if _early(ds, g, th): continue`.
Новые правила в блоке эффективности (после `burnout`):
```python
@rule
def cpc_outlier(ds, th, ctx):
    avg = ds["totals"].get("cpc")
    if not avg:
        return []
    out = []
    for etype, ent, s in _perf_entities(ds, th):
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
        if not w7 or not w30 or not w7["cpa"] or not w30["cpa"] \
                or w30["conversions"] < th["min_conversions_for_cpa"]:
            continue
        if w7["cpa"] >= w30["cpa"] * (1 + th["cpa_trend_rise"]):
            out.append(finding("cpa_trend", "orange", "group", g, "CPA за последнюю неделю заметно выше, чем за месяц",
                               {"cpa_7d": w7["cpa"], "cpa_30d": w30["cpa"], "frequency": g["stats"]["frequency"]},
                               "Проверить выгорание по частоте, обновить креативы или аудиторию",
                               effect_rub=max(w7["spent"] - w7["conversions"] * w30["cpa"], 0),
                               conf=confidence(w7["clicks"], th)))
    return out
```
Блок объявлений (после блока Task 4):
```python
# ---------- v1: объявления ----------

def _content_groups(ds, th):
    covered = _banner_groups(ds)
    return [(g, _active_banners(ds, g, th)) for g in _live(ds) if _active(g, th) and g["id"] in covered]


@rule
def missing_ratios(ds, th, ctx):
    out = []
    for g, active in _content_groups(ds, th):
        if not any(b.get("content_ids") for b in active):   # контент не получен — «не проверено»
            continue
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
    active = [b for g, bs in _content_groups(ds, th) for b in bs if b.get("content_ids")]
    if not active or any(b.get("has_video") for b in active):
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


@rule
def stale_creatives(ds, th, ctx):
    covered = _banner_groups(ds)
    out = []
    for g in _top_groups(ds, th):
        ages = [b["age_days"] for b in _active_banners(ds, g, th) if b.get("age_days") is not None]
        if g["id"] not in covered or not ages:
            continue
        newest = min(ages)
        if newest > th["creative_stale_red_days"]:
            level = "orange"
        elif newest > th["creative_stale_days"]:
            level = "white"
        else:
            continue
        out.append(finding("stale_creatives", level, "group", g, "Креативы в топ-группе давно не обновлялись",
                           {"newest_creative_age_days": newest},
                           "Обновить угол подачи: новый сценарий, боль или оффер (не только дизайн)"))
    return out


@rule
def no_cta_text(ds, th, ctx):
    words = [w.lower() for w in th["cta_words"]]
    out = []
    for g, active in _content_groups(ds, th):
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
    for b in ds["banners"]:
        created = parse_dt(b.get("created"))
        if b["status"] == "deleted" or b["moderation_status"] not in th["moderating_statuses"] or not created:
            continue
        hours = (as_of - created).total_seconds() / 3600
        norm = th["moderation_video_hours"] if b.get("has_video") else th["moderation_hours"]
        if hours > norm:
            out.append(finding("moderation_stuck", "orange", "banner", b, "Модерация затянулась",
                               {"hours": round(hours, 1), "norm_hours": norm},
                               "Проверить, не нужны ли документы (Настройки → Документы для модерации), иначе — в поддержку"))
    return out
```

- [ ] **Step 4: Прогнать** `python -m pytest tests/test_rules_ef.py -v` и `python -m pytest -q` — Expected: 14 passed; прочие зелёные (эталоны — Task 9).

- [ ] **Step 5: Commit**

```bash
git add scripts/diagnose.py tests/test_rules_ef.py
git commit -m "diagnose: правила v1 — форматы, видео, свежесть, призывы, кнопки, модерация, CPC, тренд CPA, окно 7 дней"
```

---

### Task 6: Чек-лист по блокам и вопросы к клиенту

**Files:**
- Create: `scripts/checks.py`, `scripts/questions.py`, `tests/test_checks.py`
- Modify: `scripts/diagnose.py` (`main` пишет `checklist` и `questions` в `findings.json`)

**Interfaces:**
- Consumes: `diagnose._live/_active/_measured/_run_days/_banner_groups/_feat/_layer`, находки с полем `rule`.
- Produces: `checks.BLOCKS: list[(id, title)]`, `checks.run_checks(ds, th, findings) -> {"blocks": [{"id","title","checks":[{"id","title","status","reason"}],"score":[n,m]}], "total":[n,m]}` (status ∈ pass/fail/unknown/na; балл = pass / (pass+fail)); `questions.build_questions(ds, th, findings) -> [{"id","text","important","why"}]`; в `findings.json` ключи `checklist` и `questions`.

- [ ] **Step 1: Тест**

`tests/test_checks.py`:
```python
from __future__ import annotations

from scripts.checks import BLOCKS, run_checks
from scripts.diagnose import diagnose, load_thresholds, main
from scripts.questions import build_questions
from tests.helpers import make_banner, make_ds, make_group, stats

TH = load_thresholds()


def checklist(ds):
    fs = diagnose(ds, TH)["findings"]
    return run_checks(ds, TH, fs)


def status(cl, cid):
    return next(c for b in cl["blocks"] for c in b["checks"] if c["id"] == cid)["status"]


def test_blocks_and_scores():
    cl = checklist(make_ds([make_group(1, budget_day_rub=200.0)], [make_banner(11, 1), make_banner(12, 1)]))
    assert [b["id"] for b in cl["blocks"]] == [b for b, _ in BLOCKS]
    assert status(cl, "D1") == "fail" and status(cl, "D4") == "pass"
    d = next(b for b in cl["blocks"] if b["id"] == "D")
    n, m = d["score"]
    assert m == sum(1 for c in d["checks"] if c["status"] in ("pass", "fail")) and n < m
    assert cl["total"] == [sum(b["score"][0] for b in cl["blocks"]), sum(b["score"][1] for b in cl["blocks"])]


def test_unknown_fields_are_not_fails():
    cl = checklist(make_ds([make_group(1)], [make_banner(11, 1), make_banner(12, 1)]))
    for cid in ("A2", "B6", "C4", "C6", "C7", "C10", "C15", "C16", "E1"):
        assert status(cl, cid) in ("unknown", "na"), cid


def test_funnels():
    gs = [make_group(1, objective="leadads", layer="warm"), make_group(2, objective="site_conversions")]
    cl = checklist(make_ds(gs, [make_banner(11, 1, urls=["https://vk.com/app5898182_-1#s=senler"])]))
    assert status(cl, "B1.leadform") == "pass" and status(cl, "B1.retargeting") == "pass"
    assert status(cl, "B1.site") == "pass" and status(cl, "B1.subscription") == "fail"
    assert status(cl, "B1.senler") == "pass"


def test_f4_unknown_on_short_period():
    ds = make_ds([make_group(1)], period={"from": "2026-09-21", "to": "2026-09-30", "days": 10})
    assert status(checklist(ds), "F4") == "unknown"


def test_metrika_block_unknown_without_metrika():
    assert status(checklist(make_ds([make_group(1)])), "G1") == "unknown"


def test_old_dataset_without_features():
    g = make_group(1)
    for k in ("features", "layer", "windows", "max_price_rub", "auto_utm"):
        g.pop(k)
    ds = make_ds([g], [make_banner(11, 1), make_banner(12, 1)])
    ds.pop("totals_windows")
    for b in ds["banners"]:
        for k in ("cta", "text", "urls", "ratios", "has_video", "content_ids", "age_days", "erid"):
            b.pop(k)
    fs = diagnose(ds, TH)["findings"]
    assert run_checks(ds, TH, fs)["blocks"]


def test_questions_flag_relevant():
    qs = {q["id"]: q for q in build_questions(make_ds([make_group(1, objective="leadads")]), TH, [])}
    assert qs["leadform"]["important"] is True and qs["community"]["important"] is False
    assert all(q["text"] for q in qs.values())


def test_main_writes_checklist(tmp_path):
    import json
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "dataset.json").write_text(json.dumps(make_ds([make_group(1)])), encoding="utf-8")
    assert main(["--workspace", str(ws)]) == 0
    out = json.loads((ws / "findings.json").read_text(encoding="utf-8"))
    assert out["checklist"]["blocks"] and out["questions"]
```
(В Task 1 helpers `make_ds` принимает `period=` через `**kw` — уже поддерживается `ds.update(kw)`.)

- [ ] **Step 2: Убедиться, что падает** — `python -m pytest tests/test_checks.py -v` — FAIL.

- [ ] **Step 3: `scripts/checks.py`**

```python
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
             no_banners if no_banners[0] else _known_banner(banners, "urls", "ссылки объявлений"))

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
            present(f"B1.{key}", f"Воронка: {title}", funnel_ok[key])
    by_rules("B2", "Есть тёплый, LAL и холодный слои", ["layer_missing", "no_retargeting"])
    spend = sum(g["stats"]["spent"] for g in _live(ds))
    by_rules("B3", "Доли бюджета по слоям", ["layer_share"], OK if spend else ("na", "нет расхода за период"))
    by_rules("B4", "1 сегмент = 1 кампания", ["mixed_plan"])
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
    by_rules("C11", "Таргетинг не слишком широкий", ["too_broad"])
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
    by_rules("E1", "Все соотношения сторон 1:1, 4:5, 16:9, 9:16", ["missing_ratios"], content_gate)
    by_rules("E2", "Видео используется", ["no_video"], content_gate)
    by_rules("E3", "Креативов в группе 2–5", ["banners_per_group", "no_active_banners"], no_banners)
    by_rules("E4", "Свежие креативы в топ-группах", ["stale_creatives"],
             no_banners if no_banners[0] else (OK if any(b.get("age_days") is not None for b in banners)
                                              else _unknown("дата создания объявления")))
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
```

- [ ] **Step 4: `scripts/questions.py`**

```python
"""
questions.py — вопросы к клиенту: то, что эксперты считают важным, а API не показывает.
Вопрос получает пометку «важно для вашего кабинета», если данные дают к нему повод.
"""
from __future__ import annotations

try:
    from scripts.diagnose import _active, _live
except ImportError:  # запуск напрямую
    from diagnose import _active, _live


def _objectives(ds, th) -> set:
    return {g["objective"] for g in _live(ds) if _active(g, th)}


def build_questions(ds: dict, th: dict, findings: list) -> list:
    obj = _objectives(ds, th)
    rules = {f["rule"] for f in findings}
    social = set(th["funnel_objectives"]["subscription"])
    q = [
        ("pixel_events", "Все ли ключевые действия на сайте (заявка, корзина, покупка, регистрация) передаются "
                         "в пиксель VK как события?",
         "есть группы с целью «конверсии на сайте»" if obj & set(th["pixel_objectives"]) else None),
        ("crm", "Передаются ли офлайн-конверсии из CRM в VK Рекламу?",
         "автостратегии не хватает конверсий" if "learning_starved" in rules else None),
        ("leadform", "Сколько полей в лид-форме и есть ли уточняющий вопрос («Какая услуга интересует?»)?",
         "в кабинете есть лид-формы" if "leadads" in obj else None),
        ("community", "Адаптировано ли сообщество под телефоны, заполнено ли меню, есть ли свежие посты и отзывы?",
         "трафик ведёт в сообщество" if obj & social else None),
        ("response_speed", "Получают ли менеджеры уведомления о заявках и отвечают ли в первые 15 минут? "
                           "Есть ли автоответ или мини-квиз?",
         "заявки приходят из VK" if obj & ({"leadads"} | social) else None),
        ("lead_quality", "Как вы делите заявки на целевые и нецелевые (A/B/C/D) и сколько стоит целевая (A+B)?", None),
        ("next_step", "Какой следующий шаг сделки (созвон, замер, демо, запись) и сколько он стоит?", None),
        ("landing", "Быстро ли грузится посадочная на телефоне и работает ли форма?",
         "Метрика показывает отказы или потерю кликов" if rules & {"high_bounce", "click_loss"} else None),
        ("audience_fit", "Совпадают ли аудитории и посыл креативов с вашим портретом клиента?", None),
        ("narrow_interests", "Не слишком ли узкие интересы в группах с маленьким охватом?", None),
    ]
    return [{"id": i, "text": t, "important": why is not None, "why": why} for i, t, why in q]
```

- [ ] **Step 5: `diagnose.main`** — после `result = diagnose(...)`:
```python
    from scripts.checks import run_checks          # здесь, а не наверху: checks импортирует diagnose
    from scripts.questions import build_questions
    th = load_thresholds(args.thresholds)
```
и до записи файла:
```python
    result["checklist"] = run_checks(ds, th, result["findings"])
    result["questions"] = build_questions(ds, th, result["findings"])
```
(перенести загрузку порогов в `th` и передать её в `diagnose(ds, th)`; блоки `try/except ImportError` для прямого запуска — `from checks import run_checks`, `from questions import build_questions`). В печать добавить `балл {total[0]} / {total[1]}`.

- [ ] **Step 6: Прогнать** `python -m pytest tests/test_checks.py -v` и `python -m pytest -q` — Expected: 8 passed; прочие зелёные.

- [ ] **Step 7: Commit**

```bash
git add scripts/checks.py scripts/questions.py scripts/diagnose.py tests/test_checks.py
git commit -m "checks: чек-листы A–G с баллом, вопросы к клиенту в findings.json"
```

---

### Task 7: Отчёт — чек-лист, вопросы, «не проверено»

**Files:**
- Modify: `scripts/render_report.py`, `tests/test_render_report.py`

**Interfaces:**
- Consumes: `findings.json` с `checklist` и `questions` (могут отсутствовать — старые файлы).
- Produces: `render()` выводит разделы в порядке спеки: шапка → плитки → чек-лист с баллами → находки → вопросы → структура → ограничения; `limitations(ds, collect_log, checklist=None)` добавляет «Не проверено «<title>»: <reason>» для статуса `unknown`.

- [ ] **Step 1: Тест** — добавить в `tests/test_render_report.py`:
```python
from scripts.checks import run_checks
from scripts.diagnose import load_thresholds as _lt
from scripts.questions import build_questions


def full_sample():
    ds, fs = sample()
    th = _lt()
    fs["checklist"] = run_checks(ds, th, fs["findings"])
    fs["questions"] = build_questions(ds, th, fs["findings"])
    return ds, fs


def test_checklist_and_score_rendered():
    ds, fs = full_sample()
    html = render(ds, fs)
    n, m = fs["checklist"]["total"]
    assert f"{n} / {m}" in html and "Техническая настройка" in html and "✅" in html and "❌" in html


def test_questions_rendered():
    ds, fs = full_sample()
    html = render(ds, fs)
    assert "Вопросы к клиенту" in html and "важно для вашего кабинета" in html


def test_unknown_checks_go_to_limitations():
    ds, fs = full_sample()
    lines = limitations(ds, None, fs["checklist"])
    assert any(x.startswith("Не проверено «") for x in lines)


def test_finding_shows_check_id():
    ds, fs = full_sample()
    assert "проверка F6" in render(ds, fs)


def test_old_findings_without_checklist():
    ds, fs = sample()
    assert "Чек-лист" not in render(ds, fs)
```

- [ ] **Step 2: Убедиться, что падает** — FAIL.

- [ ] **Step 3: Реализация**

CSS — добавить: `.score{font-weight:600}.st-pass{color:#1a7f37}.st-fail{color:#c62828}.st-unk{color:#888}`.
```python
STATUS_ICON = {"pass": "✅", "fail": "❌", "unknown": "—", "na": "—"}


def _checklist_html(cl) -> str:
    if not cl:
        return ""
    n, m = cl["total"]
    parts = [f"<h2>Чек-лист аудита — общий балл <span class='score'>{n} / {m}</span></h2>"]
    for b in cl["blocks"]:
        bn, bm = b["score"]
        rows = "".join(
            f"<tr><td>{escape(c['id'])}</td><td>{escape(c['title'])}</td>"
            f"<td class='st-{'unk' if c['status'] in ('unknown', 'na') else c['status']}'>{STATUS_ICON[c['status']]}</td>"
            f"<td class='muted'>{escape(c['reason'])}</td></tr>" for c in b["checks"])
        parts.append(f"<h3>{escape(b['id'])}. {escape(b['title'])} — {bn} / {bm if bm else '—'}</h3>"
                     f"<table>{rows}</table>")
    return "".join(parts)


def _questions_html(qs) -> str:
    if not qs:
        return ""
    items = "".join(
        f"<li>{escape(q['text'])}" + (f" <b>— важно для вашего кабинета: {escape(q['why'])}</b>" if q["important"] else "")
        + "</li>" for q in sorted(qs, key=lambda q: not q["important"]))
    return f"<h2>Вопросы к клиенту</h2><ul>{items}</ul>"
```
`limitations(ds, collect_log=None, checklist=None)` — в конце:
```python
    for b in (checklist or {}).get("blocks", []):
        for c in b["checks"]:
            if c["status"] == "unknown":
                lines.append(f"Не проверено «{c['title']}»: {c['reason']}")
```
`_finding_html`: в строку `class="muted"` добавить `· проверка {escape(f['check'])}` если `f.get("check")`.
`render`: после `_metrika_total_html(...)` вставить `_checklist_html(findings.get("checklist"))`; после блоков находок — `_questions_html(findings.get("questions"))`; вызов `limitations(ds, collect_log, findings.get("checklist"))`.

- [ ] **Step 4: Прогнать** `python -m pytest tests/test_render_report.py -v` и `python -m pytest -q` — Expected: все зелёные.

- [ ] **Step 5: Commit**

```bash
git add scripts/render_report.py tests/test_render_report.py
git commit -m "render_report: чек-лист с баллами, вопросы к клиенту, непроверенное в ограничениях"
```

---

### Task 8: Сбор полей и документация

**Files:**
- Modify: `scripts/collect.py` (`BANNER_FIELDS`, `GROUP_FIELDS`), `SKILL.md`, `references/diagnostic-rules.md`, `references/report-template.md`, `references/mcp-read-tools.md`, `references/limitations.md`, `tests/test_skill_docs.py`
- Create: `references/client-questions.md`

**Interfaces:**
- Consumes: всё выше; `diagnose.RULES`, `diagnose.CHECK_OF`, `checks.BLOCKS`, `questions.build_questions`, `features.*_KEYS`.
- Produces: документация, совпадающая с кодом; тесты доков.

- [ ] **Step 1: Тест доков** — добавить в `tests/test_skill_docs.py`:
```python
from scripts import features
from scripts.diagnose import CHECK_OF


def test_every_rule_has_check():
    assert {fn.__name__ for fn in RULES} - {"scale_leader"} <= set(CHECK_OF)


def test_check_ids_documented():
    doc = (SKILL_ROOT / "references" / "diagnostic-rules.md").read_text(encoding="utf-8")
    for cid in set(CHECK_OF.values()):
        assert f"`{cid}`" in doc, cid


def test_api_field_candidates_listed_for_live_gate():
    doc = (SKILL_ROOT / "references" / "mcp-read-tools.md").read_text(encoding="utf-8")
    for name in ("KEYWORD_KEYS", "MINUS_KEYS", "KW_PERIOD_KEYS", "KW_EXPAND_KEYS", "EXCLUDE_KEYS", "COMMUNITY_KEYS",
                 "AUTO_PADS_KEYS", "DEVICE_ADJ_KEYS", "DEMO_ADJ_KEYS", "ERID_KEYS", "AUTO_UTM_KEYS",
                 "PLAN_BUDGET_KEYS", "SIZE_KEYS", "UPDATED_KEYS"):
        for key in getattr(features, name):
            assert f"`{key}`" in doc, key


def test_client_questions_documented():
    from scripts.questions import build_questions
    from tests.helpers import make_ds
    doc = (SKILL_ROOT / "references" / "client-questions.md").read_text(encoding="utf-8")
    for q in build_questions(make_ds([]), load_thresholds(), []):
        assert q["text"] in doc
```
(импорт `from scripts.diagnose import load_thresholds` добавить в шапку файла, если его нет.)

- [ ] **Step 2: Убедиться, что падает** — FAIL.

- [ ] **Step 3: `scripts/collect.py`**

```python
GROUP_FIELDS = ("id,name,status,objective,package_id,ad_plan_id,budget_limit,budget_limit_day,targetings,"
                "issues,created,updated,date_start,date_end,delivery,autobidding_mode,max_price,priced_goal,"
                "enable_utm,auto_utm")
BANNER_FIELDS = ("id,name,campaign_id,status,moderation_status,moderation_reasons,issues,delivery,"
                 "textblocks,urls,content,created,updated,user_can_request_remoderation,call_to_action,"
                 "erid,ord_token,ord_erid")
```
(неизвестные имена сервер игнорирует — безопасно.)

- [ ] **Step 4: Документация**

- `references/diagnostic-rules.md` — переписать целиком по спеке v1, раздел 2: для каждого блока таблица «`id` проверки · `правило` · уровень · когда · ключи порогов · источник ([01]–[04], [D], решение Лизы)». Обязательны: все id из `CHECK_OF` в обратных кавычках (`B1.retargeting` и т. д.), все правила из `diagnose.RULES` в обратных кавычках, все ключи `thresholds.json`, раздел «Решения по расхождениям экспертов» (6 пунктов из спеки, раздел 4), раздел «Статусы чек-листа» (pass / fail / unknown / na, балл n / m).
- `references/report-template.md` — порядок разделов (спека, раздел 1), чек-лист с баллами, «Вопросы к клиенту», `client_note`.
- `references/client-questions.md` — все 10 вопросов из `questions.py` дословно + условие пометки «важно».
- `references/mcp-read-tools.md` — в «Проверить живым прогоном» добавить таблицу «признак → ключи-кандидаты» со всеми ключами из констант `features.py` в обратных кавычках, `call_to_action`, `country_region_ids` (188 = Россия?), значения `moderating_statuses`, роли `content` для соотношений.
- `references/limitations.md` — строка «Не проверено «<проверка>»: <причина>» в разделе «По прогону».
- `SKILL.md`:
  - Шаг 5: `diagnose` пишет находки, чек-лист и вопросы к клиенту в `findings.json`.
  - Шаг 6 (гейт): в чат — общий балл, баллы блоков, топ находок; вопросы «важно для вашего кабинета» задать клиенту сразу, ответы — `client_note` у связанных находок или в сводку.
  - Шаг 7: отчёт содержит чек-лист, находки, вопросы, ограничения.
  - SKILL.md ≤ 400 строк.

- [ ] **Step 5: Прогнать** `python -m pytest -q` — Expected: все зелёные (эталоны — Task 9).

- [ ] **Step 6: Commit**

```bash
git add scripts/collect.py SKILL.md references tests/test_skill_docs.py
git commit -m "docs: методика v1 в справочниках, вопросы к клиенту, поля для живого прогона; collect — новые поля"
```

---

### Task 9: Эталоны под методику v1

**Files:**
- Modify: `evals/fixtures.py`, `evals/run_evals.py`, `tests/test_evals.py`

**Interfaces:**
- Consumes: `build_dataset(..., as_of=)`, `diagnose`, `run_checks`.
- Produces: метаданные кабинета: `expected` + `scope` (None — точное совпадение по всем правилам; множество — сравнение только по правилам из scope) или `expected_rules` (каждое правило должно сработать хотя бы раз); `as_of`. `run(name, workdir) -> (found, expected)` — сравнение по договорённости кабинета; новый `checklist_of(name, workdir)`.

- [ ] **Step 1: Тест** — `tests/test_evals.py` заменить:
```python
from __future__ import annotations

import pytest

from evals.fixtures import CABINETS
from evals.run_evals import checklist_of, run

NEW_RULES = {"ord_missing", "utm_missing", "no_retargeting", "layer_missing", "layer_share", "mixed_plan",
             "fragmentation", "no_cbo", "naming", "no_minus_phrases", "kw_period", "kw_expand_off", "no_exclusions",
             "too_broad", "creative_overlap", "lal_small_source", "stale_list", "manual_placements", "cap_too_early",
             "missing_ratios", "no_video", "stale_creatives", "no_cta_text", "generic_button", "moderation_stuck",
             "cpc_outlier", "cpa_trend"}


@pytest.mark.parametrize("name", sorted(CABINETS))
def test_cabinet(name, tmp_path):
    found, expected = run(name, tmp_path)
    assert found == expected


def test_cabinets_listed():
    assert set(CABINETS) == {"healthy", "leaking", "hygiene", "empty", "path_a_partial", "expert_1", "expert_2"}


def test_every_new_rule_fires_somewhere(tmp_path):
    fired = set()
    for name in ("expert_1", "expert_2"):
        found, _ = run(name, tmp_path)          # для экспертных кабинетов found — множество сработавших правил
        fired |= found
    assert NEW_RULES <= fired, sorted(NEW_RULES - fired)


def test_healthy_checklist_has_no_fails(tmp_path):
    cl = checklist_of("healthy", tmp_path)
    fails = [c["id"] for b in cl["blocks"] for c in b["checks"] if c["status"] == "fail"]
    assert fails == ["B1.subscription", "B1.leadform", "B1.senler"]   # воронок этих нет — это не ошибка кабинета


def test_expert_checklist_mostly_fails(tmp_path):
    cl = checklist_of("expert_1", tmp_path)
    fails = {c["id"] for b in cl["blocks"] for c in b["checks"] if c["status"] == "fail"}
    assert {"A2", "A3", "B4", "B5", "B6", "B7", "C7", "C8", "C9", "C10", "C11", "C12", "C13", "C14", "C15",
            "D2", "E1", "E2", "E4", "E6", "E7", "E8", "E9", "F2", "F3", "F4"} <= fails
```

- [ ] **Step 2: Убедиться, что падает** — FAIL.

- [ ] **Step 3: `evals/fixtures.py`** — расширить строителя и добавить кабинеты.

`write_cabinet` — новая сигнатура (старые вызовы остаются совместимы):
```python
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
    summary, daily, bsum = [], [], []
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
            bsum.append({"id": b["id"], "total": block(tot[0] * share, int(tot[1] * share), int(tot[2] * share),
                                                       int(tot[3] * share), frequency=freq)})
    write_call(raw, "vk_ads_statistics_summary", {"entity": "campaigns", "ids": ",".join(map(str, live)), **P},
               {"items": summary})
    write_call(raw, "vk_ads_statistics_day", {"entity": "campaigns", "ids": ",".join(map(str, live)), **P},
               {"items": daily})
    write_call(raw, "vk_ads_statistics_summary", {"entity": "banners", **P}, {"items": bsum})


FREQ6: set = set()   # группы с частотой 6 (красное выгорание) — заполняет кабинет expert_1
```
(Сумма по объявлениям при неравных долях может разойтись с группой на копейки — `share` одинаковый, расход делится поровну; для `int()` показов/кликов это не влияет на проверку расхода.)

Кабинет `healthy` — полностью по методике (точное совпадение по всем правилам):
```python
AGES = list(range(25, 46))


def _good_banners(gid, prefix):
    roles = [{"image_1080x1080": {"id": gid * 1000 + 1}},
             {"image_1080x1350": {"id": gid * 1000 + 2}, "image_1920x1080": {"id": gid * 1000 + 3}},
             {"video_portrait_9_16_15s": {"id": gid * 1000 + 4}}]
    return [{"id": gid * 100 + i, "name": f"{prefix}_креатив{i}_Мск", "status": "active", "moderation_status": "allowed",
             "created": "2026-09-20 10:00:00", "call_to_action": "sign_up",
             "textblocks": {"text_90": {"text": "Запишитесь на бесплатную консультацию"}},
             "urls": {"primary": {"id": 7, "url": "https://site.ru/?utm_source=vk_ads&utm_campaign={{campaign_id}}"}},
             "content": roles[i]} for i in range(3)]


def healthy(raw: Path) -> dict:
    plans = [{"id": 1, "name": "ретарг_сайт_Мск", "budget_limit_day": 300000},
             {"id": 2, "name": "LAL_клиенты_Мск", "budget_limit_day": 300000},
             {"id": 3, "name": "холод_интересы_Мск", "budget_limit_day": 300000}]
    segs = [{"id": 101, "name": "Посетители сайта 30 дней"}, {"id": 102, "name": "LAL на клиентов 3%"}]
    base_t = {"geo": {"regions": [1]}, "age": {"age_list": AGES}}
    groups = [group(1, [1], ad_plan_id=1, name="ретарг_сайт_Мск_Ж25-45_моб",
                    targetings={**base_t, "sex": ["female"], "segments": [101]}),
              group(2, [1], ad_plan_id=2, name="LAL_клиенты_Мск_25-45",
                    targetings={**base_t, "segments": [102]}),
              group(3, [1], ad_plan_id=3, name="холод_велоспорт_Мск_25-45",
                    targetings={**base_t, "interests": [1001]})]
    bs = {1: _good_banners(1, "ретарг"), 2: _good_banners(2, "LAL"), 3: _good_banners(3, "холод")}
    write_cabinet(raw, groups, bs, {1: (440, 20000, 300, 4), 2: (440, 20000, 300, 4), 3: (220, 10000, 150, 2)},
                  plans=plans, segment_items=segs)
    return {**P, "target_cpa": 300.0, "expected": set(), "scope": None, "as_of": "2026-10-01T10:00"}
```
Старые кабинеты получают `scope` и `as_of`:
```python
LEAKING_SCOPE = {"spend_no_conversions", "cpa_above_target"}
HYGIENE_SCOPE = {"low_budget", "rejected_banners", "duplicate_targeting", "autodelete_risk", "banners_per_group",
                 "unused_segments"}
```
- `leaking`: `"scope": LEAKING_SCOPE, "as_of": "2026-10-01T10:00"` (expected прежний);
- `hygiene`: `"scope": HYGIENE_SCOPE`, expected прежний **плюс** `("low_budget", 2), ("low_budget", 3), ...`? — нет: бюджет 2000 ₽ у остальных групп, белая полоса 300–500 ₽ их не задевает; expected прежний;
- `empty`: `"scope": None` (точное — пустое);
- `path_a_partial`: `"scope": {"no_active_banners", "banners_per_group", "missing_ratios", "stale_creatives"}`, expected `set()`.

Кабинеты `expert_1`, `expert_2` — `"expected_rules"`:
```python
def expert_1(raw: Path) -> dict:
    """Кабинет, где методика v1 находит почти всё: каждое новое правило срабатывает хотя бы раз."""
    FREQ6.clear()
    FREQ6.add(9)
    plans = [{"id": 1, "name": "Кампания 1", "budget_limit_day": 0},
             {"id": 2, "name": "Новая кампания", "budget_limit_day": 0}]
    segs = [{"id": 501, "name": "LAL клиенты", "size": 20}]
    users = [{"id": 601, "name": "Клиенты 2025", "entries_count": 300, "updated": "2026-01-15 00:00:00"}]
    kw_t = {"geo": {"regions": [5506]}, "keywords": ["купить велосипед"], "minus_phrases": [], "search_period": 30,
            "expand_keywords": False, "segments_exclude": [], "auto_placements": False}
    groups = [group(1, [5506], ad_plan_id=1, name="Группа 1", targetings=kw_t),
              group(2, [188], ad_plan_id=1, name="Группа 2", targetings={"geo": {"regions": [188]}}),   # широкий
              group(3, [5506], ad_plan_id=2, name="Группа 3", targetings={"geo": {"regions": [5506]}, "segments": [501]}),
              group(4, [5506], ad_plan_id=2, name="Группа 4", targetings={"geo": {"regions": [5506]}, "segments": [501]},
                    max_price="50.00"),
              group(9, [5506], ad_plan_id=2, name="Группа 9", targetings={"geo": {"regions": [5506]}, "interests": [7]})]
    groups += [group(20 + i, [5506 + i], ad_plan_id=2, name=f"Группа {20 + i}", budget_kop=15000,
                     targetings={"geo": {"regions": [5506 + i]}, "interests": [i]}) for i in range(10)]
    old = "2026-08-01 10:00:00"
    common = {"status": "active", "created": old, "call_to_action": "learn_more", "erid": "",
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
```
`CABINETS` дополнить `"expert_1": expert_1, "expert_2": expert_2`. В начале `healthy`, `leaking`, `hygiene`, `empty`, `path_a_partial` — `FREQ6.clear()`.

- [ ] **Step 4: `evals/run_evals.py`**

```python
from scripts.checks import run_checks


def _dataset(name, workdir):
    raw = Path(workdir) / name / "raw"
    meta = CABINETS[name](raw)
    ds = build_dataset(raw, meta["date_from"], meta["date_to"], meta["target_cpa"], as_of=meta.get("as_of"))
    assert check_sanity(ds) == [], f"{name}: синтетика не проходит проверку здравого смысла: {check_sanity(ds)}"
    return ds, meta


def run(name: str, workdir: Path):
    ds, meta = _dataset(name, workdir)
    findings = diagnose(ds, load_thresholds())["findings"]
    if "expected_rules" in meta:
        fired = {f["rule"] for f in findings}
        return (meta["expected_rules"] & fired), meta["expected_rules"]
    found = {(f["rule"], f["entity"]["id"]) for f in findings}
    if meta.get("scope") is not None:
        found = {x for x in found if x[0] in meta["scope"]}
    return found, meta["expected"]


def checklist_of(name: str, workdir: Path) -> dict:
    ds, _ = _dataset(name, workdir)
    th = load_thresholds()
    return run_checks(ds, th, diagnose(ds, th)["findings"])
```
(`main` — без изменений; для кабинетов с `expected_rules` печатать недостающие правила.) `test_every_new_rule_fires_somewhere` упростить под этот `run`: `fired |= found` для expert-кабинетов (там `found` — множество правил).

- [ ] **Step 5: Прогнать** `python -m pytest tests/test_evals.py -v`, `python -m evals.run_evals`, затем `python -m pytest -q`.
Если `healthy` даёт находку — сначала проверить, верна ли она по методике (тогда поправить синтетику, чтобы кабинет был по-настоящему здоровым), иначе — чинить правило с юнит-тестом. Если `expert_1` не вызывает какое-то правило — поправить синтетику под условие правила (числа в комментариях), правила не ослаблять. Для `test_healthy_checklist_has_no_fails` допустимые ❌ — только отсутствующие воронки B1 (подписка, лид-форма, Senler).

- [ ] **Step 6: Commit**

```bash
git add evals tests/test_evals.py
git commit -m "evals: здоровый кабинет по методике v1, скоупы старых кабинетов, экспертные кабинеты на все новые правила"
```

---

## Self-review (выполнено при написании)

- **Покрытие спеки:** формат отчёта (раздел 1) — Tasks 6–7; проверки A–G (раздел 2) — Tasks 2–6 (A2/A3 — Task 3, B1–B7 — Tasks 3, 6; C — Tasks 4, 6; D — Tasks 2, 4; E — Tasks 2, 5, 6; F — Tasks 2, 5, 6; G — существующие правила + Task 6); вопросы (раздел 3) — Task 6, док — Task 8; решения Лизы (раздел 4) — Task 2 (пороги), Task 5 (окно 7 дней, свежесть); данные (раздел 5) — Tasks 1, 8; тесты и эталоны (раздел 6) — все задачи + Task 9. Расширение относительно спеки: проверки B8, B9, D4, F6 — существующие правила 0.1 получили место в чек-листе (спека: «как сейчас»).
- **Согласованность имён:** `features`, `layer`, `windows`, `max_price_rub`, `auto_utm` (Task 1) читаются в Tasks 3–6 через `_feat`/`_layer`/`.get`; `CHECK_OF` (Task 2) покрывает все правила Tasks 3–5 и проверки Task 6; `run_checks`/`build_questions` (Task 6) — в Tasks 7–9.
- **Плейсхолдеры:** нет; документы Task 8 описаны составом и тестами, которые их проверяют.
