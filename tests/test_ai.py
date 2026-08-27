# -*- coding: utf-8 -*-
"""Разбор ответов нейросети и очередь незнакомых слов.

Сеть не дёргаем: подменяем ответ модели и проверяем, что мы устойчивы к тому,
как она реально отвечает — с ограждением ```json, с лишними словами в ответе
и с предложениями, где нужного слова нет.
"""
import json

import pytest

import ai
import aiworker


def fake_answer(monkeypatch, payload):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(ai, "_ask", lambda *a, **k: text)


def test_json_survives_markdown_fences(monkeypatch):
    fake_answer(monkeypatch, '```json\n{"sentences":[{"en":"I have read it.","ru":"Я прочитал."}]}\n```')
    pairs = ai.make_sentences("read", "читать", forms=("read", "read"))
    assert pairs == [("I have read it.", "Я прочитал.")]


def test_json_survives_chatter_around(monkeypatch):
    fake_answer(monkeypatch, 'Sure! Here you go:\n{"sentences":[{"en":"I have read it.","ru":"Я прочитал."}]}\nHope it helps.')
    assert ai.make_sentences("read", "читать")


def test_sentences_without_the_word_are_dropped(monkeypatch):
    fake_answer(monkeypatch, {"sentences": [
        {"en": "The sun is shining.", "ru": "Светит солнце."},
        {"en": "She has hidden the key.", "ru": "Она спрятала ключ."},
    ]})
    pairs = ai.make_sentences("hide", "прятать", forms=("hid", "hidden"))
    assert len(pairs) == 1 and "hidden" in pairs[0][0]


def test_irregular_verb_form_counts_as_the_word(monkeypatch):
    """«hide» встречается как «hidden» — сверка идёт по началу каждой формы."""
    fake_answer(monkeypatch, {"sentences": [{"en": "He has hidden it.", "ru": "Он спрятал это."}]})
    assert ai.make_sentences("hide", "прятать", forms=("hid", "hidden"))


def test_no_usable_sentences_raises(monkeypatch):
    fake_answer(monkeypatch, {"sentences": [{"en": "Nothing here.", "ru": "Ничего."}]})
    with pytest.raises(ai.AiError):
        ai.make_sentences("hide", "прятать", forms=("hid", "hidden"))


def test_batch_splits_by_word(monkeypatch):
    fake_answer(monkeypatch, {"items": [
        {"word": "draw", "sentences": [{"en": "He has drawn a map.", "ru": "Он нарисовал карту."}]},
        {"word": "hide", "sentences": [
            {"en": "She has hidden it.", "ru": "Она спрятала это."},
            {"en": "The sky is blue.", "ru": "Небо синее."},
        ]},
        {"word": "stranger", "sentences": [{"en": "Nobody asked.", "ru": "Никто не просил."}]},
    ]})
    out = ai.make_sentences_batch([
        {"word": "draw", "translation": "рисовать", "v2": "drew", "v3": "drawn"},
        {"word": "hide", "translation": "прятать", "v2": "hid", "v3": "hidden"},
    ])
    assert set(out) == {"draw", "hide"}, "лишнее слово из ответа должно игнорироваться"
    assert len(out["hide"]) == 1, "предложение без слова отсеивается"


def test_rate_limit_is_recognized(monkeypatch):
    class Boom(Exception):
        pass
    Boom.__name__ = "RateLimitError"

    def raise_429(*a, **k):
        raise Boom("Error code: 429")

    monkeypatch.setattr(ai, "_client", lambda: object())
    monkeypatch.setattr(ai, "_call", raise_429)
    with pytest.raises(ai.RateLimited):
        ai._ask("hi")


def test_unknown_word_goes_to_queue_without_translation(fresh_db):
    wid, res = aiworker.queue_unknown("Portrait!", "He has drawn a portrait.")
    assert res == "created"
    row = fresh_db.conn().execute("SELECT word, translation, note FROM words WHERE id=?",
                                  (wid,)).fetchone()
    assert row["word"] == "portrait", "слово должно нормализоваться"
    assert row["translation"] == "", "перевода ещё нет — его подставит помощник"
    assert "drawn" in row["note"], "контекст сохраняется для точного перевода"


def test_word_without_translation_is_not_shown(fresh_db, words):
    import srs
    aiworker.queue_unknown("portrait", "context")
    shown = {srs.next_word()["word"] for _ in range(40)}
    assert "portrait" not in shown, "недоделанная карточка не должна попадать на экран"


def test_service_words_are_not_queued(fresh_db):
    assert aiworker.queue_unknown("a")[1] == "empty"
    assert aiworker.queue_unknown("  ")[1] == "empty"


def test_ai_disabled_without_key(fresh_db):
    fresh_db.put("ai_enabled", "1")
    fresh_db.put("ai_key", "")
    assert not ai.is_enabled()
    fresh_db.put("ai_key", "nvapi-test")
    assert ai.is_enabled()
