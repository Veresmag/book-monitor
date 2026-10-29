import os
import sqlite3

DB_PATH = os.environ.get("BOOKS_DB", "data/books.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS shops (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    type TEXT,
    base_url TEXT
);
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    isbn TEXT,
    title_key TEXT,
    title TEXT NOT NULL,
    author TEXT,
    publisher TEXT,
    image TEXT,
    year INTEGER,
    description TEXT,
    genre TEXT,          -- заполнится на этапе жанровой классификации
    mood TEXT,           -- теги настроения (этап анализа отзывов)
    rating REAL,         -- рейтинг (этап рейтингов)
    rating_count INTEGER
);
CREATE INDEX IF NOT EXISTS idx_books_isbn ON books(isbn);
CREATE INDEX IF NOT EXISTS idx_books_tkey ON books(title_key);

CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    book_id INTEGER NOT NULL REFERENCES books(id),
    url TEXT UNIQUE NOT NULL,
    title_raw TEXT,
    price REAL,
    old_price REAL,
    currency TEXT DEFAULT 'UAH',
    in_stock INTEGER DEFAULT 1,
    last_seen TEXT
);
CREATE INDEX IF NOT EXISTS idx_offers_book ON offers(book_id);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY,
    offer_id INTEGER NOT NULL REFERENCES offers(id),
    price REAL,
    in_stock INTEGER,
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hist_offer ON price_history(offer_id, ts);

CREATE TABLE IF NOT EXISTS misses (url TEXT PRIMARY KEY, ts TEXT NOT NULL);
"""


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # SQLite LIKE не понимает регистр кириллицы, поэтому своя функция
    conn.create_function(
        "nlike", 2, lambda s, q: 1 if (q or "").lower() in (s or "").lower() else 0
    )
    return conn


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
