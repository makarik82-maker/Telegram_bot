import os
import requests
import feedparser
from gigachat import GigaChat
from datetime import datetime, timedelta

# 1. Получаем наши секретные ключи из настроек GitHub
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GIGA_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS")
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat")  # Модель по умолчанию

def get_rss_news():
    """Собираем новости из RSS ТАСС"""
    print("Собираю новости...")
    # RSS лента ТАСС - основные новости
    url = "https://tass.ru/rss/v2.xml"
    news_list = []
    try:
        feed = feedparser.parse(url)
        # Берем первые 5 новостей
        for entry in feed.entries[:5]:
            news_list.append(f"- {entry.title}")
    except Exception as e:
        print(f"Ошибка при чтении RSS ТАСС: {e}")
        # Резервный источник - экономика ТАСС
        try:
            feed = feedparser.parse("https://tass.ru/rss/economy.xml")
            for entry in feed.entries[:5]:
                news_list.append(f"- {entry.title}")
        except Exception as e2:
            print(f"Ошибка резервного источника: {e2}")
    return "\n".join(news_list)

def get_weather():
    """Получаем погоду из Open-Meteo (сегодня и завтра)"""
    print("Получаю погоду...")
    # Координаты Москвы
    url = "https://api.open-meteo.com/v1/forecast?latitude=55.75&longitude=37.62&current_weather=true&daily=temperature_2m_max,temperature_2m_min&timezone=Europe%2FMoscow"
    try:
        response = requests.get(url)
        data = response.json()
        
        # Текущая погода
        current_temp = data['current_weather']['temperature']
        current_wind = data['current_weather']['windspeed']
        
        # Прогноз на завтра
        tomorrow_max = data['daily']['temperature_2m_max'][1]
        tomorrow_min = data['daily']['temperature_2m_min'][1]
        
        weather_text = (f"Сейчас в Москве: {current_temp}°C, ветер {current_wind} км/ч.\n"
                       f"Завтра: от {tomorrow_min}°C до {tomorrow_max}°C")
        return weather_text
    except Exception as e:
        print(f"Ошибка погоды: {e}")
        return "Не удалось получить погоду."

def process_with_gigachat(news, weather):
    """Отправляем сырые данные в Gigachat, чтобы она сделала красивый пост"""
    print("Обрабатываю данные в Gigachat...")
    
    try:
        # Инициализируем Gigachat с указанием модели
        ai = GigaChat(
            credentials=GIGA_CREDENTIALS, 
            scope="GIGACHAT_API_PERS", 
            verify_ssl_certs=False,
            model=GIGACHAT_MODEL  # Используем переменную окружения
        )
        
        prompt = f"""
        Ты - редактор новостного Telegram-канала. 
        Я дам тебе сырые данные. Твоя задача - написать из них короткий, интересный и структурированный пост для Telegram.
        Используй эмодзи. Раздели на блоки:  Погода,  Новости (ТАСС).
        Не выдумывай того, чего нет в данных. 
        ВАЖНО: Объем текста строго до 3500 символов (лимит Telegram).
        
        Данные:
        Погода: {weather}
        Новости ТАСС: {news}
        """
        
        response = ai.chat(prompt)
        return response.choices[0].message.content
        
    except Exception as e:
        print(f"Ошибка Gigachat: {e}")
        # Если нейросеть сломалась, отправим хотя бы сырые данные
        return f" Нейросеть занята, вот сырые данные:\n\n {weather}\n\n Новости ТАСС:\n{news}"

def send_to_telegram(text):
    """Отправляем текст в Telegram"""
    print("Отправляю в Telegram...")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    # Telegram не любит сообщения длиннее 4096 символов, обрезаем на всякий случай
    if len(text) > 4000:
        text = text[:3990] + "\n\n...(текст обрезан)"
        
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            print("Успешно отправлено!")
        else:
            print(f"Ошибка Telegram: {response.text}")
    except Exception as e:
        print(f"Ошибка отправки: {e}")

if __name__ == "__main__":
    print("=== Бот запущен ===")
    
    # 1. Собираем данные
    raw_news = get_rss_news()
    raw_weather = get_weather()
    
    # 2. Прогоняем через нейросеть
    final_post = process_with_gigachat(raw_news, raw_weather)
    
    # 3. Публикуем
    send_to_telegram(final_post)
    
    print("=== Бот завершил работу ===")
