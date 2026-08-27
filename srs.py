# -*- coding: utf-8 -*-
"""SM-2 в минутном масштабе + выбор следующего слова для показа."""
import random
from datetime import datetime, timezone, timedelta

import db

DAY = 1440

# Лестница интервалов после «Знаю»: 2 часа -> 8 часов -> сутки -> дальше × ease.
# Шаги подобраны под фоновый режим. В Anki первый шаг — 10 минут, но там человек
# сам садится за карточки раз в день; здесь показ идёт каждые несколько минут,
# и десятиминутный шаг возвращал знакомое слово уже через три карточки.
FIRST_STEPS = [120, 8 * 60, DAY]
LEARNED_AFTER_MIN = 7 * DAY   # интервал, с которого слово считается выученным
AGAIN_DELAY = 10              # «ещё раз» — слово нужно увидеть скоро
SKIP_DELAY = 90               # непросмотренная карточка: не ответ, а «не увидел»


def _now():
    return datetime.now(timezone.utc)


def grade(word_id, action):
    """Пересчитать расписание. action: know | again | skip."""
    c = db.conn()
    row = c.execute("SELECT * FROM srs WHERE word_id=?", (word_id,)).fetchone()
    if row is None:
        c.execute("INSERT INTO srs(word_id, due_at, status) VALUES (?,?, 'new')",
                  (word_id, db.now_iso()))
        c.commit()
        row = c.execute("SELECT * FROM srs WHERE word_id=?", (word_id,)).fetchone()

    ease = row["ease"] or 2.5
    interval = row["interval_min"] or 0
    reps = row["reps"] or 0
    lapses = row["lapses"] or 0

    if action == "know":
        reps += 1
        if interval < FIRST_STEPS[-1]:
            nxt = next((s for s in FIRST_STEPS if s > interval), FIRST_STEPS[-1])
            interval = nxt
        else:
            interval = interval * ease
        ease = min(3.2, ease + 0.08)
    elif action == "again":
        lapses += 1
        ease = max(1.3, ease - 0.2)
        interval = AGAIN_DELAY
    else:
        # skip — карточка погасла сама или её отложили. Это «не посмотрел»,
        # а не «не знаю»: наказывать за это нельзя, иначе слово возвращается
        # через 10 минут и весь день крутится по кругу.
        interval = max(SKIP_DELAY, interval)
        reps = reps                      # прогресс не сбрасываем

    interval = min(interval, 180 * DAY)
    # ±10% разброса, чтобы слова не слипались в одну пачку
    jitter = interval * random.uniform(-0.1, 0.1)
    due = _now() + timedelta(minutes=interval + jitter)

    if interval >= LEARNED_AFTER_MIN:
        status = "learned"
    elif action == "skip" and reps == 0:
        status = "new"          # ни разу не отвечали — слово всё ещё новое
    elif action == "skip":
        status = row["status"] or "learning"
    else:
        status = "learning"

    c.execute("""UPDATE srs SET ease=?, interval_min=?, due_at=?, reps=?, lapses=?, status=?
                 WHERE word_id=?""",
              (ease, interval, due.isoformat(timespec="seconds"), reps, lapses, status, word_id))
    c.commit()
    return status, interval


def set_status(word_id, status):
    """Ручное 'знаю совсем' / 'отложить' из дашборда."""
    c = db.conn()
    if status == "learned":
        due = _now() + timedelta(minutes=30 * DAY)
        c.execute("""UPDATE srs SET status='learned', interval_min=?, due_at=?
                     WHERE word_id=?""", (30 * DAY, due.isoformat(timespec="seconds"), word_id))
    elif status == "suspended":
        c.execute("UPDATE srs SET status='suspended' WHERE word_id=?", (word_id,))
    else:
        c.execute("""UPDATE srs SET status='new', interval_min=0, ease=2.5, reps=0,
                     due_at=? WHERE word_id=?""", (db.now_iso(), word_id))
    c.commit()


def _pick(sql, params=()):
    rows = db.conn().execute(sql, params).fetchall()
    return random.choice(rows) if rows else None


