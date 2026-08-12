import os
import json
import uuid
import logging
import warnings
from pathlib import Path
from typing import List, Dict, Any

import requests
import feedparser
from urllib3.exceptions import InsecureRequestWarning


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

# Сколько новостей брать за один запуск
MAX_NEWS = 5

# Файл для хранения уже опубликованных новостей
PUBLISHED_FILE = Path("published_news.json")

# Лимиты
TELEGRAM_MAX_LENGTH = 4000
GIGACHAT_MAX_TOKENS = 1200

# Таймауты
HTTP_TIMEOUT = 30

# На этапе тестирования оставляем отключение проверки сертификата.
# Позже это можно заменить на нормальную проверку сертификатов.
VERIFY_SSL = False


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

logger = logging.getLogger("newsbot")


# Убираем предупреждение urllib3 при verify=False
warnings.filterwarnings(
    "ignore",
    category=InsecureRequestWarning
)


# ============================================================
# ПРОВЕРКА НАСТРОЕК
# ============================================================

def check_environment() -> None:
    """Проверяем наличие необходимых GitHub Secrets."""

    required = {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID": CHAT_ID,
        "GIGACHAT_CREDENTIALS": GIGACHAT_CREDENTIALS,
    }

    missing = [
        name
        for name, value in required.items()
        if not value
    ]

    if missing:
        raise RuntimeError(
            "Не заданы необходимые переменные окружения: "
            + ", ".join(missing)
        )

    logger.info("Все необходимые секреты найдены.")


# ============================================================
# РАБОТА С ОПУБЛИКОВАННЫМИ НОВОСТЯМИ
# ============================================================

def load_published_news() -> List[str]:
    """Загружает список уже опубликованных новостей."""

    if not PUBLISHED_FILE.exists():
        return []

    try:
        with open(
            PUBLISHED_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)

        if isinstance(data, list):
            return data

        return []

    except Exception as exc:
        logger.warning(
            "Не удалось прочитать %s: %s",
            PUBLISHED_FILE,
            exc,
        )
        return []


def save_published_news(news_ids: List[str]) -> None:
    """Сохраняет список опубликованных новостей."""

    # Оставляем только последние 500 записей,
    # чтобы файл не рос бесконечно.
    news_ids = news_ids[-500:]

    try:
        with open(
            PUBLISHED_FILE,
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                news_ids,
                file,
                ensure_ascii=False,
                indent=2,
            )

        logger.info(
            "Сохранён список опубликованных новостей: %d",
            len(news_ids),
        )

    except Exception as exc:
        logger.error(
            "Ошибка сохранения published_news.json: %s",
            exc,
        )


# ============================================================
# RSS
# ============================================================

