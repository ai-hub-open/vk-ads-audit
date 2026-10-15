"""
questions.py — вопросы к клиенту: то, что эксперты считают важным, а API не показывает.
Вопрос получает пометку «важно для вашего кабинета», если данные дают к нему повод.
"""
from __future__ import annotations

try:
    from scripts.diagnose import ASK_FUNNELS, _active, _live, absent_ask_funnels
except ImportError:  # запуск напрямую
    from diagnose import ASK_FUNNELS, _active, _live, absent_ask_funnels


def _objectives(ds, th) -> set:
    return {g["objective"] for g in _live(ds) if _active(g, th)}


def build_questions(ds: dict, th: dict, findings: list, answers: dict | None = None) -> list:
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
    out = [{"id": i, "text": t, "important": why is not None, "why": why} for i, t, why in q]
    answered = answers or {}
    for key in absent_ask_funnels(ds, th):  # воронки нет среди активных групп — спрашиваем, пока клиент не ответил
        if key not in answered:
            f = ASK_FUNNELS[key]
            out.append({"id": f"funnel_{key}", "text": f["question"], "important": True, "why": f["why"]})
    return out
