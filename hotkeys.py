# -*- coding: utf-8 -*-
"""Глобальные горячие клавиши Windows.

Карточка намеренно не забирает фокус, иначе она обрывала бы набор текста.
Побочный эффект — обычные нажатия до неё не доходят. Поэтому ответы
навешены на системные сочетания, которые ловятся в любом активном окне.
"""
import ctypes
import logging
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter

log = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

# id -> (модификаторы, virtual-key, подпись для меню)
BINDINGS = {
    1: (MOD_CONTROL | MOD_ALT, 0x31, "Ctrl+Alt+1"),   # знаю
    2: (MOD_CONTROL | MOD_ALT, 0x32, "Ctrl+Alt+2"),   # ещё раз
    3: (MOD_CONTROL | MOD_ALT, 0x33, "Ctrl+Alt+3"),   # пропустить
    4: (MOD_CONTROL | MOD_ALT, 0x30, "Ctrl+Alt+0"),   # показать слово сейчас
}


class HotkeyFilter(QAbstractNativeEventFilter):
    """Ловит WM_HOTKEY в очереди сообщений главного потока."""

    def __init__(self, on_hotkey):
        super().__init__()
        self.on_hotkey = on_hotkey

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            try:
                msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
                if msg.message == WM_HOTKEY:
                    self.on_hotkey(int(msg.wParam))
            except (ValueError, TypeError, OSError):
                pass
        return False, 0


def register(app, on_hotkey):
    """Регистрирует сочетания. Возвращает список подписей, которые удалось занять."""
    import sys
    if sys.platform != "win32":
        return []
    filt = HotkeyFilter(on_hotkey)
    app.installNativeEventFilter(filt)
    app._hotkey_filter = filt          # держим ссылку, иначе фильтр соберёт GC

    user32 = ctypes.windll.user32
    ok = []
    for hid, (mods, vk, label) in BINDINGS.items():
        if user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
            ok.append(label)
        else:
            log.warning("Сочетание %s занято другой программой", label)
    return ok


def unregister():
    import sys
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    for hid in BINDINGS:
        user32.UnregisterHotKey(None, hid)
