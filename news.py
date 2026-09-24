"""
news.py — лента новостей по позициям из RSS-источников (у T-Invest API новостей нет).

Источники — обычные RSS деловых СМИ; список редактируется в настройках. Фильтр — по названиям и тикерам
твоих бумаг (плюс алиасы: «Сбер» для Сбербанка и т.п.). Всё best-effort: недоступный источник пропускается.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import net                         # сеть к сайтам — net.fetch (корни ОС)

DEFAULT_FEEDS = [
    "https://www.interfax.ru/rss.asp",
    "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",
    "https://www.kommersant.ru/RSS/news.xml",
    "http://www.finmarket.ru/rss/mainnews.asp",
    "https://smart-lab.ru/rss/",
    "https://www.vedomosti.ru/rss/news",
]

# Каталог источников для кнопки «Добавить источники…» в настройках: категория → [(адрес, название)].
# Адреса могут меняться — кнопка «Проверить» в настройках показывает, какие реально отвечают.
FEED_CATALOG = {
    "Деловые СМИ": [
        ("https://www.interfax.ru/rss.asp", "Интерфакс"),
        ("https://rssexport.rbc.ru/rbcnews/news/30/full.rss", "РБК"),
        ("https://www.kommersant.ru/RSS/news.xml", "Коммерсантъ"),
        ("https://www.vedomosti.ru/rss/news", "Ведомости"),
        ("https://1prime.ru/export/rss2/index.xml", "Прайм"),
        ("https://tass.ru/rss/v2.xml", "ТАСС"),
        ("https://www.forbes.ru/newrss.xml", "Forbes Россия"),
        ("https://lenta.ru/rss/news/economics", "Лента.ру — экономика"),
    ],
    "Рынок и инвестиции": [
        ("http://www.finmarket.ru/rss/mainnews.asp", "Финмаркет"),
        ("https://smart-lab.ru/rss/", "smart-lab"),
        ("https://www.finam.ru/net/analysis/conews/rsspoint", "Финам"),
        ("https://bcs-express.ru/rss", "БКС Экспресс"),
        ("https://quote.rbc.ru/rss", "РБК Инвестиции"),
        ("https://www.moex.com/export/news.aspx?cat=100", "Мосбиржа"),
    ],
    "Банки и финансы": [
        ("https://www.banki.ru/xml/news.rss", "Банки.ру"),
        ("https://frankmedia.ru/feed", "Frank Media"),
    ],
    "Нефть, газ, энергетика": [
        ("https://neftianka.ru/feed/", "Нефтянка"),
        ("https://oilcapital.ru/rss", "Нефть и Капитал"),
        ("https://peretok.ru/rss/", "Переток.ру (электроэнергетика)"),
        ("https://www.bigpowernews.ru/rss/", "BigpowerNews (электроэнергетика)"),
        ("https://www.eprussia.ru/rss/", "Энергетика и промышленность"),
    ],
    "Промышленность и сырьё": [
        ("https://www.metalinfo.ru/ru/news/rss.xml", "Металлоснабжение и сбыт"),
        ("https://www.agroinvestor.ru/rss/", "Агроинвестор"),
    ],
    "Недвижимость и склады": [
        ("https://cre.ru/rss", "CRE (коммерческая недвижимость)"),
        ("https://arendator.ru/rss/", "Arendator"),
        ("https://www.rbc.ru/v10/ajax/get-news-feed/project/realty/lastDate/rss", "РБК Недвижимость"),
    ],
    "Потребительский сектор": [
        ("https://www.retail.ru/rss/news/", "Retail.ru"),
    ],
}


def check_feed(url: str, ca_bundle: Path | None = None) -> tuple[bool, str]:
    """(доступен, описание) — для кнопки «Проверить» в настройках."""
    try:
        data = net.fetch(url, timeout=10)
        items = parse_feed(data, url)
        return (True, f"{len(items)} новостей") if items else (False, "нет элементов <item>")
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:80]}"

# Алиасы для поиска: тикер → слова, по которым узнаётся компания в заголовках
DEFAULT_ALIASES = {
    "SBERP": ["Сбер", "Сбербанк", "Греф"], "SIBN": ["Газпром нефть", "Газпромнефть"], "GAZP": ["Газпром"],
    "ROSN": ["Роснефть", "Сечин", "Восток Ойл"], "MDMG": ["Мать и дитя", "МД Медикал", "MD Medical", "Курцер"],
    "PHOR": ["ФосАгро", "Фосагро"], "TATNP": ["Татнефть"], "TRNFP": ["Транснефть"], "SNGSP": ["Сургутнефтегаз"],
    "NVTK": ["Новатэк", "НОВАТЭК"], "DOMRF": ["ДОМ.РФ", "Дом.РФ"], "BELU": ["Novabev", "Белуга", "Винлаб"],
    "FLOT": ["Совкомфлот"], "MTSS": ["МТС"], "MGKL": ["МГКЛ", "Мосгорломбард"], "MOEX": ["Мосбиржа", "Московская биржа"],
    "VKCO": ["VK", "ВКонтакте"], "VTBR": ["ВТБ"], "YDEX": ["Яндекс"], "AGRO": ["Русагро", "РусАгро"],
    "GLDRUB_TOM": ["золот"], "SLVRUB_TOM": ["серебр"],
}
# Короткие метки источников (для узкой колонки) и их цвета
SOURCE_BADGES = {
    "interfax.ru": ("ИФ", "Интерфакс", "#1F77B4"),
    "rssexport.rbc.ru": ("РБК", "РБК", "#2E7D32"),
    "rbc.ru": ("РБК", "РБК", "#2E7D32"),
    "kommersant.ru": ("Ъ", "Коммерсантъ", "#8E44AD"),
    "finmarket.ru": ("ФМ", "Финмаркет", "#D68910"),
    "smart-lab.ru": ("СЛ", "smart-lab", "#C0392B"),
    "vedomosti.ru": ("Вед", "Ведомости", "#16A085"),
}


def source_badge(source: str) -> tuple[str, str, str]:
    """(короткая метка, полное имя, цвет) — для неизвестных источников: первые буквы домена."""
    if source in SOURCE_BADGES:
        return SOURCE_BADGES[source]
    short = source.split(".")[0][:4].upper()
    return short, source, "#7F8C8D"


MACRO_KEYWORDS = ["ключевая ставка", "ЦБ РФ", "Набиуллина", "инфляци", "курс рубля", "Мосбиржа", "дивиденд"]


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    published: datetime | None
    matched: list[str]      # тикеры, к которым относится
    summary: str = ""


ATOM = "{http://www.w3.org/2005/Atom}"


def _parse_date(v: str | None):
    if not v:
        return None
    try:
        return parsedate_to_datetime(v)
    except Exception:  # noqa: BLE001
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            return None


def parse_feed(data: bytes, source: str) -> list[NewsItem]:
    """Понимает RSS (<item>) и Atom (<entry>) — часть изданий отдаёт именно Atom."""
    items = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return items
    for it in root.iter("item"):                       # RSS
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "").strip()
        pub = _parse_date(it.findtext("pubDate")) or _parse_date(it.findtext("{http://purl.org/dc/elements/1.1/}date"))
        if title:
            items.append(NewsItem(title, link, source, pub, [], desc[:300]))
    if items:
        return items
    for e in root.iter(f"{ATOM}entry"):                # Atom
        title = (e.findtext(f"{ATOM}title") or "").strip()
        link = ""
        for l in e.findall(f"{ATOM}link"):
            if l.get("rel", "alternate") == "alternate" and l.get("href"):
                link = l.get("href"); break
        summary = e.findtext(f"{ATOM}summary") or e.findtext(f"{ATOM}content") or ""
        desc = re.sub(r"<[^>]+>", " ", summary).strip()
        pub = _parse_date(e.findtext(f"{ATOM}updated")) or _parse_date(e.findtext(f"{ATOM}published"))
        if title:
            items.append(NewsItem(title, link, source, pub, [], desc[:300]))
    return items


# Слова, которые сами по себе ничего не означают: «Банк Санкт-Петербург» не должен ловить все новости про банки
GENERIC_WORDS = {"банк", "группа", "холдинг", "компания", "корпорация", "концерн", "инвест", "финанс", "капитал",
                 "россия", "российский", "русский", "русская", "первый", "первая", "объединённая", "объединенная",
                 "акции", "фонд", "зпиф", "заблокированные", "интер", "новый", "новая", "центр"}


def build_keywords(positions, aliases: dict[str, list[str]]) -> dict[str, list[str]]:
    """тикер → фразы для поиска: алиасы + осмысленное имя компании + тикер.
    Общие слова («Банк», «Группа») в одиночку не используются — берётся фраза из двух слов."""
    kw: dict[str, list[str]] = {}
    for p in positions:
        explicit = list(aliases.get(p.ticker, []))
        words = list(explicit)
        parts = [w for w in re.split(r"[\s\-—(),]+", p.name or "") if w]
        drop = {"пао", "оао", "ао", "ооо", "акции", "обыкновенные", "привилегированные", "им", "зпиф", "бпиф", "пиф"}
        parts = [w for w in parts if w.lower() not in drop]
        if parts:
            first = parts[0]
            if first.lower() in GENERIC_WORDS or len(first) < 4:
                if len(parts) >= 2:
                    words.append(f"{first} {parts[1]}")          # «Банк Санкт-Петербург», «Интер РАО»
            else:
                words.append(first)
        if p.ticker and p.ticker.isalpha() and len(p.ticker) >= 4:
            words.append(p.ticker)
        # короткие слова из названия — мусор («АО», «ГК»), но явные алиасы («МТС», «ВТБ», «VK») — нет:
        # раньше они отбрасывались, и новости этих компаний к бумагам не привязывались
        kw[p.ticker] = sorted({w for w in words if len(w) >= 4 or w in explicit}, key=str.lower)
    return kw


SHORT_WORD = 4   # короче — ищем только целым словом: «мтс» не должно находиться внутри других слов


def mentions(text: str, w: str) -> bool:
    """Упоминается ли ключ w в тексте (text — уже в нижнем регистре)."""
    w = w.lower()
    if len(w) >= SHORT_WORD:
        return w in text                       # длинные — подстрокой: алиасы-основы вроде «золот» так и задуманы
    return re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text) is not None


def match(items: list[NewsItem], keywords: dict[str, list[str]]) -> list[NewsItem]:
    out = []
    for it in items:
        text = f"{it.title} {it.summary}".lower()
        hits = [t for t, words in keywords.items() if any(mentions(text, w) for w in words)]
        if hits or any(k.lower() in text for k in MACRO_KEYWORDS):
            it.matched = hits
            out.append(it)
    return out


def fetch_news(feeds: list[str], positions, aliases: dict[str, list[str]], ca_bundle: Path | None = None) -> tuple[list[NewsItem], list[str]]:
    """Возвращает (новости по позициям и макро, список недоступных источников)."""
    all_items, failed = [], []
    for url in feeds:
        try:
            src = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
            all_items += parse_feed(net.fetch(url), src)
        except Exception:  # noqa: BLE001
            failed.append(url)
    kw = build_keywords(positions, {**DEFAULT_ALIASES, **aliases})
    matched = match(all_items, kw)
    seen, uniq = set(), []
    for it in matched:
        key = it.title.lower()[:80]
        if key not in seen:
            seen.add(key); uniq.append(it)
    uniq.sort(key=lambda x: x.published.timestamp() if x.published else 0, reverse=True)
    return uniq, failed


def save_cache(items: list[NewsItem], cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows = [{"title": i.title, "link": i.link, "source": i.source, "published": i.published.isoformat() if i.published else None,
             "matched": i.matched, "summary": i.summary} for i in items[:300]]
    (cache_dir / "news.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def load_cache(cache_dir: Path) -> list[NewsItem]:
    f = cache_dir / "news.json"
    if not f.exists():
        return []
    out = []
    for r in json.loads(f.read_text(encoding="utf-8")):
        pub = datetime.fromisoformat(r["published"]) if r.get("published") else None
        out.append(NewsItem(r["title"], r["link"], r["source"], pub, r.get("matched", []), r.get("summary", "")))
    return out
