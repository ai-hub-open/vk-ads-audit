# Правила диагностики (черновик 0.1 — заменяется методичкой)

Каждое правило — функция `(ds, th, ctx)` в `scripts/diagnose.py`, зарегистрированная `@rule`; числа берутся из
`references/thresholds.json`. «Группа» здесь — `campaign` VK (в интерфейсе «Группа»); «живая» — не `deleted`,
«активная» — статус из `active_statuses` (`active`). Удалённые группы правила игнорируют.

Опорный CPA: целевой клиента → иначе медиана CPA живых групп с ≥ `min_conversions_for_cpa` конверсиями → иначе нет
(`ref_source`: `target` / `median` / `none`; попадает в находки и шапку отчёта).
Надёжность находки по кликам: ≥ `confidence_high_clicks` — надёжно, ≥ `confidence_medium_clicks` — средне,
меньше — мало данных. Автостратегия определяется по `autobidding_mode` из списка `auto_bidding_modes`
(сейчас `max_goals`), а не по «непустому значению».

Покрытие данных. Правила по объявлениям (`no_active_banners`, `banners_per_group`, уровень объявлений в `low_ctr`)
смотрят только группы из `coverage.banner_groups` — те, для которых был вызов `vk_ads_banners_list`; на пути A
объявления есть лишь у части групп, у остальных вывод «нет объявлений» был бы выдумкой. Правила по статистике
работают с `_measured` — живые группы без пометки `stats_missing` (статистика по группе не получена ни сводкой,
ни по дням); такие группы отчёт перечисляет в «Ограничениях».

## Гигиена

| Правило | Уровень | Когда | Ключи порогов |
|---|---|---|---|
| `no_active_banners` | 🟠 | объявления собраны для группы (`banner_groups`); активная группа без активных объявлений или с issue `NO_BANNERS_WITH_ACTIVE_STATUS` | `active_statuses` |
| `rejected_banners` | 🟠 | не удалённое объявление с `moderation_status` из списка | `rejected_moderation_statuses` (`banned`, `rejected`) |
| `blocking_issues` | 🟠 | активная группа с issue из списка (`NO_MONEY`, `AD_PLAN_STOPPED`) | `blocking_issue_codes`, `active_statuses` |
| `autodelete_risk` | ⚪ | статистика собрана; нет показов за период, группе ≥ N дней на конец периода (VK удаляет через 180 дней без показов) | `autodelete_warn_days` (150) |
| `banners_per_group` | ⚪ | объявления собраны для группы (`banner_groups`); у активной группы 1 активное объявление или больше максимума | `max_active_banners_per_group` (8) |
| `low_budget` | 🟠 | активная группа с дневным бюджетом: меньше минимума; либо автостратегия и бюджет < опорный CPA × множитель. Бюджет 0 или не задан — «лимита нет», группа пропускается | `min_daily_budget_rub` (300), `learning_budget_cpa_multiplier` (2), `auto_bidding_modes` |
| `duplicate_targeting` | 🟠 | ≥ 2 активные группы с одной целью и одинаковым ключом таргетинга | `active_statuses` |
| `unused_segments` | ⚪ | сегмент не встречается ни в одной живой группе | — |
| `no_pixel` | 🟠 | пиксели запрашивались и их нет; у активной группы цель из списка | `pixel_objectives` (`site_conversions`) |

## Эффективность

| Правило | Уровень | Когда | Эффект ₽ | Ключи порогов |
|---|---|---|---|---|
| `spend_no_conversions` | 🔴 | 0 конверсий, расход ≥ опорный CPA × множитель (опорного нет — ≥ минимального расхода) | весь расход | `no_conv_spend_cpa_multiplier` (2), `no_conv_min_spend_rub` (3000), `confidence_*_clicks` |
| `cpa_above_target` | 🔴 | есть опорный CPA; конверсий ≥ минимума, CPA > опорный × коэффициент | расход − конверсии × опорный CPA | `min_conversions_for_cpa` (5), `cpa_over_ratio` (1.5) |
| `low_ctr` | 🟠 | показов ≥ минимума, CTR (в процентах) < порога; по объявлениям групп из `banner_groups`; группы без данных объявлений — по группе (`_measured`) | расход | `low_ctr_min_shows` (5000), `low_ctr_pct` (0.3) |
| `burnout` | 🟠 | ≥ 4 дневных строк, частота и показы ≥ минимума, CTR второй половины периода (по датам) ниже первой на ≥ доли | расход второй половины | `burnout_min_frequency` (3), `burnout_min_shows` (10000), `burnout_ctr_drop` (0.3) |
| `learning_starved` | 🟠 | активная группа на автостратегии с расходом: конверсий в неделю (по дням с показами) < минимума | — | `learning_min_conversions_per_week` (10), `auto_bidding_modes` |
| `spend_concentration` | ⚪ | ≥ N групп с расходом, у одной доля ≥ порога | — | `concentration_min_groups` (3), `concentration_share` (0.7) |
| `scale_leader` | 🟢 | активная группа с дневным бюджетом, есть опорный CPA; конверсий ≥ минимума, CPA ≤ опорный × коэффициент, средний расход в дни с расходом ≥ бюджет × доля | средний расход × доля роста × дни периода | `min_conversions_for_cpa`, `leader_cpa_ratio` (0.7), `leader_budget_use` (0.9), `leader_growth_share` (0.5) |

## Метрика

Только активные группы, по которым есть данные Метрики (склейка по `utm_campaign` = id группы).

| Правило | Уровень | Когда | Ключи порогов |
|---|---|---|---|
| `high_bounce` | 🟠 | визитов ≥ минимума, доля отказов ≥ порога | `metrika_min_visits` (100), `high_bounce_rate` (0.6) |
| `click_loss` | 🟠 | кликов ≥ минимума, визиты / клики < порога | `click_loss_min_clicks` (200), `click_loss_visit_ratio` (0.6) |
| `goals_without_vk_conversions` | 🟠 | в Метрике достижения целей есть, в VK конверсий 0 | — |

Находки сортируются: 🔴 → 🟠 → 🟢 → ⚪, внутри уровня по убыванию эффекта в ₽.
