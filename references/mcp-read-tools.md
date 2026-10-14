# Инструменты MCP vk-ads для аудита (только чтение)

Сервер vk-ads-mcp 0.11.0. Иерархия: `ad_plan` (UI «Кампания») → `campaign` (UI «Группа») → `banner` (UI «Объявление»).
`campaigns_*` и `ad_groups_*` — один ресурс; используем `campaigns_*`.

## Порядок сбора (путь A повторяет его руками)

Формат файла в `raw/` — `<инструмент>__<NNN>.json` = `{"tool", "arguments", "response"}`; `NNN` — целое число.
В `raw/` других файлов быть не должно, проверка — `python -m scripts.raw_store check --workspace <ws>`.
Для списков — по файлу на страницу либо один общий файл `{"items": [...]}`.

| # | Инструмент | Аргументы | Файл raw/ |
|---|---|---|---|
| 1 | `vk_ads_ad_plans_list` | `fields=id,name,status,objective,budget_limit,budget_limit_day,date_start,date_end,created,updated`, `limit=250`, `offset` | `vk_ads_ad_plans_list__NNN.json` |
| 2 | `vk_ads_campaigns_list` | `fields=id,name,status,objective,package_id,ad_plan_id,budget_limit,budget_limit_day,targetings,issues,created,updated,date_start,date_end,delivery,autobidding_mode,max_price,priced_goal` | `vk_ads_campaigns_list__NNN.json` |
| 3 | `vk_ads_banners_list` | `campaign_id=<группа>`, `fields=id,name,campaign_id,status,moderation_status,moderation_reasons,issues,delivery,textblocks,urls,content,created,updated,user_can_request_remoderation` | по файлу на группу |
| 4 | `vk_ads_remarketing_segments_list`, `vk_ads_users_lists_list`, `vk_ads_remarketing_pixels_list` | — | по файлу |
| 5 | `vk_ads_statistics_summary` | `entity=campaigns`, `ids` ≤ 50 через запятую, `date_from`, `date_to`, `metrics=all` | по файлу на пачку |
| 6 | `vk_ads_statistics_day` | то же | по файлу на пачку |
| 7 | `vk_ads_statistics_summary` | `entity=banners`, `ids` объявлений | по файлу на пачку |

Путь B (`python -m scripts.collect`) делает то же самое сам; в режиме `--mode quick` — только шаги 1, 2 и 6.
Режим `quick` на пути A — те же шаги 1, 2 и 6 (статистика по живым группам).

Без `fields` списки отдают только `id`, `name`, `package_id`. Неизвестные имена в `fields` сервер игнорирует.

`vk_ads_packages_list` (обновление сервера 2026-10-14) по умолчанию отдаёт только пакеты, на которых можно
создать группу (`can_create`); пакет уже существующей группы может там отсутствовать — для разбора её пакета
нужен `include_unavailable=true`. Аудиту пакеты не нужны для сбора, только для пояснений.

## Ответы

- Списки: `{"count"?, "items": [...], "offset"?}` — `count` бывает не у всех.
- Статистика: `{"items": [{"id", "total": {"base": {...}, "uniques": {...}, ...}, "rows"?: [{"date", "base", ...}]}], "total": {...}}`;
  `rows` — только у `statistics_day`. Сущностей без показов в `items` может не быть — это нули.
- `base`: `shows`, `clicks`, `goals`, `spent` (строка, ₽), `cpm`/`cpc`/`cpa` (строки), `ctr`/`cr` (доли), `vk.{goals,result,cpa,cr,cpr}`.
- `uniques`: `reach`, `frequency`.
- Группа: `budget_limit`/`budget_limit_day` — **копейки**; `max_price` — строка в рублях; `issues: [{"code","message"}]`;
  `delivery`; `autobidding_mode` (например `max_goals`); `targetings` (`geo.regions`, `age.age_list`, `sex`, `pads`, `fulltime`…).
- Ошибка: `isError` + текст, часто JSON `{"error": true, "status": N, "detail": ...}`.

Единицы (копейки → рубли, доли → проценты) приводит только `scripts/normalize.py`; в `raw/` ответы кладутся как есть.

## Проверить живым прогоном (до релиза 0.1.0)

- Конверсии: `base.goals` (сайт) и `base.vk.result` (лид-формы, сообщество) — верно ли по целям (`CONVERSION_SOURCE` в `scripts/normalize.py`).
- `ctr` — доля (проверка `ctr_units` в `check_sanity`); допуск проверки против округления на стороне VK.
- Сколько `ids` принимает один вызов статистики (сейчас пачки по 50).
- Имена полей объявления: `moderation_status`, `moderation_reasons`, `user_can_request_remoderation`.
- Реальный набор значений `autobidding_mode`: пока виден только `max_goals`; значение ручного режима неизвестно
  (автостратегия определяется по списку `auto_bidding_modes` в `thresholds.json`).
- Смысл `budget_limit_day` для «без лимита»: 0 или null (правила `low_budget` и `scale_leader` считают оба значения «лимита нет»).