def get_rss_news() -> List[Dict[str, Any]]:
    """
    Загружает новости из RSS ТАСС.

    Возвращает список словарей:
    {
        title,
        description,
        link,
        published,
        id
    }
    """

    logger.info("Собираю новости из RSS ТАСС...")

    published_news = load_published_news()
    news_list: List[Dict[str, Any]] = []

    for rss_url in RSS_URLS:

        logger.info("Проверяю RSS: %s", rss_url)

        try:
            response = requests.get(
                rss_url,
                timeout=HTTP_TIMEOUT,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 "
                        "(compatible; NewsBot/1.0)"
                    )
                },
            )

            response.raise_for_status()

            feed = feedparser.parse(response.content)

            if getattr(feed, "bozo", False):
                logger.warning(
                    "RSS содержит ошибки: %s",
                    getattr(
                        feed,
                        "bozo_exception",
                        "неизвестная ошибка"
                    ),
                )

            if not feed.entries:
                logger.warning(
                    "RSS не содержит записей: %s",
                    rss_url,
                )
                continue

            for entry in feed.entries:

                title = (
                    getattr(entry, "title", "")
                    or ""
                ).strip()

                link = (
                    getattr(entry, "link", "")
                    or ""
                ).strip()

                description = (
                    getattr(entry, "summary", "")
                    or getattr(entry, "description", "")
                    or ""
                ).strip()

                published = (
                    getattr(entry, "published", "")
                    or getattr(entry, "updated", "")
                    or ""
                ).strip()

                # Уникальный идентификатор новости
                news_id = (
                    getattr(entry, "id", None)
                    or link
                    or title
                )

                if not title:
                    continue

                if not news_id:
                    continue

                # Не добавляем уже опубликованную новость
                if news_id in published_news:
                    continue

                news_item = {
                    "id": news_id,
                    "title": title,
                    "description": description,
                    "link": link,
                    "published": published,
                }

                news_list.append(news_item)

                if len(news_list) >= MAX_NEWS:
                    break

            if len(news_list) >= MAX_NEWS:
                break

        except requests.RequestException as exc:
            logger.error(
                "Ошибка загрузки RSS %s: %s",
                rss_url,
                exc,
            )

        except Exception as exc:
            logger.error(
                "Ошибка обработки RSS %s: %s",
                rss_url,
                exc,
            )

    logger.info(
        "Получено новых новостей: %d",
        len(news_list),
    )

    return news_list[:MAX_NEWS]


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
        "daily": (
            "temperature_2m_max,"
            "temperature_2m_min"
        ),
        "timezone": WEATHER_TIMEZONE,
    }

    try:
        response = requests.get(
            url,
            params=params,
            timeout=HTTP_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        current_weather = data.get(
            "current_weather",
            {}
        )

        daily = data.get(
            "daily",
            {}
        )

        current_temp = current_weather.get(
            "temperature"
        )

        current_wind = current_weather.get(
            "windspeed"
        )

        max_temperature = daily.get(
            "temperature_2m_max",
            []
        )

        min_temperature = daily.get(
            "temperature_2m_min",
            []
        )

        if (
            current_temp is None
            or current_wind is None
            or len(max_temperature) < 2
            or len(min_temperature) < 2
        ):
            raise ValueError(
                "Open-Meteo вернул неполные данные"
            )

        tomorrow_max = max_temperature[1]
        tomorrow_min = min_temperature[1]

        weather_text = (
            f"Сейчас в Москве: "
            f"{current_temp}°C, "
            f"ветер {current_wind} км/ч.\n"
            f"Завтра: от {tomorrow_min}°C "
            f"до {tomorrow_max}°C."
        )

        logger.info("Погода успешно получена.")

        return weather_text

    except Exception as exc:
        logger.error(
            "Ошибка получения погоды: %s",
            exc,
        )

        return (
            "Не удалось получить данные о погоде."
        )


# ============================================================
# GIGACHAT — ПОЛУЧЕНИЕ ТОКЕНА
# ============================================================

def get_gigachat_token() -> str | None:
    """Получает OAuth-токен GigaChat."""

    logger.info("Получаю токен GigaChat...")

    url = (
        "https://ngw.devices.sberbank.ru:9443"
        "/api/v2/oauth"
    )

    headers = {
        "Content-Type": (
            "application/x-www-form-urlencoded"
        ),
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": (
            f"Basic {GIGACHAT_CREDENTIALS}"
        ),
    }

    data = {
        "scope": "GIGACHAT_API_PERS",
    }

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

        access_token = token_data.get(
            "access_token"
        )

        if not access_token:
            logger.error(
                "GigaChat не вернул access_token."
            )

            logger.error(
                "Ответ сервера: %s",
                response.text,
            )

            return None

        logger.info(
            "Токен GigaChat успешно получен."
        )

        return access_token

    except requests.HTTPError as exc:
        logger.error(
            "HTTP ошибка GigaChat OAuth: %s",
            exc,
        )

        try:
            logger.error(
                "Ответ сервера: %s",
                response.text,
            )
        except Exception:
            pass

        return None

    except Exception as exc:
        logger.error(
            "Ошибка получения токена GigaChat: %s",
            exc,
        )

        return None


# ============================================================
# ПОДГОТОВКА НОВОСТЕЙ ДЛЯ GIGACHAT
# ============================================================

