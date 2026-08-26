# -*- coding: utf-8 -*-
"""Разбор трёх форм глагола из строки карточки.

В файле колоды формы записаны компактно, одной строкой:
    перевод · V2 — V3     и     /ipa1/ · /ipa2/ · /ipa3/
Здесь они раскладываются по отдельным полям, чтобы карточка могла показать
их таблицей, а режим проверки — спрятать V2 и V3, оставив только V1.
"""
import re

DOT = "·"
DASH_RE = re.compile(r"\s+[—–-]\s+")


def split_translation(translation):
    """'рисовать; тянуть · drew — drawn' -> ('рисовать; тянуть', 'drew', 'drawn')"""
    text = (translation or "").strip()
    if DOT not in text:
        # запасной формат старых карточек: 'писать (wrote — written)'
        m = re.match(r"^(.*?)\s*\(([^()]*[—–-][^()]*)\)\s*$", text)
        if not m:
            return text, "", ""
        base, forms = m.group(1).strip(), m.group(2)
    else:
        base, _, forms = text.partition(DOT)
        base, forms = base.strip(), forms.strip()

    parts = DASH_RE.split(forms)
    if len(parts) < 2:
        return text, "", ""
    return base, parts[0].strip(), parts[1].strip()


def split_ipa(ipa):
    """'/drɔː/ · /druː/ · /drɔːn/' -> ('/drɔː/', '/druː/', '/drɔːn/')"""
    parts = [p.strip() for p in (ipa or "").split(DOT) if p.strip()]
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1], parts[2]


def parse(translation, ipa):
    """Возвращает (базовый перевод, v2, v3, ipa1, ipa2, ipa3)."""
    base, v2, v3 = split_translation(translation)
    i1, i2, i3 = split_ipa(ipa)
    return base, v2, v3, i1, i2, i3
