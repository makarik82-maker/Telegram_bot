import os
import json
import uuid
import html
import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone

import requests
import feedparser


# ============================================================
# НАСТРОЙКИ
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GIGACHAT_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS")

# Москва
WEATHER_LATITUDE = 55.7558
WEATHER_LONGITUDE = 37.6173
WEATHER_TIMEZONE = "Europe/Moscow"

# RSS ТАСС
RSS_URLS = [
    "https://tass.ru/rss/v2.xml",
    "https://tass.ru/rss/economy.xml",
]

# Получаем больше материалов, чем публикуем.
# Это позволяет отфильтровать и ранжировать новости.
MAX_RSS_ITEMS_PER_FEED = 30
MAX_NEWS_FOR_POST = 5

# Хранилище уже опубликованных новостей
PUBLISHED_FILE = Path("published_news.json")
MAX_PUBLISHED_IDS = 1000

# Ограничения
TELEGRAM_MAX_LENGTH = 4000
GIGACHAT_MAX_TOKENS = 1200
RSS_DESCRIPTION_MAX_LENGTH = 800
GIGACHAT_TIMEOUT = 60
HTTP_TIMEOUT = 30

# ВАЖНО: SSL-проверка включена.
VERIFY_SSL = True

# Ключевые слова для простого приоритетного ранжирования.
# Это НЕ заменяет редактора/AI, а только помогает выбрать материалы.
HIGH_PRIORITY_KEYWORDS = [
    "москва", "россия", "правительство", "президент",
    "госдума", "экономика", "рубль", "цб", "центробанк",
    "инфляция", "нефть", "газ", "санкции", "технологии",
    "безопасность", "происшествие", "чрезвычай", "закон",
]

LOW_PRIORITY_KEYWORDS = [
    "спорт", "футбол", "хоккей", "матч", "турнир",
    "шоу-бизнес", "кино", "музыка",
]

# Telegram разрешает только эти теги в HTML parse_mode.
ALLOWED_TELEGRAM_TAGS = {
    "b", "strong", "i", "em", "u", "s", "del", "ins", "code", "pre", "a"
}


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("newsbot")


# ============================================================
# ПРОВЕРКА НАСТРОЕК
# ============================================================

def check_environment() -> None:
    """Проверяет наличие необходимых GitHub Secrets."""
    required = {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID": CHAT_ID,
        "GIGACHAT_CREDENTIALS": GIGACHAT_CREDENTIALS,
    }

    missing = [name for name, value in required.items() if not value]

    if missing:
        raise RuntimeError(
            "Не заданы необходимые переменные окружения: "
            + ", ".join(missing)
        )

    logger.info("Все необходимые Secrets найдены.")


# ============================================================
# PUBLISHED NEWS
# ============================================================

def load_published_news() -> List[str]:
    """Загружает список опубликованных ID."""
    if not PUBLISHED_FILE.exists():
        return []

    try:
        with PUBLISHED_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, list):
            return [str(item) for item in data if item]

        logger.warning("Формат %s не является списком.", PUBLISHED_FILE)
        return []

    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Не удалось прочитать %s: %s", PUBLISHED_FILE, exc)
        return []


def save_published_news(news_ids: List[str]) -> None:
    """Безопасно сохраняет последние опубликованные ID."""
    unique_ids = list(dict.fromkeys(str(item) for item in news_ids if item))
    unique_ids = unique_ids[-MAX_PUBLISHED_IDS:]

    tmp_file = PUBLISHED_FILE.with_suffix(".tmp")

    try:
        with tmp_file.open("w", encoding="utf-8") as file:
            json.dump(unique_ids, file, ensure_ascii=False, indent=2)

        tmp_file.replace(PUBLISHED_FILE)

        logger.info(
            "Сохранён список опубликованных новостей: %d",
            len(unique_ids),
        )

    except OSError as exc:
        logger.error("Ошибка сохранения %s: %s", PUBLISHED_FILE, exc)
        try:
            if tmp_file.exists():
                tmp_file.unlink()
        except OSError:
            pass


# ============================================================
# TEXT / RSS NORMALIZATION
# ============================================================

