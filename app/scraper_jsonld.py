"""Универсальный разбор страницы товара: JSON-LD (schema.org) + запасной вариант через meta-теги."""
import json
import re

from bs4 import BeautifulSoup


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _name(v) -> str | None:
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, dict):
        return _name(v.get("name"))
    if isinstance(v, list):
        names = [n for n in (_name(x) for x in v) if n]
        return ", ".join(names) or None
    return None


def _crumbs(soup) -> list[str]:
    out = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk(data):
            if node.get("@type") == "BreadcrumbList":
                for it in node.get("itemListElement", []):
                    n = it.get("name") or (it.get("item") or {}).get("name") if isinstance(it, dict) else None
                    if isinstance(n, str):
                        out.append(n.strip())
    return out


def _old_price(offers, soup):
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    spec = offers.get("priceSpecification") if isinstance(offers, dict) else None
    for sp in ([spec] if isinstance(spec, dict) else spec or []):
        if isinstance(sp, dict) and re.search(r"List|Strikethrough|Original", str(sp.get("priceType", "")), re.I):
            try:
                return float(str(sp.get("price")).replace(",", "."))
            except ValueError:
                pass
    m = soup.find("meta", property="product:original_price:amount")
    try:
        return float(m.get("content").replace(",", ".")) if m else None
    except (ValueError, AttributeError):
        return None


def _price(offers):
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        return None, None, True
    raw = offers.get("price") or offers.get("lowPrice")
    try:
        price = float(str(raw).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        price = None
    avail = str(offers.get("availability", "InStock"))
    in_stock = not re.search(r"OutOfStock|SoldOut|Discontinued", avail, re.I)
    return price, offers.get("priceCurrency") or "UAH", in_stock


def parse_product_page(html: str, url: str) -> dict | None:
    soup = BeautifulSoup(html, "lxml")
    product = None
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk(data):
            t = node.get("@type")
            types = t if isinstance(t, list) else [t]
            if "Product" in types or "Book" in types:
                product = node
                break
        if product:
            break

    if product:
        price, currency, in_stock = _price(product.get("offers"))
        image = product.get("image")
        if isinstance(image, list):
            image = image[0] if image else None
        if isinstance(image, dict):
            image = image.get("url")
        result = {
            "url": url,
            "title": _name(product.get("name")),
            "author": _name(product.get("author")),
            "publisher": _name(product.get("publisher")) or _name(product.get("brand")),
            "isbn": product.get("isbn") or product.get("gtin13") or product.get("gtin"),
            "price": price,
            "currency": currency,
            "in_stock": in_stock,
            "image": image if isinstance(image, str) else None,
            "old_price": _old_price(product.get("offers"), soup),
            "description": _name(product.get("description")) or "",
            "category": " > ".join(c for c in _crumbs(soup)[1:-1]) or _name(product.get("category")) or "",
        }
    else:
        # запасной вариант: Open Graph / product meta
        def meta(prop):
            m = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
            return m.get("content") if m else None

        raw = meta("product:price:amount")
        try:
            price = float(str(raw).replace(",", ".")) if raw else None
        except ValueError:
            price = None
        result = {
            "url": url, "title": meta("og:title"), "author": None, "publisher": None,
            "isbn": None, "price": price, "currency": meta("product:price:currency") or "UAH",
            "in_stock": True, "image": meta("og:image"),
        }

    if not result["title"] or result["price"] is None:
        return None
    return result
