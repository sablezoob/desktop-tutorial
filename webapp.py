# -*- coding: utf-8 -*-
"""Локальный дашборд на 127.0.0.1:8777 — импорт слов, редактирование, статистика."""
import logging
import re
import sqlite3
from datetime import datetime, timedelta

from flask import Flask, jsonify, render_template, request

import db
import quiz
import srs

app = Flask(__name__)

# Разделители намеренно узкие: тире не годится — оно живёт внутри примеров.
SPLIT_RE = re.compile(r"\s*(?:\||\t|;;)\s*")


def parse_line(line):
    """Разбирает строку импорта.

    Поддерживает: `word`, `word | перевод`, `word | перевод | /ipa/`,
    `word | перевод | /ipa/ | example en | пример ру`.
    Разделитель — вертикальная черта, таб или `;;`.
    """
    parts = [p.strip() for p in SPLIT_RE.split(line.strip()) if p.strip()]
    if not parts:
        return None
    out = {"word": parts[0], "translation": "", "ipa": "",
           "example_en": "", "example_ru": ""}
    rest = parts[1:]
    # IPA может стоять в любой позиции — узнаём по слешам или квадратным скобкам
    for p in list(rest):
        if (p.startswith("/") and p.endswith("/")) or (p.startswith("[") and p.endswith("]")):
            out["ipa"] = p
            rest.remove(p)
            break
    if rest:
        out["translation"] = rest[0]
    if len(rest) > 1:
        out["example_en"] = rest[1]
    if len(rest) > 2:
        out["example_ru"] = rest[2]
    return out


@app.route("/")
def index():
    return render_template("index.html")


@app.get("/favicon.ico")
def favicon():
    # заглушка, чтобы браузер не сыпал 404 в консоль
    return ("", 204)


@app.route("/train")
def train():
    return render_template("train.html")


@app.get("/api/session")
def api_session():
    """Карточки для тренировки: данные + заранее собранные варианты для квиза."""
    tag = (request.args.get("deck") or "").strip()
    mode = request.args.get("mode") or "selftest"
    limit = min(100, max(3, int(request.args.get("limit") or 20)))

    rows = srs.session_words(limit=limit, tag=tag, only_verbs=(mode == "forms"))
    out = []
    for r in rows:
        d = dict(r)
        if mode == "quiz":
            d["options"] = quiz.translation_options(r)
        elif mode == "forms":
            d["options"] = quiz.form_options(r)
        out.append(d)
    return jsonify(out)


@app.post("/api/answer")
def api_answer():
    """Ответ из тренировки — попадает в ту же статистику, что и всплывашки."""
    d = request.get_json(force=True)
    wid = int(d.get("word_id"))
    action = d.get("action")
    if action not in ("know", "again", "skip"):
        return jsonify({"ok": False, "error": "bad action"}), 400
    try:
        db.log_event(wid, action, int(d.get("ms") or 0))
        srs.grade(wid, action)
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "error": "word deleted"}), 404
    return jsonify({"ok": True})


@app.get("/api/decks")
def api_decks():
    tags = {}
    for r in db.conn().execute("SELECT tags FROM words WHERE tags != ''"):
        for t in r["tags"].split(","):
            t = t.strip()
            if t:
                tags[t] = tags.get(t, 0) + 1
    return jsonify(sorted(tags.items(), key=lambda x: -x[1]))