def strip_html_tags(text: str) -> str:
    """Удаляет HTML-теги из RSS-описания."""
    if not text:
        return ""

    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_text(text: str) -> str:
    """Нормализует пробелы и HTML-сущности."""
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def normalize_url(url: str) -> str:
    """Минимально нормализует URL для сравнения дублей."""
    url = (url or "").strip()
    if not url:
        return ""
    return url.rstrip("/")


def make_title_key(title: str) -> str:
    """Создаёт ключ заголовка для дополнительной проверки дублей."""
    title = normalize_text(title).lower()
    title = re.sub(r"[^\w\sа-яё]", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title)
    return title.strip()


def truncate_text(text: str, max_length: int) -> str:
    """Обрезает текст по границе слова."""
    text = normalize_text(text)
    if len(text) <= max_length:
        return text

    shortened = text[:max_length].rsplit(" ", 1)[0].strip()
    return shortened + "…"


def parse_entry_datetime(entry: Any) -> Optional[datetime]:
    """Извлекает дату RSS и приводит её к UTC."""
    parsed = (
        getattr(entry, "published_parsed", None)
        or getattr(entry, "updated_parsed", None)
    )

    if parsed:
        try:
            from calendar import timegm
            return datetime.fromtimestamp(timegm(parsed), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            pass

    raw = (
        getattr(entry, "published", "")
        or getattr(entry, "updated", "")
        or ""
    ).strip()

    if raw:
        try:
            value = raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass

    return None


# ============================================================
# RSS
# ============================================================

def get_rss_news() -> List[Dict[str, Any]]:
    """ Получает новости из RSS, нормализует их, удаляет дубли, сортирует по дате и возвращает кандидатов для дальнейшего отбора. """
    logger.info("Собираю новости из RSS ТАСС...")

    published_ids = set(load_published_news())
    candidates: List[Dict[str, Any]] = []

    stats = {
        "feeds": 0,
        "raw": 0,
        "already_published": 0,
        "duplicates": 0,
        "accepted": 0,
    }

    seen_ids = set()
    seen_urls = set()
    seen_titles = set()

    for rss_url in RSS_URLS:
        stats["feeds"] += 1
        logger.info("Проверяю RSS: %s", rss_url)

        try:
            response = requests.get(
                rss_url,
                timeout=HTTP_TIMEOUT,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; NewsBot/2.0)"
                },
                verify=VERIFY_SSL,
            )
            response.raise_for_status()

            feed = feedparser.parse(response.content)

            if getattr(feed, "bozo", False):
                logger.warning(
                    "RSS содержит ошибки: %s",
                    getattr(feed, "bozo_exception", "неизвестная ошибка"),
                )

            entries = list(feed.entries)
            stats["raw"] += len(entries)

            if not entries:
                logger.warning("RSS не содержит записей: %s", rss_url)
                continue

            # Сначала сортируем весь RSS по дате, а затем ограничиваем выборку.
            entries.sort(
                key=lambda entry: (
                    parse_entry_datetime(entry)
                    or datetime.min.replace(tzinfo=timezone.utc)
                ),
                reverse=True,
            )

            for entry in entries[:MAX_RSS_ITEMS_PER_FEED]:
                title = normalize_text(getattr(entry, "title", "") or "")
                link = normalize_url(getattr(entry, "link", "") or "")
                description = strip_html_tags(
                    getattr(entry, "summary", "")
                    or getattr(entry, "description", "")
                    or ""
                )
                description = truncate_text(
                    description, RSS_DESCRIPTION_MAX_LENGTH
                )

                published_raw = normalize_text(
                    getattr(entry, "published", "")
                    or getattr(entry, "updated", "")
                    or ""
                )

                entry_date = parse_entry_datetime(entry)
                published_iso = (
                    entry_date.isoformat() if entry_date else published_raw
                )

                entry_id = normalize_text(
                    getattr(entry, "id", "") or ""
                )

                # Приоритет идентификаторов:
                # RSS id -> URL -> заголовок.
                news_id = entry_id or link or make_title_key(title)

                if not title or not news_id:
                    continue

                title_key = make_title_key(title)

                if news_id in published_ids:
                    stats["already_published"] += 1
                    continue

                # Тройная защита от дублей:
                # ID + URL + нормализованный заголовок.
                if news_id in seen_ids:
                    stats["duplicates"] += 1
                    continue

                if link and link in seen_urls:
                    stats["duplicates"] += 1
                    continue

                if title_key and title_key in seen_titles:
                    stats["duplicates"] += 1
                    continue

                item = {
                    "id": news_id,
                    "title": title,
                    "description": description,
                    "link": link,
                    "published": published_raw,
                    "published_iso": published_iso,
                    "source": "ТАСС",
                }

                candidates.append(item)
                seen_ids.add(news_id)
                if link:
                    seen_urls.add(link)
                if title_key:
                    seen_titles.add(title_key)

                stats["accepted"] += 1

        except requests.RequestException as exc:
            logger.error("Ошибка загрузки RSS %s: %s", rss_url, exc)

        except Exception as exc:
            logger.exception("Ошибка обработки RSS %s: %s", rss_url, exc)

    candidates.sort(
        key=lambda item: item.get("published_iso", ""),
        reverse=True,
    )

    logger.info(
        "RSS: источников=%d, записей=%d, уже опубликовано=%d, "
        "дублей=%d, принято=%d",
        stats["feeds"],
        stats["raw"],
        stats["already_published"],
        stats["duplicates"],
        stats["accepted"],
    )

    return candidates


