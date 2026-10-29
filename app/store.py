"""Запись результатов парсинга в БД: сопоставление книг, предложения, история цен."""
from datetime import datetime, timezone

from .normalize import clean_isbn, title_key


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sync_shops(conn, shops: list[dict]) -> None:
    for s in shops:
        conn.execute(
            "INSERT INTO shops(slug,name,type,base_url) VALUES(?,?,?,?) "
            "ON CONFLICT(slug) DO UPDATE SET name=excluded.name, type=excluded.type, "
            "base_url=excluded.base_url",
            (s["slug"], s["name"], s.get("type"), s.get("base_url")),
        )


def _find_or_create_book(conn, p: dict) -> int:
    isbn = clean_isbn(p.get("isbn"))
    tkey = title_key(p["title"], p.get("author"))
    row = None
    if isbn:
        row = conn.execute("SELECT id FROM books WHERE isbn=?", (isbn,)).fetchone()
    if row is None:
        # без ISBN совпадаем по автору+названию с любой книгой;
        # с ISBN совпадаем только с записью, где ISBN ещё не известен
        sql = "SELECT id FROM books WHERE title_key=?" + (" AND isbn IS NULL" if isbn else "")
        row = conn.execute(sql, (tkey,)).fetchone()
    if row:
        conn.execute(
            "UPDATE books SET isbn=COALESCE(isbn,?), author=COALESCE(author,?), "
            "publisher=COALESCE(publisher,?), image=COALESCE(image,?), "
            "genre=COALESCE(NULLIF(genre,''),?), mood=COALESCE(NULLIF(mood,''),?), "
            "year=COALESCE(year,?), description=COALESCE(NULLIF(description,''),?), fmt=COALESCE(NULLIF(fmt,''),?) WHERE id=?",
            (isbn, p.get("author"), p.get("publisher"), p.get("image"),
             p.get("genre") or None, p.get("mood") or None, p.get("year"), p.get("description") or None, p.get("format") or None, row["id"]),
        )
        return row["id"]
    cur = conn.execute(
        "INSERT INTO books(isbn,title_key,title,author,publisher,image,genre,mood,year,description,fmt) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (isbn, tkey, p["title"], p.get("author"), p.get("publisher"), p.get("image"),
         p.get("genre") or None, p.get("mood") or None, p.get("year"), p.get("description") or None, p.get("format") or None),
    )
    return cur.lastrowid


def upsert_offer(conn, shop_id: int, p: dict) -> None:
    book_id = _find_or_create_book(conn, p)
    ts = now()
    in_stock = 1 if p.get("in_stock", True) else 0
    existing = conn.execute("SELECT id FROM offers WHERE url=?", (p["url"],)).fetchone()
    if existing:
        offer_id = existing["id"]
        conn.execute(
            "UPDATE offers SET book_id=?, title_raw=?, price=?, old_price=?, currency=?, in_stock=?, last_seen=? "
            "WHERE id=?",
            (book_id, p["title"], p["price"], p.get("old_price"), p.get("currency"), in_stock, ts, offer_id),
        )
    else:
        offer_id = conn.execute(
            "INSERT INTO offers(shop_id,book_id,url,title_raw,price,old_price,currency,in_stock,last_seen) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (shop_id, book_id, p["url"], p["title"], p["price"], p.get("old_price"), p.get("currency"), in_stock, ts),
        ).lastrowid

    last = conn.execute(
        "SELECT price,in_stock,ts FROM price_history WHERE offer_id=? ORDER BY ts DESC LIMIT 1",
        (offer_id,),
    ).fetchone()
    # точка в историю: при изменении цены/наличия или раз в сутки
    if not last or last["price"] != p["price"] or last["in_stock"] != in_stock \
            or last["ts"][:10] != ts[:10]:
        conn.execute(
            "INSERT INTO price_history(offer_id,price,in_stock,ts) VALUES(?,?,?,?)",
            (offer_id, p["price"], in_stock, ts),
        )


def import_offer(conn, shop_id: int, p: dict, history: list, seen: str | None) -> None:
    """Восстановление предложения из data.json (с историей цен)."""
    book_id = _find_or_create_book(conn, p)
    conn.execute("UPDATE books SET genre=COALESCE(NULLIF(genre,''),?), mood=COALESCE(NULLIF(mood,''),?) WHERE id=?",
                 (p.get("genre") or None, p.get("mood") or None, book_id))
    in_stock = 1 if p.get("in_stock", True) else 0
    conn.execute(
        "INSERT INTO offers(shop_id,book_id,url,title_raw,price,old_price,currency,in_stock,last_seen) "
        "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO NOTHING",
        (shop_id, book_id, p["url"], p["title"], p["price"], p.get("old_price"), "UAH", in_stock, seen))
    offer_id = conn.execute("SELECT id FROM offers WHERE url=?", (p["url"],)).fetchone()["id"]
    for day, price in history:
        conn.execute("INSERT INTO price_history(offer_id,price,in_stock,ts) VALUES(?,?,?,?)",
                     (offer_id, price, in_stock, f"{day}T00:00:00+00:00"))
