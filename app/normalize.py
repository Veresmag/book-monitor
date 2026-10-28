import re
import unicodedata

_APOS = str.maketrans({"’": "'", "ʼ": "'", "`": "'", "´": "'", "′": "'"})


def norm_text(s: str | None) -> str:
    """Приводит название/автора к единому виду для сопоставления."""
    s = unicodedata.normalize("NFKC", s or "").lower().translate(_APOS)
    s = re.sub(r"[«»“”„\"']", "", s)
    s = re.sub(r"\b(том|книга|кн|частина|ч)\.?\s*(\d+)", r"т\2", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_isbn(s: str | None) -> str | None:
    digits = re.sub(r"[^0-9Xx]", "", s or "")
    return digits.upper() if len(digits) in (10, 13) else None


def title_key(title: str | None, author: str | None) -> str:
    return f"{norm_text(title)}|{norm_text(author)}"
