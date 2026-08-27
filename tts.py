# -*- coding: utf-8 -*-
"""Озвучка слов и примеров нейроголосом Microsoft.

Файл озвучивается один раз и кладётся в кеш: дальше воспроизведение идёт
офлайн и мгновенно. Синтез занимает секунду-две, поэтому его нельзя делать
в потоке интерфейса — карточка бы подвисала.
"""
import hashlib
import logging
import os
import subprocess
import sys
import threading

import db

log = logging.getLogger("vocab.tts")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "data", "audio")
VOICES = {
    "uk-female": "en-GB-SoniaNeural",
    "uk-male": "en-GB-RyanNeural",
    "us-female": "en-US-AriaNeural",
    "us-male": "en-US-GuyNeural",
}


def is_enabled():
    return db.get("tts_enabled", "0") == "1"


def voice():
    return VOICES.get(db.get("tts_voice", "uk-female"), VOICES["uk-female"])


def _path(text):
    key = hashlib.sha1(f"{voice()}|{text}".encode("utf-8")).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"{key}.mp3")


def _synthesize(text, path):
    """Скачивает озвучку. Требует сети, но только при первом обращении."""
    import asyncio

    os.makedirs(os.path.dirname(path), exist_ok=True)

    import edge_tts

    async def go():
        comm = edge_tts.Communicate(text, voice())
        await comm.save(path)

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(go())


def _play(path):
    if sys.platform != "win32":
        return
    # Проигрываем скрытым процессом: свой аудиовыход тянуть в приложение
    # ради одной кнопки незачем.
    script = (f'$p = New-Object -ComObject WMPlayer.OCX;'
              f'$p.URL = "{path}"; $p.controls.play();'
              f'Start-Sleep -Seconds 4; $p.close()')
    subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def say(text):
    """Озвучить текст. Возвращает сразу — работа идёт в фоне."""
    text = (text or "").strip()
    if not text or not is_enabled():
        return False

    def worker():
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            path = _path(text)
            if not os.path.exists(path):
                _synthesize(text, path)
            _play(path)
        except Exception:
            log.exception("Не удалось озвучить: %s", text[:40])

    threading.Thread(target=worker, daemon=True).start()
    return True


def cache_size():
    if not os.path.isdir(CACHE_DIR):
        return 0, 0
    files = [f for f in os.listdir(CACHE_DIR) if f.endswith(".mp3")]
    total = sum(os.path.getsize(os.path.join(CACHE_DIR, f)) for f in files)
    return len(files), total


def clear_cache():
    n = 0
    if os.path.isdir(CACHE_DIR):
        for f in os.listdir(CACHE_DIR):
            if f.endswith(".mp3"):
                os.remove(os.path.join(CACHE_DIR, f))
                n += 1
    return n
