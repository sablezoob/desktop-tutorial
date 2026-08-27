# -*- coding: utf-8 -*-
"""Дашборд и тренировка: страницы, API и защита от кривых данных."""
import srs


def test_pages_open(client):
    assert client.get("/").status_code == 200
    assert client.get("/train").status_code == 200
    assert client.get("/theme.css").status_code == 200
    assert client.get("/favicon.ico").status_code == 204


def test_theme_is_shared_with_the_card(client):
    """Палитра приходит из того же модуля, что и стили карточки."""
    import theme
    css = client.get("/theme.css").get_data(as_text=True)
    assert f"--example: {theme.c('example')}" in css


def test_session_modes(client):
    for mode in ("selftest", "quiz", "forms", "type", "show"):
        rows = client.get(f"/api/session?mode={mode}&limit=5").get_json()
        assert rows, f"режим {mode} вернул пусто"


def test_quiz_session_brings_options(client):
    rows = client.get("/api/session?mode=quiz&limit=5").get_json()
    assert all(len(r.get("options", [])) in (0, 4) for r in rows)


def test_forms_session_returns_verbs_only(client):
    rows = client.get("/api/session?mode=forms&limit=5").get_json()
    assert rows and all(r["v2"] for r in rows)


def test_review_session_takes_unanswered(client, fresh_db, words):
    fresh_db.log_event(words["winter"], "skip", 14000)
    srs.grade(words["winter"], "skip")
    rows = client.get("/api/session?only=unreviewed&limit=10").get_json()
    assert [r["word"] for r in rows] == ["winter"]


def test_answer_moves_the_word(client, fresh_db, words):
    before = fresh_db.conn().execute(
        "SELECT interval_min FROM srs WHERE word_id=?", (words["draw"],)).fetchone()[0]
    r = client.post("/api/answer", json={"word_id": words["draw"], "action": "know", "ms": 4000})
    assert r.get_json()["ok"]
    after = fresh_db.conn().execute(
        "SELECT interval_min FROM srs WHERE word_id=?", (words["draw"],)).fetchone()[0]
    assert after > before


def test_answer_rejects_unknown_action(client, words):
    r = client.post("/api/answer", json={"word_id": words["draw"], "action": "hack"})
    assert r.status_code == 400


def test_answer_on_deleted_word_returns_404(client):
    r = client.post("/api/answer", json={"word_id": 999999, "action": "know"})
    assert r.status_code == 404


def test_import_and_edit_word(client):
    r = client.post("/api/import", json={"text": "brandnew | новинка | /nju/", "tags": "tmp"})
    assert r.get_json()["created"] == 1
    row = client.get("/api/words?q=brandnew").get_json()[0]
    assert client.post(f"/api/word/{row['id']}", json={"translation": "изменено"}).get_json()["ok"]
    assert client.delete(f"/api/word/{row['id']}").get_json()["ok"]
    assert client.get("/api/words?q=brandnew").get_json() == []


def test_settings_ignore_unknown_keys(client):
    data = client.post("/api/settings", json={"evil": "1", "daily_goal": "7"}).get_json()
    assert "evil" not in data
    assert data["daily_goal"] == "7"


def test_stats_have_all_sections(client):
    d = client.get("/api/stats").get_json()
    for key in ("stats", "days", "hard", "decks", "learned_days",
                "actions", "hours", "forecast", "extra", "recent_learned"):
        assert key in d, f"в статистике нет раздела {key}"
    assert len(d["days"]) == 30
    assert len(d["hours"]) == 24
    assert len(d["forecast"]) == 7
    assert "goal" in d["extra"]


def test_deck_progress_sums_up(client):
    for deck in client.get("/api/stats").get_json()["decks"]:
        assert deck["new"] + deck["learning"] + deck["learned"] == deck["total"]


def test_progress_endpoint(client, fresh_db, words):
    fresh_db.log_event(words["draw"], "know", 3000)
    p = client.get("/api/progress").get_json()
    assert p["done"] == 1 and p["goal"] >= 1


def test_html_is_escaped_in_word_list(client, fresh_db):
    """Слово с разметкой должно вернуться текстом, а не выполниться на странице."""
    fresh_db.add_word("<script>alert(1)</script>", translation="взлом", ipa="/x/")
    rows = client.get("/api/words?q=script").get_json()
    assert rows and rows[0]["word"].startswith("<script>")
