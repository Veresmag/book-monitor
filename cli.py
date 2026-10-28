import argparse
import json
import logging
import os
import re

from app import db, store

DATA_JSON = "docs/data.json"
MISSES_JSON = "data/misses.json"


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9а-яіїєґ]+", "-", s.lower()).strip("-") or "shop"


def load_state() -> None:
    """Восстановить базу из data.json и misses.json (на раннере GitHub базы между запусками нет)."""
    db.init()
    with db.connect() as conn:
        if os.path.exists(DATA_JSON):
            data = json.load(open(DATA_JSON, encoding="utf-8"))
            for b in data.get("books", []):
                for o in b.get("offers", []):
                    slug = o.get("shop_slug") or slugify(o["shop"])
                    store.sync_shops(conn, [{"slug": slug, "name": o["shop"]}])
                    sid = conn.execute("SELECT id FROM shops WHERE slug=?", (slug,)).fetchone()["id"]
                    store.import_offer(conn, sid, {
                        "url": o["url"], "title": b["title"], "author": b.get("author"),
                        "publisher": b.get("publisher"), "isbn": b.get("isbn"), "genre": b.get("genre"),
                        "mood": b.get("mood"), "image": b.get("image"), "year": b.get("year"), "price": o["price"], "old_price": o.get("oldPrice"), "in_stock": o.get("inStock", True),
                    }, o.get("history", []), o.get("seen"))
        if os.path.exists(MISSES_JSON):
            for url, ts in json.load(open(MISSES_JSON, encoding="utf-8")).items():
                conn.execute("INSERT OR IGNORE INTO misses(url,ts) VALUES(?,?)", (url, ts))


def export_state() -> None:
    with db.connect() as conn:
        shops = {r["id"]: r for r in conn.execute("SELECT * FROM shops")}
        books = []
        for b in conn.execute("SELECT * FROM books ORDER BY title COLLATE NOCASE"):
            offers = []
            for o in conn.execute("SELECT * FROM offers WHERE book_id=? ORDER BY price", (b["id"],)):
                hist = {}
                for h in conn.execute("SELECT substr(ts,1,10) d, price FROM price_history "
                                      "WHERE offer_id=? ORDER BY ts", (o["id"],)):
                    hist[h["d"]] = h["price"]
                sh = shops[o["shop_id"]]
                offers.append({"shop": sh["name"], "shop_slug": sh["slug"], "url": o["url"],
                               "price": o["price"], "oldPrice": o["old_price"], "inStock": bool(o["in_stock"]),
                               "seen": o["last_seen"], "history": [[d, p] for d, p in hist.items()]})
            if offers:
                books.append({"title": b["title"], "author": b["author"] or "", "publisher": b["publisher"] or "",
                              "isbn": b["isbn"] or "", "genre": b["genre"] or "", "mood": b["mood"] or "",
                              "image": b["image"] or "", "year": b["year"] or 0,
                              "offers": offers})
        misses = {r["url"]: r["ts"][:10] for r in conn.execute("SELECT url,ts FROM misses ORDER BY url")}
    dump = lambda x: json.dumps(x, ensure_ascii=False, separators=(",", ":"))
    os.makedirs("docs", exist_ok=True)
    with open(DATA_JSON, "w", encoding="utf-8") as f:  # одна книга на строку: удобные диффы в git
        f.write('{"updated":%s,"books":[\n' % dump(store.now()))
        f.write(",\n".join(dump(b) for b in books))
        f.write("\n]}\n")
    os.makedirs("data", exist_ok=True)
    with open(MISSES_JSON, "w", encoding="utf-8") as f:
        f.write("{\n" + ",\n".join(f"{dump(u)}:{dump(t)}" for u, t in misses.items()) + "\n}\n")
    print(f"экспорт: книг {len(books)}, пустых страниц {len(misses)}")


def crawl_all(limit: int, delay: float, only: str | None = None) -> None:
    from app import crawler
    for shop in crawler.load_shops():
        if only and shop["slug"] != only:
            continue
        if not shop.get("enabled", True) and not only:
            continue
        print(shop["slug"], crawler.crawl_shop(shop, limit=limit, delay=delay))


def cmd_probe(args) -> None:
    import httpx
    from app import crawler
    rows = ["| Магазин | Главная | robots.txt | sitemap.xml | Вывод |", "|---|---|---|---|---|"]
    with httpx.Client(headers={"User-Agent": crawler.UA}, timeout=20, follow_redirects=True) as c:
        for shop in crawler.load_shops():
            if not shop.get("enabled", True):
                continue
            res = []
            for path in ("/", "/robots.txt", "/sitemap.xml"):
                try:
                    res.append(str(c.get(shop["base_url"].rstrip("/") + path).status_code))
                except Exception as e:  # noqa: BLE001
                    res.append(type(e).__name__)
            verdict = "доступен" if res[0] == "200" else "ПРОБЛЕМА: возможно, блок зарубежных IP"
            rows.append(f"| {shop['name']} | {res[0]} | {res[1]} | {res[2]} | {verdict} |")
    text = "\n".join(rows)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("## Проверка доступа к магазинам\n\n" + text + "\n")


