# -*- coding: utf-8 -*-
"""Подбор вариантов для режимов проверки.

Неправильные варианты берутся из той же колоды: выбирать из случайных слов
всего словаря слишком легко — ответ угадывается по теме, а не по знанию.
"""
import random

import db

OPTIONS = 4


def _pool(word_id, tags, column, limit=40):
    """Кандидаты в неправильные варианты: сначала соседи по колоде, потом любые."""
    c = db.conn()
    tag = (tags or "").split(",")[0].strip()
    rows = []
    if tag:
        rows = c.execute(
            f"""SELECT DISTINCT {column} v FROM words
                WHERE id != ? AND {column} != '' AND tags LIKE ?
                ORDER BY RANDOM() LIMIT ?""",
            (word_id, f"%{tag}%", limit)).fetchall()
    if len(rows) < OPTIONS:
        rows += c.execute(
            f"""SELECT DISTINCT {column} v FROM words
                WHERE id != ? AND {column} != '' ORDER BY RANDOM() LIMIT ?""",
            (word_id, limit)).fetchall()
    return [r["v"] for r in rows]


def _build(correct, pool):
    correct = (correct or "").strip()
    if not correct:
        return []
    seen = {correct.lower()}
    wrong = []
    for v in pool:
        v = (v or "").strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            wrong.append(v)
        if len(wrong) == OPTIONS - 1:
            break
    if len(wrong) < OPTIONS - 1:
        return []                      # слишком мало слов в базе — квиз не собрать
    options = wrong + [correct]
    random.shuffle(options)
    return options


def translation_options(row):
    """Варианты перевода: что значит это слово."""
    return _build(row["translation"], _pool(row["id"], row["tags"], "translation"))


def form_options(row):
    """Варианты третьей формы глагола."""
    if not row["v3"]:
        return []
    return _build(row["v3"], _pool(row["id"], row["tags"], "v3"))


def check_typed(answer, expected):
    """Сверка введённого слова: регистр и лишние пробелы не считаются ошибкой."""
    norm = lambda s: " ".join((s or "").lower().replace("ё", "е").split())
    return norm(answer) == norm(expected)