@app.get("/api/stats")
def api_stats():
    s = srs.stats()
    c = db.conn()

    # Один запрос с группировкой по локальному дню вместо 30 отдельных.
    agg = {r["d"]: r for r in c.execute(
        f"""SELECT {srs.LOCAL_DAY} d, COUNT(*) a, SUM(action='know') k
            FROM events GROUP BY d""")}
    days = []
    for i in range(29, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        r = agg.get(d)
        days.append({"date": d,
                     "shown": (r["a"] if r else 0) or 0,
                     "know": (r["k"] if r else 0) or 0})

    hard = [dict(r) for r in c.execute("""
        SELECT w.id, w.word, w.translation,
               SUM(e.action='again') again_cnt,
               SUM(e.action='know')  know_cnt,
               COUNT(*) shown_cnt
        FROM events e JOIN words w ON w.id = e.word_id
        GROUP BY w.id
        HAVING again_cnt > 0
        ORDER BY again_cnt DESC, shown_cnt DESC
        LIMIT 15""")]

    tags = {}
    for r in c.execute("SELECT tags FROM words WHERE tags != ''"):
        for t in r["tags"].split(","):
            t = t.strip()
            if t:
                tags[t] = tags.get(t, 0) + 1

    return jsonify({"stats": s, "days": days, "hard": hard,
                    "tags": sorted(tags.items(), key=lambda x: -x[1])})


@app.get("/api/words")
def api_words():
    q = (request.args.get("q") or "").strip()
    status = request.args.get("status") or ""
    tag = (request.args.get("tag") or "").strip()

    sql = """SELECT w.*, COALESCE(s.status,'new') status,
                    COALESCE(s.reps,0) reps, COALESCE(s.lapses,0) lapses,
                    s.due_at, COALESCE(s.interval_min,0) interval_min,
                    (SELECT COUNT(*) FROM events e WHERE e.word_id=w.id) shown_cnt,
                    (SELECT COUNT(*) FROM events e WHERE e.word_id=w.id AND e.action='know') know_cnt
             FROM words w LEFT JOIN srs s ON s.word_id=w.id WHERE 1=1"""
    params = []
    if q:
        sql += " AND (w.word LIKE ? OR w.translation LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    if status:
        sql += " AND s.status = ?"
        params.append(status)
    if tag:
        sql += " AND w.tags LIKE ?"
        params.append(f"%{tag}%")
    sql += " ORDER BY w.created_at DESC, w.id DESC LIMIT 800"
    return jsonify([dict(r) for r in db.conn().execute(sql, params)])


@app.post("/api/import")
def api_import():
    data = request.get_json(force=True)
    text = data.get("text", "")
    tags = (data.get("tags") or "").strip()
    level = (data.get("level") or "").strip()

    created = updated = skipped = 0
    for line in text.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        p = parse_line(line)
        if not p:
            skipped += 1
            continue
        _, res = db.add_word(p["word"], ipa=p["ipa"], translation=p["translation"],
                             example_en=p["example_en"], example_ru=p["example_ru"],
                             level=level, tags=tags)
        if res == "created":
            created += 1
        elif res == "updated":
            updated += 1
        else:
            skipped += 1
    return jsonify({"created": created, "updated": updated, "skipped": skipped})


@app.post("/api/word/<int:wid>")
def api_word_update(wid):
    d = request.get_json(force=True)
    fields = ["word", "ipa", "translation", "example_en", "example_ru",
              "level", "tags", "note"]
    sets, params = [], []
    for f in fields:
        if f in d:
            sets.append(f"{f}=?")
            params.append((d[f] or "").strip())
    if sets:
        params.append(wid)
        c = db.conn()
        c.execute(f"UPDATE words SET {', '.join(sets)} WHERE id=?", params)
        c.execute("UPDATE words SET enriched=1 WHERE id=? AND ipa!='' AND translation!=''", (wid,))
        c.commit()
    if "status" in d:
        srs.set_status(wid, d["status"])
    return jsonify({"ok": True})


@app.delete("/api/word/<int:wid>")
def api_word_delete(wid):
    c = db.conn()
    c.execute("DELETE FROM words WHERE id=?", (wid,))
    c.commit()
    return jsonify({"ok": True})


@app.get("/api/settings")
def api_settings_get():
    return jsonify(db.all_settings())


@app.post("/api/settings")
def api_settings_set():
    for k, v in (request.get_json(force=True) or {}).items():
        if k in db.DEFAULTS:
            db.put(k, v)
    return jsonify(db.all_settings())


def run():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app.run(host="127.0.0.1", port=8777, debug=False,
            use_reloader=False, threaded=True)


if __name__ == "__main__":
    db.init()
    run()