# ============================================================
# NEWS RANKING
# ============================================================

def calculate_news_score(news: Dict[str, Any]) -> int:
    """ Рассчитывает простой редакционный приоритет. Более свежие и тематически важные новости получают больший балл. """
    title = normalize_text(news.get("title", "")).lower()
    description = normalize_text(news.get("description", "")).lower()
    text = f"{title} {description}"

    score = 0

    for keyword in HIGH_PRIORITY_KEYWORDS:
        if keyword in text:
            score += 3

    for keyword in LOW_PRIORITY_KEYWORDS:
        if keyword in text:
            score -= 2

    # Наличие описания и ссылки повышает качество исходного материала.
    if description:
        score += 1
    if news.get("link"):
        score += 1

    # Более свежие материалы получают небольшой бонус.
    try:
        published = datetime.fromisoformat(
            news.get("published_iso", "")
        )
        age_hours = max(
            0,
            (datetime.now(timezone.utc) - published).total_seconds() / 3600,
        )
        score += max(0, int(24 - age_hours) // 6)
    except (ValueError, TypeError):
        pass

    return score


def select_news_for_post( candidates: List[Dict[str, Any]], ) -> List[Dict[str, Any]]:
    """Ранжирует кандидатов и выбирает материалы для поста."""
    if not candidates:
        logger.info("Для публикации нет новостей.")
        return []

    ranked = []
    for item in candidates:
        enriched = dict(item)
        enriched["score"] = calculate_news_score(item)
        ranked.append(enriched)

    ranked.sort(
        key=lambda item: (
            item.get("score", 0),
            item.get("published_iso", ""),
        ),
        reverse=True,
    )

    selected = ranked[:MAX_NEWS_FOR_POST]

    logger.info(
        "Ранжирование: кандидатов=%d, выбрано=%d",
        len(candidates),
        len(selected),
    )

    for index, item in enumerate(selected, start=1):
        logger.info(
            "Новость #%d: score=%d | %s",
            index,
            item.get("score", 0),
            truncate_text(item.get("title", ""), 120),
        )

    return selected


# ============================================================
# ПОГОДА
# ============================================================

def get_weather() -> str:
    """Получает текущую погоду и прогноз на завтра."""
    logger.info("Получаю погоду из Open-Meteo...")

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": WEATHER_LATITUDE,
        "longitude": WEATHER_LONGITUDE,
        "current_weather": "true",
        "daily": "temperature_2m_max,temperature_2m_min",
        "timezone": WEATHER_TIMEZONE,
    }

    try:
        response = requests.get(
            url,
            params=params,
            timeout=HTTP_TIMEOUT,
            verify=VERIFY_SSL,
        )
        response.raise_for_status()

        data = response.json()
        current_weather = data.get("current_weather") or {}
        daily = data.get("daily") or {}

        current_temp = current_weather.get("temperature")
        current_wind = current_weather.get("windspeed")
        max_temperature = daily.get("temperature_2m_max") or []
        min_temperature = daily.get("temperature_2m_min") or []

        if (
            current_temp is None
            or current_wind is None
            or len(max_temperature) < 2
            or len(min_temperature) < 2
        ):
            raise ValueError("Open-Meteo вернул неполные данные")

        weather_text = (
            f"Сейчас в Москве: {current_temp}°C, "
            f"ветер {current_wind} км/ч. "
            f"Завтра: от {min_temperature[1]}°C "
            f"до {max_temperature[1]}°C."
        )

        logger.info("Погода успешно получена.")
        return weather_text

    except Exception as exc:
        logger.error("Ошибка получения погоды: %s", exc)
        return "Данные о погоде временно недоступны."