def _some_urls(c, sm, files=6, depth=0, log=None):
    """Часть адресов из sitemap (для больших сайтов читаем только несколько файлов)."""
    import random
    from app import crawler
    text = crawler._get_text(c, sm)
    if not text:
        return [], 0
    locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", text, flags=re.S)
    if "<sitemapindex" in text and depth < 2:
        kids = locs[:]
        random.shuffle(kids)
        kids.sort(key=lambda k: 0 if re.search(r"prod|book|item|good|tovar|knyg|kniga|catalog|shop", k, re.I) else 1)
        if log is not None:
            log += [k.rsplit("/", 1)[-1] for k in kids[:12]]
        urls = []
        for k in kids[:files]:
            urls += _some_urls(c, k, 2, depth + 1)[0]
        return urls, len(kids)
    return locs, 1


def cmd_analyze(args) -> None:
    """Проверка всех магазинов из config/shops.yaml: доступ, sitemap, читаются ли карточки книг."""
    import random
    import time
    from collections import Counter
    from urllib.parse import urlparse
    import httpx
    from app import crawler
    from app.scraper_jsonld import diagnose, parse_product_page

    random.seed(7)
    rows = ["| Магазин | Главная | Файлов sitemap | Проверено страниц | Книг распознано | С ISBN | С категорией | Фильтр адреса книги |",
            "|---|---|---|---|---|---|---|---|"]
    notes = []
    with httpx.Client(headers={"User-Agent": crawler.UA}, timeout=25, follow_redirects=True) as c:
        for shop in crawler.load_shops():
            name, base = shop["name"], shop["base_url"].rstrip("/")
            if "CHANGE-ME" in base:
                rows.append(f"| {name} | нет адреса | | | | | | впиши base_url в shops.yaml |")
                continue
            try:
                home = str(c.get(base).status_code)
            except Exception as e:  # noqa: BLE001
                rows.append(f"| {name} | {type(e).__name__} | | | | | | |")
                continue
            robots = crawler._get_text(c, base + "/robots.txt")
            urls, nfiles, smlog = [], 0, []
            for sm in crawler._discover_sitemaps(c, shop, robots):
                u, n = _some_urls(c, sm, log=smlog)
                urls += u
                nfiles += n
            urls = list(dict.fromkeys(urls))
            if not urls:
                rows.append(f"| {name} | {home} | 0 | | | | | sitemap не найден или пуст |")
                continue
            sample = random.sample(urls, min(args.sample, len(urls)))
            ok = isbn = cat = 0
            shapes_ok, shapes_all, good, bad = Counter(), Counter(), [], []
            for url in sample:
                segs = [x for x in urlparse(url).path.split("/") if x]
                shape = (len(segs), segs[0] if len(segs) > 1 else "")
                shapes_all[shape] += 1
                html = crawler._get_text(c, url)
                time.sleep(1.0)
                prod = parse_product_page(html, url) if html else None
                if prod:
                    ok += 1; shapes_ok[shape] += 1
                    isbn += 1 if prod.get("isbn") else 0
                    cat += 1 if prod.get("category") else 0
                    if len(good) < 2:
                        good.append(f"{prod['title']} — {prod['price']:.0f}")
                elif len(bad) < 2:
                    bad.append(f"{url} [{diagnose(html) if html else 'страница не загрузилась'}]")
            hint = "—"
            if shapes_ok:
                (n, first), hits = shapes_ok.most_common(1)[0]
                if n > 1 and hits / shapes_all[(n, first)] >= 0.8:
                    hint = "`^https?://[^/]+/%s(/[^/]+){%d}/?$`" % (re.escape(first), n - 1)
                else:
                    hint = "книги в корне сайта, нужен другой признак"
            rows.append(f"| {name} | {home} | {nfiles} | {len(sample)} | {ok} | {isbn} | {cat} | {hint} |")
            notes.append(f"- **{name}**: пример книги: {good[0] if good else 'нет'}; не распознано: {'; '.join(bad) if bad else 'нет'}"
                         + (f"; sitemap-файлы: {', '.join(smlog[:8])}" if smlog else ""))
    text = "\n".join(rows) + "\n\n" + "\n".join(notes)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("## Анализ магазинов\n\n" + text + "\n")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(required=True)
    p = sub.add_parser("probe", help="проверить доступность магазинов"); p.set_defaults(fn=cmd_probe)
    p = sub.add_parser("analyze", help="проверить все магазины и карточки книг")
    p.add_argument("--sample", type=int, default=24); p.set_defaults(fn=cmd_analyze)
    for name, fn in (("daily", None), ("crawl", None)):
        p = sub.add_parser(name)
        p.add_argument("--shop"); p.add_argument("--limit", type=int, default=600)
        p.add_argument("--delay", type=float, default=1.5)
        p.set_defaults(fn=(lambda a: (load_state(), crawl_all(a.limit, a.delay, a.shop), export_state())))
    sub.add_parser("export").set_defaults(fn=lambda a: export_state())
    sub.add_parser("load").set_defaults(fn=lambda a: load_state())
    a = ap.parse_args(); a.fn(a)


if __name__ == "__main__":
    main()
