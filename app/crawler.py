"""Сборщик цен: sitemap -> страницы товаров -> JSON-LD -> БД. Вежливый режим: паузы, robots.txt."""
import gzip
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx
import yaml

from . import db, store
from .genres import classify
from .scraper_jsonld import parse_product_page

log = logging.getLogger("crawler")
UA = "BookPriceMonitor/0.2 (personal price comparison, polite, 1 req/1.5s)"


def load_shops(path: str = "config/shops.yaml") -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["shops"]


def _get_text(client: httpx.Client, url: str) -> str | None:
    try:
        r = client.get(url)
        r.raise_for_status()
        if url.endswith(".gz"):
            return gzip.decompress(r.content).decode("utf-8", "replace")
        return r.text
    except Exception as e:  # noqa: BLE001
        log.warning("не удалось загрузить %s: %s", url, e)
        return None


def _sitemap_urls(client, url: str, depth: int = 0) -> list[str]:
    text = _get_text(client, url)
    if not text:
        return []
    locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", text, flags=re.S)
    if "<sitemapindex" in text and depth < 3:
        out: list[str] = []
        for sm in locs:
            out += _sitemap_urls(client, sm, depth + 1)
        return out
    return locs


def _discover_sitemaps(client, shop: dict, robots_text: str | None) -> list[str]:
    if shop.get("sitemaps"):
        return shop["sitemaps"]
    found = re.findall(r"(?im)^sitemap:\s*(\S+)", robots_text or "")
    return found or [urljoin(shop["base_url"], "/sitemap.xml")]


def crawl_shop(shop: dict, limit: int = 50, delay: float = 1.5, recheck_hours: int = 20) -> dict:
    stats = {"seen": 0, "saved": 0, "skipped": 0, "failed": 0}
    with httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True) as client:
        robots_text = _get_text(client, urljoin(shop["base_url"], "/robots.txt"))
        rp = RobotFileParser()
        rp.parse((robots_text or "").splitlines())

        urls: list[str] = []
        for sm in _discover_sitemaps(client, shop, robots_text):
            urls += _sitemap_urls(client, sm)
        rx = re.compile(shop["product_url_regex"]) if shop.get("product_url_regex") else None
        if rx:
            urls = [u for u in urls if rx.search(u)]
        log.info("%s: найдено %d ссылок", shop["slug"], len(urls))

        with db.connect() as conn:
            store.sync_shops(conn, [shop])
            shop_id = conn.execute("SELECT id FROM shops WHERE slug=?", (shop["slug"],)).fetchone()["id"]
            seen = {r["url"]: r["last_seen"] or "" for r in conn.execute(
                "SELECT url,last_seen FROM offers WHERE shop_id=?", (shop_id,))}
            miss = {r["url"]: r["ts"] for r in conn.execute("SELECT url,ts FROM misses")}
            iso = lambda h: (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat(timespec="seconds")
            cut_offer, cut_miss = iso(recheck_hours), iso(24 * 14)

            def due(u):  # (когда проверяли, порог свежести)
                if u in seen:
                    return seen[u], cut_offer
                if u in miss:
                    return miss[u], cut_miss
                return "", cut_offer

            # сначала ни разу не виденные, потом самые давние: так за несколько дней обходится весь каталог
            for url in sorted(urls, key=lambda u: due(u)[0]):
                if stats["seen"] >= limit:
                    break
                ts, cut = due(url)
                if ts > cut or not rp.can_fetch(UA, url):
                    stats["skipped"] += 1
                    continue
                stats["seen"] += 1
                html = _get_text(client, url)
                time.sleep(delay)
                product = parse_product_page(html, url) if html else None
                if not product:
                    stats["failed"] += 1
                    if html:  # страница загрузилась, но товара на ней нет
                        conn.execute("INSERT OR REPLACE INTO misses(url,ts) VALUES(?,?)", (url, store.now()))
                        conn.commit()
                    continue
                product["genre"], product["mood"] = classify(
                    product.get("category"), product["title"], product.get("description"))
                store.upsert_offer(conn, shop_id, product)
                conn.commit()
                stats["saved"] += 1
    return stats
