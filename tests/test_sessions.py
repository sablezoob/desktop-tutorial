# -*- coding: utf-8 -*-
"""Статистика тренировок: итоги сессий и источник ответа.

Отдельные ответы и так лежат в events, но по ним не видно, где кончилась одна
сессия и началась другая — и не отличить фоновый показ от осознанного разбора.
"""


def test_finish_writes_session(client, fresh_db):
    r = client.post("/api/session/finish", json={
        "mode": "quiz", "deck": "test", "total": 10,
        "right": 7, "wrong": 3, "skipped": 0, "seconds": 95})
    assert r.get_json()["ok"]
    row = fresh_db.conn().execute("SELECT * FROM sessions").fetchone()
    assert row["mode"] == "quiz" and row["total"] == 10 and row["right_cnt"] == 7


def test_summary_counts_accuracy(client):
    for right, wrong in ((8, 2), (5, 5)):
        client.post("/api/session/finish", json={
            "mode": "selftest", "deck": "", "total": right + wrong,
            "right": right, "wrong": wrong, "skipped": 0, "seconds": 60})
    d = client.get("/api/sessions").get_json()
    assert d["sessions"] == 2
    assert d["cards"] == 20
    assert d["accuracy"] == 65, "13 верных из 20 ответов"
    assert d["minutes"] == 2


def test_summary_splits_by_mode(client):
    client.post("/api/session/finish", json={"mode": "quiz", "total": 10, "right": 9,
                                             "wrong": 1, "skipped": 0, "seconds": 30})
    client.post("/api/session/finish", json={"mode": "forms", "total": 5, "right": 2,
                                             "wrong": 3, "skipped": 0, "seconds": 20})
    modes = {m["mode"]: m for m in client.get("/api/sessions").get_json()["by_mode"]}
    assert modes["quiz"]["cards"] == 10 and modes["quiz"]["right_cnt"] == 9
    assert modes["forms"]["cards"] == 5


def test_history_keeps_order(client):
    for i in range(3):
        client.post("/api/session/finish", json={"mode": f"m{i}", "total": i + 1,
                                                 "right": i, "wrong": 1, "skipped": 0,
                                                 "seconds": 10})
    recent = client.get("/api/sessions").get_json()["recent"]
    assert [r["mode"] for r in recent] == ["m2", "m1", "m0"], "новые сверху"


def test_answer_source_is_recorded(client, fresh_db, words):
    client.post("/api/answer", json={"word_id": words["draw"], "action": "know",
                                     "ms": 3000, "source": "train"})
    client.post("/api/answer", json={"word_id": words["hide"], "action": "know", "ms": 3000})
    d = client.get("/api/sessions").get_json()
    assert d["answers_from_train"] == 1
    assert d["answers_from_popup"] == 1, "без указания источника ответ считается фоновым"


def test_empty_history_is_valid(client):
    d = client.get("/api/sessions").get_json()
    assert d["sessions"] == 0 and d["accuracy"] == 0
    assert d["recent"] == [] and len(d["days"]) == 14


def test_bad_payload_does_not_break(client):
    r = client.post("/api/session/finish", json={"mode": "quiz", "total": "много"})
    assert r.status_code == 400
