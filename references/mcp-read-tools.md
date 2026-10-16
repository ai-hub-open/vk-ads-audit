# Инструменты MCP vk-ads для аудита (только чтение)

Сервер vk-ads-mcp 0.11.0. Иерархия: `ad_plan` (UI «Кампания») → `campaign` (UI «Группа») → `banner` (UI «Объявление»).
`campaigns_*` и `ad_groups_*` — один ресурс; используем `campaigns_*`.

## Порядок сбора (путь A повторяет его руками)

**Предполётка (до сбора):** `vk_ads_auth_check`, `vk_ads_account_info` (id пользователя), затем один вызов
`vk_ads_statistics_day(entity="users", ids=<id пользователя>, date_from, date_to)`. Расход за период 0 — сказать
пользователю сразу и предложить другой период или кабинет; собирать нечего. Путь B делает это сам (код выхода 5), в
`raw/` предполётка не пишется.

Формат файла в `raw/` — `<инструмент>__<NNN>.json` = `{"tool", "arguments", "response"}`; `NNN` — целое число.
В `raw/` других файлов быть не должно, проверка — `python -m scripts.raw_store check --workspace <ws>`.
Для списков — по файлу на страницу либо один общий файл `{"items": [...]}`, но не оба сразу: точные дубли по `id`
отбрасываются, расходящиеся — ошибка «raw/ не разобран».

| # | Инструмент | Аргументы | Файл raw/ |
|---|---|---|---|
| 1 | `vk_ads_ad_plans_list` | `fields=id,name,status,objective,budget_limit,budget_limit_day,date_start,date_end,created,updated`, `limit=250`, `offset` | `vk_ads_ad_plans_list__NNN.json` |
| 2 | `vk_ads_campaigns_list` | `fields=id,name,status,objective,package_id,ad_plan_id,budget_limit,budget_limit_day,targetings,issues,created,updated,date_start,date_end,delivery,autobidding_mode,max_price,priced_goal,enable_utm,utm` | `vk_ads_campaigns_list__NNN.json` |
| 3 | `vk_ads_banners_list` | `campaign_id=<группа>`, `fields=id,name,campaign_id,status,moderation_status,moderation_reasons,issues,delivery,textblocks,urls,content,created,updated,user_can_request_remoderation,call_to_action,ord_marker` | по файлу на группу |
| 4 | `vk_ads_remarketing_segments_list`, `vk_ads_users_lists_list`, `vk_ads_remarketing_pixels_list` | — | по файлу |
| 5 | `vk_ads_statistics_day` | `entity=campaigns`, `ids` ≤ 50 через запятую (все группы, кроме удалённых), `date_from`, `date_to`, `metrics=all` | по файлу на пачку |
| 6 | `vk_ads_statistics_day` | `entity=banners`, `ids` объявлений (≤ 50 в пачке) | по файлу на пачку |
| 7 | `vk_ads_statistics_summary` | `entity=campaigns`, как в шаге 5 — **необязательно**: охват и частота только у групп, созданных внутри периода | по файлу на пачку |

Итоги периода (расход, показы, клики, конверсии) считаются **суммой строк `statistics_day`**: `statistics_summary`
игнорирует `date_from` / `date_to` и отдаёт всё время (баг MCP, передан платформе). Поэтому и у групп, и у объявлений
итоги берутся из дней (шаги 5 и 6); сводка нужна лишь ради охвата и частоты, и скрипты берут их только у групп, чья
жизнь целиком в периоде (создана не раньше `date_from`, показы сводки совпадают с суммой дней). У остальных частота —
«не определена», правила выгорания молчат, а в «Ограничениях» появляется строка. Старые `raw/` со сводками объявлений
по-прежнему читаются.

Путь B (`python -m scripts.collect`) делает то же самое сам; в режиме `--mode quick` — только предполётка и шаги 1, 2, 5.
Режим `quick` на пути A — те же шаги 1, 2 и 5 (статистика по живым группам).

