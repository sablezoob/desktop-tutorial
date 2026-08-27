# -*- coding: utf-8 -*-
"""Общее окружение тестов: каждый тест получает свежую базу во временной папке.

Рабочая база не трогается — иначе тесты портили бы реальный прогресс.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Пустая база в отдельной папке, подменяет путь на время теста."""
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    db._local.__dict__.clear()
    db.init()
    yield db
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()
    db._local.__dict__.clear()


@pytest.fixture()
def words(fresh_db):
    """Небольшой словарь: обычные слова и один неправильный глагол."""
    ids = {}
    # Глаголов минимум четыре: для квиза по формам нужны три чужих варианта
    # плюс правильный, иначе вопрос честно не собрать.
    data = [
        ("draw", "рисовать", "/drɔː/", "drew", "drawn"),
        ("hide", "прятать", "/haɪd/", "hid", "hidden"),
        ("write", "писать", "/raɪt/", "wrote", "written"),
        ("speak", "говорить", "/spiːk/", "spoke", "spoken"),
        ("give", "давать", "/ɡɪv/", "gave", "given"),
        ("advice", "совет", "/ədˈvaɪs/", "", ""),
        ("winter", "зима", "/ˈwɪntə/", "", ""),
        ("season", "время года", "/ˈsiːzn/", "", ""),
        ("weather", "погода", "/ˈweðə/", "", ""),
    ]
    for word, tr, ipa, v2, v3 in data:
        wid, _ = fresh_db.add_word(word, ipa=ipa, translation=tr, tags="test",
                                   example_en=f"I have seen the {word}.",
                                   example_ru=f"Я видел {tr}.")
        ids[word] = wid
        if v2:
            fresh_db.set_forms(wid, v2, v3, "/x/", "/y/")
        fresh_db.add_sentence(wid, f"This is a sentence with {word}.", f"Пример со словом {tr}.", "seed")
    return ids


@pytest.fixture()
def client(fresh_db, words):
    """Тестовый клиент дашборда поверх временной базы."""
    import webapp
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client()
