# vk-ads-audit

Скилл Claude: аудит и быстрый анализ кабинета VK Рекламы только на чтение через MCP `vk-ads`
(опционально с Яндекс Метрикой). Глубокий аудит → HTML-отчёт с находками по приоритету; быстрый анализ —
сводка дня в 10 строк. Создание и правка кампаний — [vk-ads-manager](https://github.com/ai-hub-open/vk-ads-manager).

Установка: маркетплейс плагинов `ai-hub-open` (`/plugin`) или архив `vk-ads-audit.skill` из релизов.

Разработка: `pip install -r requirements-dev.txt && python -m pytest`, эталоны — `python -m evals.run_evals`.