def prepare_news_for_gigachat(
    news: List[Dict[str, Any]]
) -> str:
    """Формирует текст с новостями для GigaChat."""

    if not news:
        return "Новых новостей нет."

    parts = []

    for index, item in enumerate(
        news,
        start=1
    ):
        title = item.get(
            "title",
            ""
        )

        description = item.get(
            "description",
            ""
        )

        published = item.get(
            "published",
            ""
        )

        link = item.get(
            "link",
            ""
        )

        # Ограничиваем описание,
        # чтобы не отправлять слишком большой prompt.
        description = description[:1500]

        block = (
            f"НОВОСТЬ {index}\n"
            f"Заголовок: {title}\n"
            f"Описание: {description}\n"
            f"Дата: {published}\n"
            f"Ссылка: {link}"
        )

        parts.append(block)

    return "\n\n".join(parts)


# ============================================================
# GIGACHAT — ГЕНЕРАЦИЯ ПОСТА
# ============================================================

def process_with_gigachat(
    news: List[Dict[str, Any]],
    weather: str
) -> str:
    """Создаёт готовый пост через GigaChat."""

    logger.info(
        "Передаю данные в GigaChat..."
    )

    access_token = get_gigachat_token()

    if not access_token:

        logger.warning(
            "GigaChat недоступен. "
            "Использую резервный вариант."
        )

        return create_fallback_post(
            news,
            weather,
        )

    news_text = prepare_news_for_gigachat(
        news
    )

    url = (
        "https://api.giga.chat"
        "/v1/chat/completions"
    )

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": (
            f"Bearer {access_token}"
        ),
    }

    prompt = f"""
Ты — редактор новостного Telegram-канала.

Твоя задача — подготовить короткий,
интересный и аккуратно структурированный
новостной пост на русском языке.

Используй только информацию,
которая присутствует в исходных данных.

НИЧЕГО НЕ ВЫДУМЫВАЙ.

Не добавляй:
- собственные факты;
- неподтверждённые сведения;
- оценки, которых нет в исходных данных;
- придуманные цифры;
- придуманные цитаты.

Формат поста:

🌤 <strong>Погода</strong>

Краткая информация о текущей погоде
и прогнозе на завтра.

📰 <strong>Новости</strong>

Для каждой новости:
• короткий и понятный заголовок;
• 1–2 предложения с сутью события.

В конце каждой новости,
если ссылка присутствует в исходных данных,
можно добавить её отдельной строкой.

Текст должен быть пригоден
для публикации в Telegram.

Максимальный объём:
3000 символов.

Не используй Markdown.
Разрешён простой HTML Telegram:
<strong>, <b>, <i>, <em>, <u>, <s>.

ДАННЫЕ О ПОГОДЕ:
{weather}

НОВОСТИ:
{news_text}
""".strip()

    payload = {
        "model": "GigaChat-2",
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "temperature": 0.4,
        "max_tokens": GIGACHAT_MAX_TOKENS,
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            verify=VERIFY_SSL,
            timeout=60,
        )

        response.raise_for_status()

        result = response.json()

        choices = result.get(
            "choices",
            []
        )

        if not choices:
            raise ValueError(
                "GigaChat вернул пустой список choices"
            )

        message = choices[0].get(
            "message",
            {}
        )

        content = message.get(
            "content",
            ""
        )

        if not content:
            raise ValueError(
                "GigaChat не вернул текст"
            )

        content = content.strip()

        logger.info(
            "GigaChat успешно подготовил пост."
        )

        return content

    except Exception as exc:
        logger.error(
            "Ошибка GigaChat: %s",
            exc,
        )

        return create_fallback_post(
            news,
            weather,
        )


# ============================================================
# РЕЗЕРВНЫЙ ПОСТ БЕЗ GIGACHAT
# ============================================================

def create_fallback_post(
    news: List[Dict[str, Any]],
    weather: str
) -> str:
    """
    Формирует простой пост,
    если GigaChat недоступен.
    """

    parts = [
        "🌤 <strong>Погода</strong>",
        weather,
        "",
        "📰 <strong>Новости</strong>",
    ]

    if not news:
        parts.append(
            "Новых новостей нет."
        )
    else:
        for item in news:

            title = item.get(
                "title",
                ""
            )

            link = item.get(
                "link",
                ""
            )

            parts.append(
                f"• {title}"
            )

            if link:
                parts.append(link)

    return "\n".join(parts)


