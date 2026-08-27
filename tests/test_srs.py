# -*- coding: utf-8 -*-
"""Планирование повторов и выбор следующего слова.

Здесь собраны случаи, которые ломались на практике: слово возвращалось через
три карточки после «Знаю», выборка кучковалась вокруг горстки слов, а карточка,
погасшая без ответа, откатывала прогресс.
"""
import srs


def test_know_ladder_matches_background_mode(words, fresh_db):
    """Первый шаг — часы, а не минуты: при показе раз в 3 минуты
    десятиминутный шаг возвращал знакомое слово почти сразу."""
    wid = words["advice"]
    _, first = srs.grade(wid, "know")
    assert first >= 120, "знакомое слово не должно возвращаться через минуты"


def test_two_correct_answers_mark_word_learned(words, fresh_db):
    wid = words["advice"]
    status1, _ = srs.grade(wid, "know")
    status2, interval = srs.grade(wid, "know")
    assert status1 == "learning"
    assert status2 == "learned"
    assert interval >= 30 * srs.DAY


def test_learned_word_is_not_shown_again(words, fresh_db):
    wid = words["advice"]
    srs.grade(wid, "know")
    srs.grade(wid, "know")
    shown = {srs.next_word()["id"] for _ in range(60)}
    assert wid not in shown


def test_learned_returns_when_review_enabled(words, fresh_db):
    wid = words["advice"]
    srs.grade(wid, "know")
    srs.grade(wid, "know")
    fresh_db.put("review_learned", "1")
    fresh_db.conn().execute("UPDATE srs SET due_at=? WHERE word_id=?",
                            (fresh_db.now_iso(), wid))
    fresh_db.conn().commit()
    shown = {srs.next_word()["id"] for _ in range(80)}
    assert wid in shown


def test_again_brings_word_back_soon(words, fresh_db):
    _, interval = srs.grade(words["draw"], "again")
    assert interval == srs.AGAIN_DELAY


def test_skip_is_not_a_wrong_answer(words, fresh_db):
    """Карточка погасла сама — это «не увидел», а не «не знаю»:
    прогресс не сбрасывается, слово не возвращается через десять минут."""
    wid = words["draw"]
    srs.grade(wid, "know")
    before = fresh_db.conn().execute(
        "SELECT reps, interval_min FROM srs WHERE word_id=?", (wid,)).fetchone()
    _, interval = srs.grade(wid, "skip")
    after = fresh_db.conn().execute(
        "SELECT reps FROM srs WHERE word_id=?", (wid,)).fetchone()
    assert interval >= srs.SKIP_DELAY
    assert interval >= before["interval_min"], "пропуск не должен уменьшать интервал"
    assert after["reps"] == before["reps"], "пропуск не должен сбрасывать прогресс"


def test_selection_does_not_repeat_the_same_word(words, fresh_db):
    """Показ записывается — на эту историю и опирается защита от повторов."""
    picks = []
    for _ in range(30):
        row = srs.next_word()
        picks.append(row["word"])
        fresh_db.log_event(row["id"], "skip", 14000)
    pairs = [(a, b) for a, b in zip(picks, picks[1:]) if a == b]
    assert not pairs, f"слово повторилось подряд: {pairs[:3]}"


def test_selection_covers_the_whole_deck(words, fresh_db):
    """Раньше выбор шёл из первых сорока слов по сроку, и часть колоды
    не показывалась никогда."""
    seen = set()
    for _ in range(40):
        row = srs.next_word()
        seen.add(row["word"])
        fresh_db.log_event(row["id"], "skip", 14000)
        srs.grade(row["id"], "skip")
    assert len(seen) >= len(words) - 1


def test_next_word_survives_empty_dictionary(fresh_db):
    assert srs.next_word() is None


def test_unreviewed_counts_only_shown_without_answer(words, fresh_db):
    assert srs.unreviewed_count() == 0
    fresh_db.log_event(words["winter"], "skip", 14000)
    srs.grade(words["winter"], "skip")
    assert srs.unreviewed_count() == 1
    srs.grade(words["winter"], "know")
    assert srs.unreviewed_count() == 0, "после ответа слово перестаёт быть неразобранным"


def test_never_shown_counts_untouched_words(words, fresh_db):
    assert srs.never_shown_count() == len(words)
    fresh_db.log_event(words["draw"], "skip", 1000)
    assert srs.never_shown_count() == len(words) - 1


def test_daily_goal_progress(words, fresh_db):
    fresh_db.put("daily_goal", "2")
    assert srs.goal_progress()["done"] == 0
    for w in ("draw", "hide"):
        fresh_db.log_event(words[w], "know", 3000)
    progress = srs.goal_progress()
    assert progress["done"] == 2
    assert progress["reached"] is True


def test_stats_use_local_day(words, fresh_db):
    """События хранятся в UTC, а «сегодня» человек понимает по своим часам:
    ночью статистика показывала вчерашний день."""
    fresh_db.log_event(words["draw"], "know", 5000)
    assert srs.stats()["know_today"] == 1