# Слово не повторяется, пока не пройдут другие. Иначе выборка кучкуется:
# одно и то же слово выпадало по нескольку раз подряд, а часть колоды молчала.
RECENT_BLOCK = 25


def _recent_ids(pool_size):
    """Недавно показанные слова. Блокируем не больше трети колоды,
    иначе на маленькой колоде блокировать станет нечего."""
    limit = max(0, min(RECENT_BLOCK, pool_size // 3))
    if limit == 0:
        return []
    rows = db.conn().execute(
        "SELECT word_id FROM events ORDER BY id DESC LIMIT ?", (limit * 2,)).fetchall()
    seen, out = set(), []
    for r in rows:
        if r["word_id"] not in seen:
            seen.add(r["word_id"])
            out.append(r["word_id"])
        if len(out) >= limit:
            break
    return out


def _count(clause, params, focus_clause=""):
    """Сколько слов подходит под условие — нужно, чтобы взвесить ветки выбора."""
    sql = ("""SELECT COUNT(*) FROM words w JOIN srs s ON s.word_id = w.id
              WHERE w.translation != '' AND s.status != 'suspended' """
           + focus_clause + clause)
    return db.conn().execute(sql, params).fetchone()[0]


def new_started_today():
    """Сколько новых слов уже начато сегодня — по первому показу каждого слова."""
    today = datetime.now().strftime("%Y-%m-%d")
    return db.conn().execute(
        """SELECT COUNT(*) FROM (SELECT word_id, MIN(shown_at) m FROM events GROUP BY word_id)
           WHERE substr(datetime(m, 'localtime'), 1, 10) = ?""", (today,)).fetchone()[0]


def new_quota_left():
    """Остаток дневной порции. 0 или меньше — новые слова на сегодня закончились."""
    limit = db.get_int("new_per_day", 20)
    if limit <= 0:
        return 10 ** 6            # 0 в настройке = без ограничения
    return limit - new_started_today()


def _pool_size(focus):
    sql = """SELECT COUNT(*) FROM words w JOIN srs s ON s.word_id = w.id
             WHERE w.translation != '' AND s.status != 'suspended'"""
    params = ()
    if focus:
        sql += " AND w.tags LIKE ?"
        params = (f"%{focus}%",)
    return db.conn().execute(sql, params).fetchone()[0]


def _build(clause, exclude):
    """Запрос с исключением недавно показанных.

    Порядок плейсхолдеров важен: сначала список исключений, затем фокус-тег,
    затем условие по сроку — параметры собираются в вызывающем коде так же.
    """
    base = """SELECT w.*, s.status, s.interval_min, s.reps
              FROM words w JOIN srs s ON s.word_id = w.id
              WHERE w.translation != '' AND s.status != 'suspended' """
    if exclude:
        base += " AND w.id NOT IN (%s) " % ",".join("?" * len(exclude))
    return base + clause


def next_word():
    """Следующее слово: просроченные повторы, новые и контроль выученного.

    Новые слова берутся в случайном порядке, а не по дате добавления: их срок
    совпадает с моментом создания, и сортировка по сроку всегда возвращала бы
    одну и ту же голову списка — остальная колода не показывалась бы никогда.
    """
    focus = (db.get("focus_tag") or "").strip()
    now = db.now_iso()
    use_focus = bool(focus) and random.random() < 0.8
    focus_clause = " AND w.tags LIKE ? " if use_focus else ""
    focus_param = (f"%{focus}%",) if use_focus else ()

    recent = _recent_ids(_pool_size(focus if use_focus else ""))
    quota_left = new_quota_left()

    # Доля повторов зависит от того, сколько их реально созрело. Фиксированные
    # 55% при семи просроченных словах означали бы, что больше половины показов
    # крутится вокруг этой семёрки, пока две сотни новых ждут своей очереди.
    due_learning = _count(" AND s.due_at <= ? AND s.status='learning'",
                          focus_param + (now,), focus_clause)
    due_learned = _count(" AND s.due_at <= ? AND s.status='learned'",
                         focus_param + (now,), focus_clause)
    p_learning = min(0.40, due_learning / 60.0)
    p_learned = min(0.10, due_learned / 60.0)

    for exclude in (recent, []):
        r = random.random()
        variants = []                       # (условие, доп. параметры)
        if r < p_learning:
            variants.append((focus_clause + " AND s.due_at <= ? AND s.status='learning' "
                             "ORDER BY s.due_at LIMIT 30", (now,)))
        elif r < p_learning + p_learned:
            variants.append((focus_clause + " AND s.due_at <= ? AND s.status='learned' "
                             "ORDER BY RANDOM() LIMIT 30", (now,)))
        # Основная масса показов — новые слова, в случайном порядке.
        # Но не больше дневной порции: иначе за день пролетает весь словарь,
        # и ничего не успевает закрепиться.
        if quota_left > 0:
            variants.append((focus_clause + " AND s.status='new' ORDER BY RANDOM() LIMIT 30", ()))
        variants.append((focus_clause + " AND s.due_at <= ? ORDER BY RANDOM() LIMIT 30", (now,)))
        variants.append((focus_clause + " AND s.status='new' ORDER BY RANDOM() LIMIT 30", ()))
        variants.append((focus_clause + " ORDER BY RANDOM() LIMIT 30", ()))

        for clause, extra in variants:
            row = _pick(_build(clause, exclude),
                        tuple(exclude) + focus_param + extra)
            if row:
                return row

    # фокус-колода пуста — берём любое слово из словаря
    return _pick("""SELECT w.*, s.status, s.interval_min, s.reps
                    FROM words w JOIN srs s ON s.word_id = w.id
                    WHERE w.translation != '' AND s.status != 'suspended'
                    ORDER BY RANDOM() LIMIT 30""")


# Время в базе хранится в UTC, а «сегодня» человек понимает по своим часам.
# Поэтому день события всегда берём через localtime, иначе с полуночи до утра
# статистика показывала бы вчерашний день.
LOCAL_DAY = "substr(datetime(shown_at, 'localtime'), 1, 10)"


def session_words(limit=20, tag="", only_verbs=False):
    """Набор карточек для тренировки: сперва просроченные, затем новые.

    В отличие от next_word здесь не нужна случайность — сессия конечная,
    и важно за неё пройти самое нужное.
    """
    sql = """SELECT w.*, s.status, s.due_at FROM words w JOIN srs s ON s.word_id = w.id
             WHERE w.translation != '' AND s.status != 'suspended' """
    params = []
    if tag:
        sql += " AND w.tags LIKE ? "
        params.append(f"%{tag}%")
    if only_verbs:
        sql += " AND w.v2 != '' "
    sql += """ ORDER BY CASE WHEN s.due_at <= ? THEN 0 ELSE 1 END,
                        CASE s.status WHEN 'learning' THEN 0 WHEN 'new' THEN 1 ELSE 2 END,
                        s.due_at
               LIMIT ?"""
    params += [db.now_iso(), int(limit)]
    return db.conn().execute(sql, params).fetchall()


def stats():
    c = db.conn()
    q = lambda s, p=(): c.execute(s, p).fetchone()[0]
    now = db.now_iso()
    today = datetime.now().strftime("%Y-%m-%d")
    return {
        "total":     q("SELECT COUNT(*) FROM words"),
        "new":       q("SELECT COUNT(*) FROM srs WHERE status='new'"),
        "learning":  q("SELECT COUNT(*) FROM srs WHERE status='learning'"),
        "learned":   q("SELECT COUNT(*) FROM srs WHERE status='learned'"),
        "suspended": q("SELECT COUNT(*) FROM srs WHERE status='suspended'"),
        "due_now":   q("SELECT COUNT(*) FROM srs WHERE due_at<=? AND status IN ('new','learning')", (now,)),
        "shown_today": q(f"SELECT COUNT(*) FROM events WHERE {LOCAL_DAY}=?", (today,)),
        "know_today":  q(f"SELECT COUNT(*) FROM events WHERE action='know' AND {LOCAL_DAY}=?", (today,)),
        "shown_total": q("SELECT COUNT(*) FROM events"),
        "no_translation": q("SELECT COUNT(*) FROM words WHERE translation=''"),
    }
