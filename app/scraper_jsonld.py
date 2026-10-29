"""Универсальный разбор страницы товара: JSON-LD (schema.org) + запасной вариант через meta-теги."""
import json
import re
from urllib.parse import urljoin

from .formats import detect as detect_format

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


def _year(node, soup):
    """Год издания: JSON-LD, itemprop=datePublished или строка «Рік видання» в характеристиках."""
    for k in ("datePublished", "copyrightYear", "releaseDate"):
        m = re.search(r"(?:19|20)\d{2}", str((node or {}).get(k) or ""))
        if m:
            return int(m.group())
    el = soup.find(attrs={"itemprop": "datePublished"})
    if el:
        m = re.search(r"(?:19|20)\d{2}", el.get("content") or el.get_text() or "")
        if m:
            return int(m.group())
    text = soup.get_text(" ", strip=True)
    m = re.search(r"(?:Рік\s+(?:видання|випуску|публікації)|Год\s+(?:издания|выпуска))\D{0,30}((?:19|20)\d{2})", text, re.I)
    return int(m.group(1)) if m else None


def _clean_desc(t, min_len=0):
    """Аннотация: без HTML-тегов, одним абзацем, не длиннее 1500 символов."""
    if not t:
        return ""
    t = BeautifulSoup(str(t), "lxml").get_text(" ", strip=True)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) < min_len:
        return ""
    return t[:1500].rsplit(" ", 1)[0] + "…" if len(t) > 1500 else t


_LABELS = {
    "author": r"автор(?:и)?(?:\s+книги)?", "publisher": r"видавництво|видавець", "isbn": r"isbn(?:-1[03])?",
}


def _chars(soup):
    """Характеристики на странице («Автор: …», «ISBN: …»), в том числе таблицей из двух столбцов."""
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    lines = [x.strip() for x in soup.get_text("\n", strip=True).split("\n") if x.strip()][:3000]
    out = {}
    for i, line in enumerate(lines):
        for key, rx in _LABELS.items():
            if key in out:
                continue
            if re.fullmatch(rf"(?:{rx})\s*[:：]?", line, re.I) and i + 1 < len(lines) and len(lines[i + 1]) < 200:
                out[key] = lines[i + 1]
                continue
            m = re.match(rf"(?:{rx})\s*[:：]\s*(.{{1,200}})$", line, re.I)
            if m:
                out[key] = m.group(1).strip()
    return out


def _num(v):
    if v is None:
        return None
    m = re.search(r"\d+(?:\.\d+)?", str(v).replace("\xa0", "").replace(" ", "").replace(",", "."))
    return float(m.group()) if m else None


def _microdata(soup):
    """schema.org в виде атрибутов itemprop (старый, но частый способ разметки)."""
    node = soup.find(attrs={"itemtype": re.compile(r"schema\.org/(Product|Book)", re.I)})
    if not node:
        return None

    def prop(name):
        el = node.find(attrs={"itemprop": name})
        if not el:
            return None
        v = el.get("content") or el.get("href") or el.get_text(" ", strip=True)
        return v.strip() if v else None

    avail = prop("availability") or ""
    return {
        "title": prop("name"), "author": prop("author"), "publisher": prop("publisher") or prop("brand"),
        "isbn": prop("isbn") or prop("gtin13"), "price": _num(prop("price")),
        "currency": prop("priceCurrency") or "UAH",
        "in_stock": not re.search(r"OutOfStock|SoldOut|Discontinued", avail, re.I),
        "image": prop("image"), "old_price": None, "description": prop("description") or "",
        "category": " > ".join(c for c in _crumbs(soup)[1:-1]),
    }


def diagnose(html: str) -> str:
    """Короткое описание разметки страницы: чем она отличается от ожидаемой."""
    soup = BeautifulSoup(html, "lxml")
    types = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            types += [str(n.get("@type")) for n in _walk(json.loads(tag.string or tag.get_text())) if n.get("@type")]
        except (json.JSONDecodeError, TypeError):
            types.append("битый JSON")
    micro = [t.get("itemtype", "").rsplit("/", 1)[-1] for t in soup.find_all(attrs={"itemtype": True})][:3]
    og = (soup.find("meta", property="og:type") or {}).get("content")
    price = bool(soup.find("meta", property=re.compile("price", re.I)) or soup.find(attrs={"itemprop": "price"}))
    return (f"ld+json: {sorted(set(types))[:5] or 'нет'}; microdata: {micro or 'нет'}; "
            f"og:type: {og}; цена в разметке: {'да' if price else 'нет'}; {len(html) // 1024} КБ")


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
        result = _microdata(soup)
        if not result:
            # запасной вариант: Open Graph / product meta
            def meta(prop):
                m = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop}) \
                    or soup.find("meta", attrs={"itemprop": prop})
                return m.get("content") if m else None

            price = _num(meta("product:price:amount") or meta("og:price:amount") or meta("price"))
            result = {
                "url": url, "title": meta("og:title"), "author": None, "publisher": None,
                "isbn": None, "price": price, "currency": meta("product:price:currency") or "UAH",
                "in_stock": True, "image": meta("og:image"), "old_price": _num(meta("product:original_price:amount")),
                "description": meta("og:description") or "", "category": "",
            }
        result["url"] = url

    result["year"] = _year(product, soup)
    result["format"] = detect_format(result.get("title"), url, result.get("category"),
                                     str(product.get("bookFormat", "")) if product else "")
    # og:description у магазинов часто рекламный текст, поэтому берём его только если он длинный
    result["description"] = _clean_desc(result.get("description"), 120 if not product else 0)
    if not result.get("image"):
        og = soup.find("meta", property="og:image")
        result["image"] = og.get("content") if og else None
    if result.get("image"):
        result["image"] = urljoin(url, result["image"])
    if not (result.get("author") and result.get("isbn")):  # дозаполняем из характеристик на странице
        chars = _chars(soup)
        for k in ("author", "isbn", "publisher"):
            if not result.get(k) and chars.get(k):
                result[k] = chars[k]
    if not result["title"] or result["price"] is None:
        return None
    return result
