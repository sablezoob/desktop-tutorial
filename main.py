# -*- coding: utf-8 -*-
"""Точка входа: иконка в трее, планировщик показов, локальный дашборд."""
import ctypes
import logging
import os
import sqlite3
import sys
import threading
import time
import webbrowser
from datetime import datetime
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import ai
import aiworker
import db
import hotkeys
import srs
import webapp
from popup import Popup

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "data", "app.log")
DASHBOARD_URL = "http://127.0.0.1:8777/"
MUTEX_NAME = "VocabPopup_SingleInstance_Mutex"

log = logging.getLogger("vocab")


def setup_logging():
    """Без лога упавшее приложение молча исчезает из трея — причину не найти."""
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    h = RotatingFileHandler(LOG_PATH, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(h)

    def hook(exc_type, exc, tb):
        log.critical("Необработанная ошибка", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook


def already_running():
    """Второй экземпляр занял бы порт дашборда и показывал бы двойные карточки."""
    if sys.platform != "win32":
        return False
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return kernel32.GetLastError() == 183      # ERROR_ALREADY_EXISTS


def make_icon(letter="A", bg="#3a86ff"):
    """Иконка трея рисуется в коде — не тащим бинарник в репозиторий."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(bg))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(QColor("#ffffff"))
    p.setFont(QFont("Segoe UI", 34, QFont.Bold))
    p.drawText(pm.rect(), Qt.AlignCenter, letter)
    p.end()
    return QIcon(pm)


# Состояния SHQueryUserNotificationState, при которых Windows сам прячет
# уведомления: полноэкранное приложение, игра D3D, режим презентации, Store-приложение.
BUSY_STATES = {2, 3, 4, 7}


def is_fullscreen_active():
    """True, если идёт полноэкранное приложение (игра, презентация, звонок).

    Сравнивать размер окна с размером экрана нельзя: у развёрнутого окна
    Windows рисует невидимые рамки, и оно оказывается на 16 px больше экрана —
    обычный максимизированный браузер выглядел бы полноэкранным. Спрашиваем
    систему тем же способом, каким она решает судьбу собственных уведомлений.
    """
    if sys.platform != "win32":
        return False
    try:
        state = ctypes.c_int()
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0:
            return state.value in BUSY_STATES
    except Exception:
        log.exception("Не удалось определить полноэкранное окно")
    return False


def in_quiet_hours():
    if db.get("quiet_enabled", "1") != "1":
        return False
    try:
        h1, m1 = [int(x) for x in db.get("quiet_from", "23:00").split(":")]
        h2, m2 = [int(x) for x in db.get("quiet_to", "08:00").split(":")]
    except (ValueError, AttributeError):
        return False
    now = datetime.now()
    start, end = h1 * 60 + m1, h2 * 60 + m2
    cur = now.hour * 60 + now.minute
    if start == end:
        return False
    if start > end:                     # интервал переходит через полночь
        return cur >= start or cur < end
    return start <= cur < end


class App:
    def __init__(self):
        db.init()
        self.qt = QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)

        self.popup = Popup()
        self.popup.answered.connect(self.on_answer)
        self.popup.unknown_word.connect(self.on_unknown_word)
        self._skip_reason = None
        self._interval_min = 3
        self._due_at = 0.0

        self.tray = QSystemTrayIcon(make_icon("A"))
        self.tray.activated.connect(self.on_tray_click)

        self.hk_labels = hotkeys.register(self.qt, self.on_hotkey)
        self.qt.aboutToQuit.connect(hotkeys.unregister)
        log.info("Горячие клавиши заняты: %s", ", ".join(self.hk_labels) or "нет")

        self.build_menu()
        self.tray.show()

        self.timer = QTimer()
        self.timer.timeout.connect(self.on_timer)
        self.restart_timer()

        threading.Thread(target=self.serve, daemon=True).start()
        aiworker.start()
        log.info("Нейросеть: %s", "включена" if ai.is_enabled() else "выключена")

    def serve(self):
        try:
            webapp.run()
        except OSError:
            log.exception("Дашборд не поднялся — порт 8777 занят")

    # ---------- меню ----------
    def build_menu(self):
        m = QMenu()

        hk = dict((lbl.split("+")[-1], lbl) for lbl in self.hk_labels)
        a_now = QAction("Показать слово сейчас" + (f"\t{hk['0']}" if "0" in hk else ""), m)
        a_now.triggered.connect(lambda: self.show_next(force=True))
        m.addAction(a_now)

        self.a_pause = QAction("Пауза", m, checkable=True)
        self.a_pause.setChecked(db.get("paused", "0") == "1")
        self.a_pause.toggled.connect(self.on_pause)
        m.addAction(self.a_pause)

        m.addSeparator()

        sub_int = QMenu("Интервал", m)
        grp = QActionGroup(sub_int)
        cur = db.get_int("interval_min", 3)
        for n in (1, 2, 3, 5, 10, 15, 30, 60):
            a = QAction(f"{n} мин", sub_int, checkable=True)
            a.setChecked(n == cur)
            a.triggered.connect(lambda _, v=n: self.set_interval(v))
            grp.addAction(a)
            sub_int.addAction(a)
        m.addMenu(sub_int)

        sub_pos = QMenu("Где показывать", m)
        grp2 = QActionGroup(sub_pos)
        positions = [("top-center", "Сверху по центру"), ("top-left", "Сверху слева"),
                     ("top-right", "Сверху справа"), ("center", "По центру экрана"),
                     ("bottom-left", "Снизу слева"), ("bottom-center", "Снизу по центру"),
                     ("bottom-right", "Снизу справа")]
        curpos = db.get("corner", "top-center")
        for key, title in positions:
            a = QAction(title, sub_pos, checkable=True)
            a.setChecked(key == curpos)
            a.triggered.connect(lambda _, v=key: db.put("corner", v))
            grp2.addAction(a)
            sub_pos.addAction(a)
        m.addMenu(sub_pos)

        m.addSeparator()

        a_dash = QAction("Дашборд и слова…", m)
        a_dash.triggered.connect(lambda: webbrowser.open(DASHBOARD_URL))
        m.addAction(a_dash)

        if self.hk_labels:
            info = QAction("Клавиши: 1 — знаю, 2 — ещё раз, 3 — пропустить", m)
            info.setEnabled(False)
            m.addAction(info)

        m.addSeparator()
        a_quit = QAction("Выход", m)
        a_quit.triggered.connect(self.qt.quit)
        m.addAction(a_quit)

        self.menu = m
        self.tray.setContextMenu(m)

    def on_tray_click(self, reason):
        if reason == QSystemTrayIcon.Trigger:      # левый клик
            self.show_next(force=True)
        elif reason == QSystemTrayIcon.DoubleClick:
            webbrowser.open(DASHBOARD_URL)

    def on_hotkey(self, hid):
        if hid == 4:
            self.show_next(force=True)
        elif self.popup.isVisible():
            self.popup.answer({1: "know", 2: "again", 3: "skip"}[hid])

    # ---------- планировщик ----------
    # Тикаем часто, а показываем по накопленному времени. Если ждать полного
    # интервала, смена настройки из дашборда вступала бы в силу только на
    # следующем тике старого интервала: поставил 1 минуту вместо часа —
    # и ждёшь час.
    TICK_MS = 10_000

    def restart_timer(self):
        minutes = max(1, db.get_int("interval_min", 3))
        self._interval_min = minutes
        self._due_at = time.monotonic() + minutes * 60
        self.timer.start(self.TICK_MS)
        self.tray.setToolTip(f"Vocab Popup — каждые {minutes} мин")

    def set_interval(self, minutes):
        db.put("interval_min", minutes)
        self.restart_timer()

    def on_pause(self, checked):
        db.put("paused", "1" if checked else "0")
        self.tray.setIcon(make_icon("A", "#5b6272" if checked else "#3a86ff"))

    def on_timer(self):
        want = max(1, db.get_int("interval_min", 3))
        if want != self._interval_min:
            # интервал изменили из дашборда — пересчитываем срок показа
            log.info("Интервал изменён: %s -> %s мин", self._interval_min, want)
            self._due_at -= (self._interval_min - want) * 60
            self._interval_min = want
            self.tray.setToolTip(f"Vocab Popup — каждые {want} мин")
        if time.monotonic() >= self._due_at:
            self._due_at = time.monotonic() + want * 60
            self.show_next()

    def note_skip(self, reason):
        """Пишет в лог смену причины молчания — иначе непонятно, почему нет карточек."""
        if reason == self._skip_reason:
            return
        if reason:
            log.info("Показы приостановлены: %s", reason)
        else:
            log.info("Показы возобновлены")
        self._skip_reason = reason

    def show_next(self, force=False):
        try:
            if force:
                # закрываем текущую карточку по-честному, с записью события
                if self.popup.isVisible():
                    self.popup.answer("skip")
            else:
                if self.popup.isVisible():
                    return
                reason = ("пауза" if db.get("paused", "0") == "1" else
                          "тихие часы" if in_quiet_hours() else
                          "полноэкранное приложение" if is_fullscreen_active() else None)
                if reason:
                    self.note_skip(reason)
                    return
                self.note_skip(None)
            row = srs.next_word()
            if row is None:
                if force:
                    self.tray.showMessage("Vocab Popup",
                                          "Словарь пуст. Откройте дашборд и добавьте слова.",
                                          QSystemTrayIcon.Information, 4000)
                return
            self.popup.show_word(row)
        except Exception:
            log.exception("Ошибка при показе слова")

    def on_unknown_word(self, word, context):
        """Слово, отмеченное в примере. Разбор идёт фоном — интерфейс не ждёт."""
        try:
            wid, res = aiworker.queue_unknown(word, context)
            log.info("Незнакомое слово «%s»: %s", word, res)
            if res == "exists":
                self.tray.showMessage("Vocab Popup", f"«{word}» уже есть в словаре",
                                      QSystemTrayIcon.Information, 3000)
            elif not ai.is_enabled():
                self.tray.showMessage(
                    "Vocab Popup",
                    f"«{word}» добавлено, но перевод не подтянуть: "
                    "нейросеть выключена в настройках дашборда.",
                    QSystemTrayIcon.Warning, 5000)
        except Exception:
            log.exception("Не удалось поставить слово в очередь")

    def on_answer(self, word_id, action, ms):
        try:
            db.log_event(word_id, action, ms)
            srs.grade(word_id, action)
        except sqlite3.IntegrityError:
            # Слово удалили из дашборда, пока карточка висела на экране.
            log.info("Ответ по удалённому слову id=%s пропущен", word_id)
        except Exception:
            log.exception("Не удалось записать ответ word_id=%s", word_id)

    def run(self):
        if db.get("paused", "0") == "1":
            self.tray.setIcon(make_icon("A", "#5b6272"))
        QTimer.singleShot(4000, lambda: self.show_next())
        log.info("Запущено. Интервал %s мин", db.get_int("interval_min", 3))
        sys.exit(self.qt.exec())


if __name__ == "__main__":
    setup_logging()
    if already_running():
        log.warning("Экземпляр уже запущен — выходим")
        ctypes.windll.user32.MessageBoxW(
            None, "Vocab Popup уже запущен — ищите иконку в трее.",
            "Vocab Popup", 0x40)
        sys.exit(0)
    try:
        App().run()
    except SystemExit:
        raise
    except Exception:
        log.critical("Не удалось запустить", exc_info=True)
        raise