**Большие ответы.** Claude Desktop отдаёт ответ длиннее лимита модели файлом (список групп на ~56 групп — около 260 КБ):
вставить его руками в `raw/` нельзя. Файл кладётся командой
`python -m scripts.raw_store import --workspace <ws> --tool <инструмент> --arguments '<json аргументов вызова>' --from <файл>`
(в PowerShell аргументы удобнее файлом — `--arguments-file`); она проверяет, что файл — JSON-объект, и отказывает
по-русски на обрезанных и нетекстовых файлах.

Без `fields` списки отдают только `id`, `name`, `package_id`. Неизвестное имя в `fields` сервер отвергает ошибкой
`400 unknown_resource_fields` (живой прогон 16.10.2026). Поэтому путь B (`scripts/collect.py`) просит расширенный
список (`GROUP_FIELDS`, `BANNER_FIELDS` — к `GROUP_FIELDS_BASE`, `BANNER_FIELDS_BASE` добавлены `enable_utm`, `utm`,
`call_to_action`, `ord_marker`) и при ошибке ответа повторяет вызов один раз с базовым списком; оба вызова остаются в
`collect_log.json`. На пути A — те же расширенные строки; отказал сервер — повтори с базовыми.

`vk_ads_packages_list` (обновление сервера 2026-10-14) по умолчанию отдаёт только пакеты, на которых можно
создать группу (`can_create`); пакет уже существующей группы может там отсутствовать — для разбора её пакета
нужен `include_unavailable=true`. Аудиту пакеты не нужны для сбора, только для пояснений.

## Ответы

- Списки: `{"count"?, "items": [...], "offset"?}` — `count` бывает не у всех.
- Статистика: `{"items": [{"id", "total": {"base": {...}, "uniques": {...}, ...}, "rows"?: [{"date", "base", ...}]}], "total": {...}}`;
  `rows` — только у `statistics_day`. Сущностей без показов в `items` может не быть — это нули.
  `statistics_summary` игнорирует период (всё время), `statistics_day` его уважает; глубина дневной истории около 430 дней.
- `base`: `shows`, `clicks`, `goals`, `spent` (строка, ₽), `cpm`/`cpc`/`cpa` (строки), `ctr`/`cr` — **уже проценты**
  (608 кликов на 117 469 показов → `ctr: 0.5176` = 0,52 %), `vk.{goals,result,cpa,cr,cpr}`.
- `uniques`: `reach`, `frequency`.
- Группа: `budget_limit`/`budget_limit_day` — **рубли** (50000 = «50 000 ₽» в кабинете); `max_price` — строка в рублях; `issues: [{"code","message"}]`;
  `delivery`; `autobidding_mode` (например `max_goals`); `targetings` (`geo.regions`, `age.age_list`, `sex`, `pads`, `fulltime`…).
- Ошибка: `isError` + текст, часто JSON `{"error": true, "status": N, "detail": ...}`.

Единицы и итоги периода приводит только `scripts/normalize.py` (деньги — `money()`, CTR — как пришёл, итоги — сумма дней);
в `raw/` ответы кладутся как есть.

## Подтверждено 16.10.2026 (первый живой прогон, реальный кабинет и интерфейс ads.vk.ru)

- `budget_limit` / `budget_limit_day` — рубли: 50000 в API = «50 000 ₽» в день в интерфейсе, 500 = 500 ₽.
- `ctr` и `cr` в статистике — уже проценты; деньги в статистике — строки в рублях.
- `vk_ads_statistics_summary` игнорирует `date_from` / `date_to` и отдаёт итоги за всё время (баг MCP, передан
  платформе); `vk_ads_statistics_day` период уважает. Итоги периода — сумма дней (для объявлений — `entity=banners`).