# ============================================================
# ОЧИСТКА TELEGRAM HTML
# ============================================================

def sanitize_telegram_text(
    text: str
) -> str:
    """
    Минимальная очистка текста перед Telegram.

    Сохраняем разрешённые HTML-теги,
    убираем Markdown-кодовые блоки.
    """

    text = text.strip()

    # Убираем случайные тройные backticks
    text = text.replace("```html", "")
    text = text.replace("```", "")

    text = text.strip()

    return text


# ============================================================
# TELEGRAM
# ============================================================

def send_to_telegram(text: str) -> bool:
    """Отправляет пост в Telegram."""

    logger.info(
        "Отправляю сообщение в Telegram..."
    )

    if not TELEGRAM_TOKEN:
        logger.error(
            "TELEGRAM_BOT_TOKEN не задан."
        )
        return False

    if not CHAT_ID:
        logger.error(
            "TELEGRAM_CHAT_ID не задан."
        )
        return False

    text = sanitize_telegram_text(text)

    # Telegram ограничивает sendMessage примерно
    # 4096 символами. Используем запас.
    if len(text) > TELEGRAM_MAX_LENGTH:

        logger.warning(
            "Сообщение слишком длинное: %d символов.",
            len(text),
        )

        text = (
            text[:TELEGRAM_MAX_LENGTH - 50]
            + "\n\n..."
        )

    url = (
        f"https://api.telegram.org"
        f"/bot{TELEGRAM_TOKEN}"
        f"/sendMessage"
    )

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        response = requests.post(
            url,
            data=payload,
            timeout=HTTP_TIMEOUT,
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            logger.error(
                "Telegram вернул ошибку: %s",
                result,
            )
            return False

        logger.info(
            "✅ Сообщение успешно отправлено в Telegram."
        )

        return True

    except requests.HTTPError as exc:

        logger.error(
            "HTTP ошибка Telegram: %s",
            exc,
        )

        try:
            logger.error(
                "Ответ Telegram: %s",
                response.text,
            )
        except Exception:
            pass

        return False

    except Exception as exc:

        logger.error(
            "Ошибка отправки в Telegram: %s",
            exc,
        )

        return False


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    logger.info(
        "=========================================="
    )

    logger.info(
        "        NEWS BOT — START"
    )

    logger.info(
        "=========================================="
    )

    # 1. Проверяем Secrets
    check_environment()

    # 2. Получаем RSS
    news = get_rss_news()

    # 3. Получаем погоду
    weather = get_weather()

    # 4. Если нет новых новостей,
    # всё равно можно опубликовать погоду.
    if news:
        logger.info(
            "Найдено новых новостей: %d",
            len(news),
        )
    else:
        logger.info(
            "Новых новостей не найдено."
        )

    # 5. Генерируем пост
    final_post = process_with_gigachat(
        news,
        weather,
    )

    if not final_post:
        logger.error(
            "Не удалось сформировать пост."
        )
        return

    logger.info(
        "Размер готового поста: %d символов.",
        len(final_post),
    )

    # 6. Отправляем в Telegram
    success = send_to_telegram(
        final_post
    )

    # 7. Только после успешной отправки
    # помечаем новости как опубликованные.
    if success and news:

        published_news = (
            load_published_news()
        )

        for item in news:

            news_id = item.get("id")

            if news_id and news_id not in published_news:
                published_news.append(
                    news_id
                )

        save_published_news(
            published_news
        )

    if success:
        logger.info(
            "=========================================="
        )
        logger.info(
            "        NEWS BOT — SUCCESS"
        )
        logger.info(
            "=========================================="
        )
    else:
        logger.error(
            "=========================================="
        )
        logger.error(
            "        NEWS BOT — FAILED"
        )
        logger.error(
            "=========================================="
        )

        # GitHub Actions должен увидеть ошибку
        raise RuntimeError(
            "Не удалось отправить сообщение в Telegram."
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