# ============================================================
# GIGACHAT — TOKEN
# ============================================================

def get_gigachat_token() -> Optional[str]:
    """Получает OAuth-токен GigaChat."""
    logger.info("Получаю токен GigaChat...")

    url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": f"Basic {GIGACHAT_CREDENTIALS}",
    }

    data = {"scope": "GIGACHAT_API_PERS"}

    try:
        response = requests.post(
            url,
            headers=headers,
            data=data,
            verify=VERIFY_SSL,
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()

        token_data = response.json()
        access_token = token_data.get("access_token")

        if not access_token:
            logger.error("GigaChat не вернул access_token.")
            logger.error("Ответ сервера: %s", response.text[:1000])
            return None

        logger.info("Токен GigaChat успешно получен.")
        return access_token

    except requests.HTTPError as exc:
        logger.error("HTTP ошибка GigaChat OAuth: %s", exc)
        try:
            logger.error("Ответ сервера: %s", response.text[:1000])
        except Exception:
            pass
        return None

    except Exception as exc:
        logger.error("Ошибка получения токена GigaChat: %s", exc)
        return None


# ============================================================
# GIGACHAT — INPUT
# ============================================================

def prepare_news_for_gigachat( news: List[Dict[str, Any]], ) -> str:
    """Формирует компактные исходные данные для AI."""
    if not news:
        return "Новых новостей нет."

    parts = []

    for index, item in enumerate(news, start=1):
        block = (
            f"НОВОСТЬ {index}\n"
            f"ID: {item.get('id', '')}\n"
            f"Заголовок: {item.get('title', '')}\n"
            f"Описание: {item.get('description', '')}\n"
            f"Дата: {item.get('published', '')}\n"
            f"Ссылка: {item.get('link', '')}"
        )
        parts.append(block)

    return "\n\n".join(parts)


# ============================================================
# GIGACHAT — ANALYSIS / SUMMARY
# ============================================================

def process_with_gigachat( news: List[Dict[str, Any]], ) -> List[Dict[str, str]]:
    """ AI занимается только редакционной обработкой: выбирает/сжимает информацию, но НЕ формирует Telegram HTML. """
    if not news:
        return []

    logger.info(
        "Передаю в GigaChat %d новостей для редакционной обработки...",
        len(news),
    )

    access_token = get_gigachat_token()

    if not access_token:
        logger.warning(
            "GigaChat недоступен. Использую исходные заголовки и описания."
        )
        return create_fallback_articles(news)

    news_text = prepare_news_for_gigachat(news)

    prompt = f""" Ты — новостной редактор Telegram-канала. Обработай ПЕРЕДАННЫЕ НИЖЕ новости. КРИТИЧЕСКИЕ ПРАВИЛА: 1. Ничего не выдумывай. 2. Используй только факты из исходных данных. 3. Не добавляй собственные оценки. 4. Не добавляй причины, последствия или детали, которых нет в источнике. 5. Не изменяй цифры, имена и названия. 6. Не придумывай цитаты. 7. Не создавай новые ссылки. 8. Для каждой новости дай короткий заголовок и краткое резюме в 1–2 предложениях. 9. Верни результат СТРОГО в JSON-массиве. 10. Каждый элемент должен содержать поля: id, title, summary, link 11. ID и link должны быть взяты из исходных данных без изменения. 12. Не добавляй Markdown, HTML или пояснения до/после JSON. Пример структуры: [ {{ "id": "исходный_id", "title": "Короткий заголовок", "summary": "Краткое резюме.", "link": "исходная_ссылка" }} ] ИСХОДНЫЕ НОВОСТИ: {news_text} """.strip()

    url = "https://api.giga.chat/v1/chat/completions"

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    payload = {
        "model": "GigaChat-2",
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "temperature": 0.1,
        "max_tokens": GIGACHAT_MAX_TOKENS,
    }

    started = datetime.