- Глубина дневной статистики около 430 дней: `400 WRONG_DATE: minimal allowed date is '2025-8-11'`.
- Поле группы `auto_utm` → `400 unknown_resource_fields`; приходят `enable_utm` (bool) и `utm` (у удалённой группы null).
- Поля объявления `erid`, `ord_token`, `ord_erid` → 400; маркировка ОРД лежит в `ord_marker` (строка, например `2VtzquiRr5b`).
- `vk_ads_users_lists_list` → HTTP 429 с `limits: {"1": 0}`: у подключения нет прав на базы клиентов, повтор бесполезен.
- Сегменты приходят только с `id, name, pass_condition, flags, created, updated` — без `size` и без `relations` (дефект MCP):
  размер LAL-сегментов и связь база → сегмент недоступны.
- `call_to_action` у всех объявлений кабинета — `null`; роли контента вида `video_landscape_180s` (соотношения сторон в
  имени нет); удалённые и архивные группы приходят с `issues: [{"code": "ARCHIVED"}]`.
- В claude.ai и Claude Desktop у коннектора vk-ads нет персональной ссылки подключения — сбор всегда идёт путём A.
  Большие ответы (список групп ≈ 260 КБ) приходят файлом — `raw_store import`.

## Проверить живым прогоном (до релиза 0.1.0)

- Конверсии: `base.goals` (сайт) и `base.vk.result` (лид-формы, сообщество) — верно ли по целям (`CONVERSION_SOURCE` в `scripts/normalize.py`).
- Допуск проверки `ctr_units` (`check_sanity`) против округления `ctr` на стороне VK.
- Форма ответа `vk_ads_account_info` (путь B берёт id пользователя из `id`, `user_id` или `user.id`; не нашёл — предполётка
  расхода пропускается) и форма ответа `statistics_day(entity=users)` — подтвердить живым прогоном.
- Сколько `ids` принимает один вызов статистики (сейчас пачки по 50).
- Имена полей объявления: `moderation_status`, `moderation_reasons`, `user_can_request_remoderation`.
- Реальный набор значений `autobidding_mode`: пока виден только `max_goals`; значение ручного режима неизвестно
  (автостратегия определяется по списку `auto_bidding_modes` в `thresholds.json`).
- Сервер stateless (tools/call без initialize; живой probe 2026-10-14, `Mcp-Session-Id` не выдаётся) — если probe
  начнёт падать с ошибкой сессии, нужен handshake в `mcp_client`.
- Удалённые объявления в `banners_list` отдаются? (от этого зависит, насколько сумма по объявлениям меньше расхода группы).
- Смысл `budget_limit_day` для «без лимита»: 0 или null (правила `low_budget` и `scale_leader` считают оба значения «лимита нет»).

### Признак → ключи-кандидаты (имена по документации, подтверждает живой прогон)

Пока ключ не встретился в ответе, признак — `None`, проверка — «unknown» («не проверено»), а не ❌.
Списки ключей — константы `scripts/features.py`; первый найденный ключ с непустым значением принимается.

