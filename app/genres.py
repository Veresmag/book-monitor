"""Определение жанра по категории (хлебным крошкам) и, если её нет, по названию/описанию."""
import re

# порядок важен: романтичное фэнтези проверяется раньше обычного фэнтези
GENRES = [
    ("Романтичне фентезі", r"романтичн\w*\s+фентез|романтичн\w*\s+фэнтез|ромфант|романтасі|romantasy|romantic\s+fantasy"),
    ("Фентезі", r"фентез|фэнтез|fantasy"),
    ("Наукова фантастика", r"фантастик|sci-?fi|science\s+fiction"),
    ("Жахи", r"жахи|хорор|horror"),
    ("Детектив / трилер", r"детектив|трилер|thriller|crime"),
    ("Любовний роман", r"любовн\w*\s+роман|романтик|романс|romance"),
    ("Історична проза", r"історичн\w*\s+(роман|проза|белетристик)"),
    ("Дитяча література", r"дитяч|для\s+дітей|children"),
    ("Нон-фікшн", r"нон-?фікшн|non-?fiction|біографі|саморозвит|психологі|бізнес|історія"),
    ("Сучасна проза", r"сучасна\s+проза|художня\s+література|проза|роман"),
]
TAGS = [
    ("темне", r"темн\w*\s+фентез|dark\s+fantasy|грімдарк|grimdark"),
    ("епічне", r"епічн|epic"),
    ("героїчне", r"героїчн|heroic|sword"),
    ("міське", r"міськ\w*\s+фентез|urban\s+fantasy"),
    ("гумористичне", r"гумористичн\w*\s+фентез|humou?r"),
    ("затишне", r"затишн|cozy"),
]


def _first(rules, text):
    for name, rx in rules:
        if re.search(rx, text, re.I):
            return name
    return None


def classify(category: str | None, title: str | None = "", description: str | None = "") -> tuple[str, str]:
    """-> (жанр, теги через запятую). Категория надёжнее описания, поэтому смотрим её первой."""
    cat = (category or "").lower()
    genre = _first(GENRES, cat) if cat else None
    if not genre:  # запасной вариант: только явные фразы в названии и начале описания
        loose = f"{title or ''} {(description or '')[:400]}".lower()
        genre = _first(GENRES[:2], loose)
    blob = f"{cat} {title or ''} {(description or '')[:400]}".lower()
    tags = [name for name, rx in TAGS if re.search(rx, blob, re.I)] if genre and "фентез" in genre.lower() else []
    return genre or "", ", ".join(tags)
