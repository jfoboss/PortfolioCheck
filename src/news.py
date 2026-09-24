"""
news.py — лента новостей по позициям из RSS-источников (у T-Invest API новостей нет).

Источники — обычные RSS деловых СМИ; список редактируется в настройках. Фильтр — по названиям и тикерам
твоих бумаг (плюс алиасы: «Сбер» для Сбербанка и т.п.). Всё best-effort: недоступный источник пропускается.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import net                         # сеть к сайтам — net.fetch (корни ОС)
import style as SY
import jsoncache as JC

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
        ("https://www.bigpowernews.ru/rss.php", "BigpowerNews (электроэнергетика)"),
    ],
    "Промышленность и сырьё": [
        ("https://www.metalinfo.ru/ru/news/rss.xml", "Металлоснабжение и сбыт"),
        ("https://www.agroinvestor.ru/feed/public-agronews.xml", "Агроинвестор"),
    ],
    "Недвижимость и склады": [
        ("https://cre.ru/rss/rss_news.xml", "CRE (коммерческая недвижимость)"),
        ("https://arendator.ru/rss/", "Arendator"),
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
        if items:
            return True, f"{len(items)} новостей"
        head = data[:600].lstrip().lower()
        if not head:
            return False, "пустой ответ"
        if head.startswith((b"<!doctype html", b"<html")):
            # Forbes, Банки.ру и др. отдают JS-проверку «вы не робот» — с домашнего адреса часто проходит, из облака нет
            return False, "вместо ленты — веб-страница (защита от ботов или лента переехала)"
        return False, "нет новостей в ленте"
    except Exception as e:  # noqa: BLE001
        return False, SY.err_text(e, 80)

# Алиасы для поиска: тикер → слова, по которым узнаётся компания в заголовках
DEFAULT_ALIASES = {
    "SBERP": ["Сбер", "Сбербанк", "Греф"], "SIBN": ["Газпром нефть", "Газпромнефть"], "GAZP": ["Газпром"],
    "ROSN": ["Роснефть", "Сечин", "Восток Ойл"], "MDMG": ["Мать и дитя", "МД Медикал", "MD Medical", "Курцер"],
    "PHOR": ["ФосАгро", "Фосагро"], "TATNP": ["Татнефть"], "TRNFP": ["Транснефть"], "SNGSP": ["Сургутнефтегаз"],
    "NVTK": ["Новатэк", "НОВАТЭК"], "DOMRF": ["ДОМ.РФ", "Дом.РФ"], "BELU": ["Novabev", "Белуга", "Винлаб"],
    "FLOT": ["Совкомфлот"], "MTSS": ["МТС"], "MGKL": ["МГКЛ", "Мосгорломбард"], "MOEX": ["Мосбиржа", "Московская биржа"],
    "VKCO": ["VK", "ВКонтакте"], "VTBR": ["ВТБ"], "YDEX": ["Яндекс"], "AGRO": ["Русагро", "РусАгро"],
    "GMKN": ["Норникель", "Норильский никель", "Норильского никеля"],   # имя на прилагательное: «норильского» само не найдётся
    "GLDRUB_TOM": ["золот"], "SLVRUB_TOM": ["серебр"],
    # валюты — только про курс: слово «доллар» из названия цепляло «$11 млрд» и «108 долларов за баррель»
    "USD000UTSTOM": ["курс доллара", "доллара к рублю", "доллар к рублю", "курс рубля к доллару", "USD/RUB"],
    "EUR_RUB__TOM": ["курс евро", "евро к рублю", "EUR/RUB"], "CNYRUB_TOM": ["курс юаня", "юаня к рублю", "CNY/RUB"],
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
    "moex.com": ("МБ", "Мосбиржа — сообщения биржи", "#A04000"),
    "banki.ru": ("Бру", "Банки.ру", "#2471A3"),
    "frankmedia.ru": ("Frank", "Frank Media", "#566573"),
}

# Правила для источников, где заголовок и описание устроены иначе, чем у новостных лент:
#  • smart-lab — блоги: в описании пост целиком (портфель автора, «Человек и пароход»), и любая упомянутая бумага
#    или слово «дивиденд» привязывали пост к портфелю или к «макро». Там — только по заголовку и без «макро».
#  • moex.com — служебные сообщения биржи («О начале торгов…», «Об исключении ценных бумаг…»): сама биржа в них
#    упомянута всегда, это не новость про акцию MOEX. Привязка — только к другим бумагам, без «макро».
SOURCE_RULES = {
    "smart-lab.ru": {"title_only": True, "no_macro": True},
    "moex.com": {"skip": {"MOEX"}, "no_macro": True, "title_only": True,
                 # техника торгов: ценовые коридоры, размещения и выкуп облигаций, допуск к торгам — даже про бумагу
                 # из портфеля («облигации Сбербанк КИБ», «коридор МТС») это не новость для инвестора
                 "drop": re.compile(r"ценового коридора|диапазона оценки|размещени|сбора заявок|условий проведения торгов|"
                                    r"условия проведения торгов|допущенных к торгам|приобретения облигаций|приостановк|"
                                    r"начале торгов|облигаций серии|баз расчета|дестабилизации цен", re.I)},
}
MAX_AGE_DAYS = 7         # старше — не показываем: лента Мосбиржи отдаёт сообщения за пару месяцев


def _rule(source: str) -> dict:
    return next((r for dom, r in SOURCE_RULES.items() if source == dom or source.endswith("." + dom)), {})


def item_text(it) -> str:
    """Текст новости для поиска (в нижнем регистре) с учётом правил источника."""
    return (it.title if _rule(it.source).get("title_only") else f"{it.title} {it.summary}").lower()


def item_refers(it, ticker: str, words) -> bool:
    """Относится ли новость к бумаге — с правилами источника. Общая проверка для ленты, «Идей» и карточки."""
    return ticker not in _rule(it.source).get("skip", ()) and refers(item_text(it), words)


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


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _text(el, name: str) -> str:
    """Текст дочернего элемента по имени без пространства имён: у части лент RSS объявлен xmlns по умолчанию
    (retail.ru — backend.userland.com/rss2), и <item>/<title> приходят как {ns}item — лента выглядела пустой."""
    for ch in el:
        if _local(ch.tag) == name:
            return ch.text or ""
    return ""


def parse_feed(data: bytes, source: str) -> list[NewsItem]:
    """Понимает RSS (<item>) и Atom (<entry>) — часть изданий отдаёт именно Atom."""
    items = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return items
    for it in (e for e in root.iter() if _local(e.tag) == "item"):     # RSS, в т.ч. с пространством имён (retail.ru)
        title = html.unescape(_text(it, "title")).strip()      # «&quot;25&quot;» у Мосбиржи
        link = _text(it, "link").strip()
        desc = html.unescape(re.sub(r"<[^>]+>", " ", _text(it, "description"))).strip()
        pub = _parse_date(_text(it, "pubDate")) or _parse_date(_text(it, "date"))
        if title:
            items.append(NewsItem(title, link, source, pub, [], desc[:300]))
    if items:
        return items
    for e in root.iter(f"{ATOM}entry"):                # Atom
        title = html.unescape(e.findtext(f"{ATOM}title") or "").strip()
        link = ""
        for l in e.findall(f"{ATOM}link"):
            if l.get("rel", "alternate") == "alternate" and l.get("href"):
                link = l.get("href"); break
        summary = e.findtext(f"{ATOM}summary") or e.findtext(f"{ATOM}content") or ""
        desc = html.unescape(re.sub(r"<[^>]+>", " ", summary)).strip()
        pub = _parse_date(e.findtext(f"{ATOM}updated")) or _parse_date(e.findtext(f"{ATOM}published"))
        if title:
            items.append(NewsItem(title, link, source, pub, [], desc[:300]))
    return items


# Слова, которые сами по себе ничего не означают: «Банк Санкт-Петербург» не должен ловить все новости про банки
GENERIC_WORDS = {"банк", "группа", "холдинг", "компания", "корпорация", "концерн", "инвест", "финанс", "капитал",
                 "россия", "российский", "русский", "русская", "первый", "первая", "объединённая", "объединенная",
                 "акции", "фонд", "зпиф", "заблокированные", "интер", "новый", "новая", "центр",
                 "московская", "московский", "национальная", "национальный", "международная", "международный",
                 "федеральная", "федеральный", "северная", "южная", "восточная", "западная", "городская",
                 "технологии", "технология", "золото", "индекс", "облигации", "денежный", "рынок", "глобальный",
                 "компаний", "компании"}

# Спорт — не про бумаги: «золото» чемпионата, «Единая лига ВТБ», «Газпром-Югра». Такие новости не привязываются ни к
# бумагам, ни к макро. Основы ищутся подстрокой в тексте в нижнем регистре.
OFFTOPIC_WORDS = ("матч", "турнир", "чемпионат", "чемпион мира", "чемпион европ", "олимпи", "медал", "спортсмен",
                  "сборная", "сборной", "футбол", "хоккей", "баскетбол", "волейбол", "теннис", "боксер", "боксёр",
                  "по боксу", "единоборств", "биатлон", "фигурист", "кхл", "рпл", "лиги втб", "лига втб", "лигу втб",
                  "полуфинал", "плей-офф", "в финале", "забил", "тренер", "легкоатлет", "гран-при")

# Основы, которые без рыночного контекста значат что угодно: «золото» — медаль, «Золотая маска», «золотой век».
# Совпадение по ним засчитывается, только если рядом есть слово из MARKET_CONTEXT.
# Существительное, а не прилагательное: «золотом», но не «Золотая маска» / «золотой век»
AMBIGUOUS_WORDS = {"золот": re.compile(r"(?<!\w)золот(?:о|а|у|ом|е)(?!\w)"),
                   "серебр": re.compile(r"(?<!\w)серебр(?:о|а|у|ом|е)(?!\w)")}
MARKET_CONTEXT = ("цен", "стоимост", "котиров", "унци", "доллар", "рубл", "бирж", "торг", "рынок", "рынк", "драгмет",
                  "металл", "подорож", "подешев", "дешев", "инвест", "резерв", "слитк", "тонн", "добыч",
                  "золотодобы", "спрос", "etf", "фрс", "ставк")
# с начала слова: иначе «цен» находится в «сцене» (та же «Золотая маска») и «лицензии»
_MARKET_RE = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, MARKET_CONTEXT)) + ")")


def offtopic(text: str) -> bool:
    """Новость не про рынок (спорт) — text уже в нижнем регистре."""
    return any(w in text for w in OFFTOPIC_WORDS)


def build_keywords(positions, aliases: dict[str, list[str]]) -> dict[str, list[str]]:
    """тикер → фразы для поиска: алиасы + осмысленное имя компании + тикер.
    Общие слова («Банк», «Группа») в одиночку не используются — берётся фраза из двух слов."""
    kw: dict[str, list[str]] = {}
    for p in positions:
        explicit = list(aliases.get(p.ticker, []))
        words = list(explicit)
        # есть явные алиасы — имя из названия не нужно: первое слово бывает прилагательным («Московская Биржа»
        # ловила любую «московскую» новость — филармонию, метро, погоду)
        # у фондов (БПИФ/ETF) название — набор общих слов («Технологии», «Золото», «Денежный рынок»): «технологии»
        # в заметке про кино привязывали её к фонду. Для них — только тикер и явные алиасы
        fund = getattr(p, "instrument_type", "") in ("etf", "currency")
        parts = [] if (explicit or fund) else [w for w in re.split(r"[\s\-—(),]+", p.name or "") if w]
        drop = {"пао", "оао", "ао", "ооо", "акции", "обыкновенные", "привилегированные", "им", "зпиф", "бпиф", "пиф"}
        parts = [w for w in parts if w.lower() not in drop]
        if parts:
            first = parts[0]
            if first.lower() in GENERIC_WORDS or len(first) < 4 or _adjective(first):
                # общие слова и прилагательные — вместе с первым значимым: «Банк Санкт», «Группа компаний Самолет»
                # (не «Группа компаний» — это любая заметка про страхование), «Норильский никель» (не «Корпоративный» —
                # «корпоративные облигации»); значимого нет — два первых слова («Интер РАО», «Корпоративный центр»)
                i = next((j for j, w in enumerate(parts)
                          if w.lower() not in GENERIC_WORDS and len(w) >= 4 and not _adjective(w)), None)
                if i is not None or len(parts) >= 2:
                    words.append(" ".join(parts[:i + 1] if i is not None else parts[:2]))
            else:
                words.append(first)
        if p.ticker and p.ticker.isalpha() and len(p.ticker) >= 4:
            words.append(p.ticker)
        # короткие слова из названия — мусор («АО», «ГК»), но явные алиасы («МТС», «ВТБ», «VK») — нет:
        # раньше они отбрасывались, и новости этих компаний к бумагам не привязывались
        kw[p.ticker] = sorted({w for w in words if len(w) >= 4 or w in explicit}, key=str.lower)
    return kw


SHORT_WORD = 6   # короче — целым словом с падежным окончанием: «Сбер», «Сбера», но не «сбережения»; «МТС», «Греф»
CASE_ENDINGS = r"(?:а|я|у|ю|ом|ем|е|ы|и|ов|ам|ами|ах)?"
# Длинные ключи — основа (без последней гласной / ь / й) + падежное окончание существительного, целым словом.
# Раньше — любое продолжение слова: «Магнит» ловил «магнитные бури», «Газпром» — «Газпромбанк», а «Роснефть»,
# «Мосбиржа» не находили «Роснефти», «Мосбиржи» (меняется последняя буква). Прилагательные не склоняются: из
# «корпоративный» получились бы «корпоративные облигации» и «корпоративный кредит».
LONG_ENDINGS = r"(?:а|я|у|ю|о|е|ы|и|ь|й|ом|ем|ой|ей|ью|ов|ев|ам|ям|ами|ями|ах|ях)?"
_ADJ = ("ий", "ый", "ой", "ая", "яя", "ое", "ее", "ые", "ие")
# «Индекс Мосбиржи» — рынок целиком, а не новость про акцию MOEX: такое упоминание биржи не засчитывается
_NOT_COMPANY = re.compile(r"(?<!\w)индекс\w*\s+(?:мосбирж\w*|московской\s+биржи)")


def _adjective(w: str) -> bool:
    return len(w) >= SHORT_WORD and w.lower().endswith(_ADJ)


def _stem(w: str) -> str:
    """Основа длинного ключа (w — в нижнем регистре); прилагательное — как есть."""
    return w[:-1] if w[-1] in "аяоеёыиьйу" and not _adjective(w) else w


def mentions(text: str, w: str) -> bool:
    """Упоминается ли ключ w в тексте (text — уже в нижнем регистре). Всегда целым словом с падежным окончанием:
    «Сбер» внутри «сбережений», «МТС» внутри других слов, «Магнит» в «магнитных» — не упоминание."""
    w = w.lower()
    if w in AMBIGUOUS_WORDS:
        return AMBIGUOUS_WORDS[w].search(text) is not None
    if len(w) >= SHORT_WORD:
        text = _NOT_COMPANY.sub(" ", text)
        ends = "" if _adjective(w) else LONG_ENDINGS
        return re.search(rf"(?<!\w){re.escape(_stem(w))}{ends}(?!\w)", text) is not None
    return re.search(rf"(?<!\w){re.escape(w)}{CASE_ENDINGS}(?!\w)", text) is not None


def refers(text: str, words) -> bool:
    """Относится ли новость (text — в нижнем регистре) к бумаге с ключами words: спорт — никогда, неоднозначная
    основа («золот») — только рядом с рыночным словом. Общая проверка для ленты, «Идей» и карточки."""
    if offtopic(text):
        return False
    for w in words:
        if mentions(text, w) and (w.lower() not in AMBIGUOUS_WORDS or _MARKET_RE.search(text)):
            return True
    return False


def match(items: list[NewsItem], keywords: dict[str, list[str]]) -> list[NewsItem]:
    out = []
    for it in items:
        text = item_text(it)
        drop = _rule(it.source).get("drop")
        if offtopic(text) or (drop is not None and drop.search(it.title)):
            continue
        hits = [t for t, words in keywords.items() if item_refers(it, t, words)]
        if hits or (not _rule(it.source).get("no_macro") and any(k.lower() in text for k in MACRO_KEYWORDS)):
            it.matched = hits
            out.append(it)
    return out


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.astimezone()      # без пояса — считаем местным


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
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    all_items = [it for it in all_items if it.published is None or _aware(it.published) >= cutoff]
    matched = match(all_items, kw)
    seen, uniq = set(), []
    for it in matched:
        key = it.title.lower()[:80]
        if key not in seen:
            seen.add(key); uniq.append(it)
    uniq.sort(key=lambda x: x.published.timestamp() if x.published else 0, reverse=True)
    return uniq, failed


def save_cache(items: list[NewsItem], cache_dir: Path) -> None:
    rows = [{"title": i.title, "link": i.link, "source": i.source, "published": i.published.isoformat() if i.published else None,
             "matched": i.matched, "summary": i.summary} for i in items[:300]]
    JC.write(cache_dir / "news.json", rows)


def load_cache(cache_dir: Path) -> list[NewsItem]:
    """Лента с диска; битый файл — пустая лента (раньше исключение роняло загрузку при старте)."""
    out = []
    for r in JC.read(cache_dir / "news.json", []) or []:
        try:
            pub = datetime.fromisoformat(r["published"]) if r.get("published") else None
            out.append(NewsItem(r["title"], r["link"], r["source"], pub, r.get("matched", []), r.get("summary", "")))
        except (KeyError, TypeError, ValueError):
            continue
    return out