| Признак | Константа | Ключи-кандидаты | Где |
|---|---|---|---|
| интересы | `INTEREST_KEYS` | `interests`, `interests_soc_dem`, `interests_stable` | `targetings` группы |
| сообщества | `COMMUNITY_KEYS` | `groups`, `vk_groups`, `ok_groups`, `communities` | `targetings` группы |
| ключевые фразы | `KEYWORD_KEYS` | `keywords`, `search_phrases`, `vk_search_phrases`, `context_phrases` | `targetings` группы |
| минус-фразы | `MINUS_KEYS` | `minus_phrases`, `negative_phrases`, `keywords_minus` | `targetings` группы |
| период поиска фраз | `KW_PERIOD_KEYS` | `search_period`, `keywords_period`, `phrases_period` | `targetings` группы |
| расширение фраз | `KW_EXPAND_KEYS` | `expand_keywords`, `keywords_expansion`, `phrases_expansion` | `targetings` группы |
| исключённые аудитории | `EXCLUDE_KEYS` | `segments_exclude`, `exclude_segments`, `excluded_segments` | `targetings` группы |
| авто-площадки | `AUTO_PADS_KEYS` | `auto_placements`, `pads_auto` | `targetings` группы |
| корректировки по устройствам | `DEVICE_ADJ_KEYS` | `device_bid_adjustments`, `mobile_bid_adjustments` | `targetings` группы |
| корректировки по полу и возрасту | `DEMO_ADJ_KEYS` | `demography_bid_adjustments`, `sex_age_bid_adjustments` | `targetings` группы |
| маркировка ОРД | `ERID_KEYS` | `ord_marker` | объявление (подтверждено) |
| авто-UTM | `AUTO_UTM_KEYS` | `enable_utm` | группа (подтверждено) |
| бюджет кампании (CBO) | `PLAN_BUDGET_KEYS` | `budget_limit`, `budget_limit_day` | кампания (`ad_plan`) |
| размер сегмента, базы | `SIZE_KEYS` | `size`, `count`, `entries_count`, `audience_size` | сегмент, пользовательская база |
| дата обновления базы | `UPDATED_KEYS` | `updated`, `last_upload`, `uploaded` | пользовательская база |
| связи сегмента (с базами) | `SEGMENT_RELATION_KEYS` | `relations` | сегмент |
| тип и id объекта связи | `RELATION_TYPE_KEYS`, `RELATION_ID_KEYS` | `object_type`, `object_id` | элемент `relations`; база опознаётся по типу, содержащему `users_list` или `user_list` (`USERS_LIST_TYPE_MARKERS`) |

Что ещё закрыть живым прогоном для методики v1:

- `call_to_action` — ключ кнопки объявления (E7): в кабинете первого прогона везде `null` (E7 — «unknown»); какое значение
  у «Подробнее» (`learn_more`, `more`, `read_more` или русский текст — словарь `generic_cta` в `thresholds.json`), не видно.
- `country_region_ids` (`thresholds.json`, сейчас `[188]` — Россия?) — id региона «вся страна» в `targetings.geo.regions`.
- Значения `moderating_statuses` (сейчас `new`, `delayed`, `moderating`) — какие статусы модерации реально отдаёт
  сервер, пока объявление не проверено.
- Роли в `content` для соотношений сторон (E1): формат ключа роли (`image_600x600`, `video_portrait_9_16_30s`, …) и
  какие соотношения он кодирует; роль, из которой соотношение не читается (`video_landscape_180s`), даёт «unknown»
  у этого объявления (`ratios_unknown`), не ❌.
- Тип сегмента LAL: по каким полям сегмент опознаётся как lookalike (сейчас — по имени и описанию).
- Связь сегмент → база (`relations`, `object_type`, `object_id`): `remarketing_segments_list` их не отдаёт (дефект MCP),
  поэтому `stale_list` — карточка низкой надёжности, а пункт C14 — «unknown». Как называется тип «пользовательская
  база», когда поля появятся, — подтвердить.
- `max_price` при автостратегии (`max_goals`): хранит ли VK предельную цену вместе с автостратегией (правило
  `cap_too_early` для групп с автостратегией молчит). Единицы `max_price`: `max_price_rub` в `normalize.py` берёт его
  через `money()` (рубли) — как и бюджеты; подтвердить, что `max_price` тоже в рублях.
- Флаги, включённые по умолчанию (`enable_utm`, `auto_placements`, `expand_keywords`): отсутствие ключа у группы ≠
  выключено, если VK опускает значения по умолчанию.
- Регулярное выражение «названия по умолчанию» (B7) и названия со временем вида «12:30» — не считаются ли они
  говорящими.
- Полный возрастной диапазон по умолчанию у VK (для C2): какой `age_list` отдаёт группа без возрастного таргетинга.
