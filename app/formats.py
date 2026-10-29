"""Формат книги: бумажная, электронная или аудио. Цены этих форматов нельзя сравнивать между собой."""
import re

_EBOOK = re.compile(r"електронн\w*\s+книг|электронн\w*\s+книг|e-?book|\bepub\b|\bfb2\b", re.I)
_AUDIO = re.compile(r"аудіо|аудио|audio", re.I)


def detect(title: str | None, url: str = "", category: str = "", book_format: str = "") -> str:
    text = f"{title or ''} {category or ''} {book_format or ''}"
    low = (url or "").lower()
    if _AUDIO.search(text) or re.search(r"audio", low):
        return "audio"
    if _EBOOK.search(text) or re.search(r"e-?book|elektronn|epub", low):
        return "ebook"
    return "paper"
