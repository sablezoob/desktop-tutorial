# -*- coding: utf-8 -*-
"""Фоновая работа с нейросетью.

Модель отвечает десятками секунд, поэтому весь обмен идёт здесь, в отдельном
потоке: интерфейс не ждёт ответа и не подтормаживает. Воркер сам решает,
чем заняться, в таком порядке важности:

1. слова, отмеченные «не знаю» прямо в примере — у них ещё нет перевода;
2. примеры для слов, которые скоро покажутся;
3. пополнение словаря, когда новых слов осталось меньше порога.
"""
import logging
import threading
import time
import ai
import db

log = logging.getLogger("vocab.aiworker")

IDLE_SLEEP = 25         # пауза, когда делать нечего
ERROR_SLEEP = 120       # пауза после сбоя, чтобы не долбить неотвечающий сервис
AI_TAG = "ai"           # тег слов, добавленных нейросетью
BATCH = 5               # слов в одном запросе: параллельные запросы упираются
                        # в 429, а один ответ на пятёрку идёт не дольше, чем
                        # на одно слово — так вчетверо меньше обращений
RATE_SLEEP = 90         # первая пауза, когда сервис просит сбавить темп
RATE_SLEEP_MAX = 1800   # дальше пауза удваивается: квота бывает часовой


# ------------------------------------------------------------------ очередь
def queue_unknown(word, context=""):
    """Слово, отмеченное «не знаю». Кладётся без перевода — воркер дозаполнит.

    Пока перевода нет, слово не показывается: выборка берёт только карточки
    с непустым переводом. Так недоделанная карточка не попадёт на экран.
    """
    word = (word or "").strip().strip(".,!?;:\"'()").lower()
    if not word or len(word) < 2:
        return None, "empty"
    c = db.conn()
    row = c.execute("SELECT id, translation FROM words WHERE word=? COLLATE NOCASE",
                    (word,)).fetchone()
    if row:
        return row["id"], ("exists" if row["translation"] else "queued")
    wid, res = db.add_word(word, tags=f"{AI_TAG}, unknown", note=context[:300])
    log.info("В очередь на разбор: %s", word)
    return wid, res


def pending_unknown(limit=5):
    return db.conn().execute(
        """SELECT id, word, note FROM words
           WHERE translation = '' ORDER BY created_at LIMIT ?""", (limit,)).fetchall()


def words_needing_sentences(limit=4):
    """Слова, у которых мало примеров. Сначала те, что скоро покажутся."""
    want = max(1, db.get_int("ai_sentences_per_word", 4))
    return db.conn().execute(
        """SELECT w.id, w.word, w.translation, w.v2, w.v3,
                  (SELECT COUNT(*) FROM sentences s WHERE s.word_id = w.id) have
           FROM words w JOIN srs r ON r.word_id = w.id
           WHERE w.translation != '' AND r.status != 'suspended'
             AND (SELECT COUNT(*) FROM sentences s WHERE s.word_id = w.id) < ?
           ORDER BY CASE r.status WHEN 'learning' THEN 0 WHEN 'new' THEN 1 ELSE 2 END,
                    r.due_at
           LIMIT ?""", (want, limit)).fetchall()


def new_words_left():
    return db.conn().execute(
        """SELECT COUNT(*) FROM words w JOIN srs s ON s.word_id = w.id
           WHERE s.status = 'new' AND w.translation != ''""").fetchone()[0]


# ------------------------------------------------------------------- задачи
def do_unknown(row):
    card = ai.explain(row["word"], row["note"] or "")
    c = db.conn()
    c.execute("""UPDATE words SET word=?, translation=?, ipa=?, v2=?, v3=?, ipa2=?, ipa3=?,
                 example_en=?, example_ru=?, enriched=1 WHERE id=?""",
              (card["word"], card["translation"], card["ipa"], card["v2"], card["v3"],
               card["ipa2"], card["ipa3"], card["example_en"], card["example_ru"], row["id"]))
    c.commit()
    if card["example_en"]:
        db.add_sentence(row["id"], card["example_en"], card["example_ru"], source="ai")
    log.info("Разобрано незнакомое слово: %s — %s", card["word"], card["translation"])
    return 1


def do_sentences(rows):
    """Примеры сразу для нескольких слов — одним запросом на всю пачку."""
    if not isinstance(rows, (list, tuple)):
        rows = [rows]
    items = [{"word": r["word"], "translation": r["translation"],
              "v2": r["v2"], "v3": r["v3"]} for r in rows]
    by_word = {r["word"]: r for r in rows}
    result = ai.make_sentences_batch(items, count=3)
    added = 0
    for word, pairs in result.items():
        row = by_word.get(word)
        if not row:
            continue
        n = sum(1 for en, ru in pairs if db.add_sentence(row["id"], en, ru, source="ai"))
        added += n
        log.info("Примеры для «%s»: +%d", word, n)
    return added


def do_new_words():
    batch = max(5, db.get_int("ai_words_per_batch", 20))
    known = [r["word"] for r in db.conn().execute(
        "SELECT word FROM words ORDER BY RANDOM() LIMIT 200")]
    cards = ai.make_words(count=batch, known=known)
    added = 0
    for card in cards:
        wid, res = db.add_word(card["word"], ipa=card["ipa"], translation=card["translation"],
                               example_en=card["example_en"], example_ru=card["example_ru"],
                               tags=AI_TAG, level=db.get("ai_level", ""))
        if res != "created":
            continue
        added += 1
        if card["v2"]:
            db.set_forms(wid, card["v2"], card["v3"], card["ipa2"], card["ipa3"])
        if card["example_en"]:
            db.add_sentence(wid, card["example_en"], card["example_ru"], source="ai")
    log.info("Словарь пополнен: +%d слов", added)
    return added


def step():
    """Одно дело за раз. Возвращает True, если работа была."""
    rows = pending_unknown(1)
    if rows:
        do_unknown(rows[0])
        return True

    if new_words_left() < max(0, db.get_int("ai_min_new_words", 10)):
        do_new_words()
        return True

    rows = words_needing_sentences(BATCH)
    if rows:
        do_sentences(rows)
        return True
    return False


def run():
    log.info("Фоновый помощник запущен")
    rate_wait = RATE_SLEEP
    while True:
        try:
            if not ai.is_enabled():
                time.sleep(IDLE_SLEEP * 2)
                continue
            if not step():
                time.sleep(IDLE_SLEEP)
            rate_wait = RATE_SLEEP          # получилось — сбрасываем ожидание
        except ai.RateLimited as e:
            # Квота у бесплатного тарифа бывает часовой: долбить её каждые
            # полторы минуты бессмысленно, поэтому пауза удваивается.
            log.warning("%s — жду %d с", e, rate_wait)
            time.sleep(rate_wait)
            rate_wait = min(rate_wait * 2, RATE_SLEEP_MAX)
        except ai.AiError as e:
            log.warning("Нейросеть недоступна: %s", e)
            time.sleep(ERROR_SLEEP)
        except Exception:
            log.exception("Сбой фонового помощника")
            time.sleep(ERROR_SLEEP)


def start():
    t = threading.Thread(target=run, daemon=True, name="aiworker")
    t.start()
    return t
