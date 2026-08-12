import os
import requests
import feedparser
import json
from datetime import datetime, timedelta

# 1. Получаем наши секретные ключи из настроек GitHub
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GIGA_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS")

def get_rss_news():
    """Собираем новости из RSS ТАСС"""
    print("Собираю новости...")
    url = "https://tass.ru/rss/v2.xml"
    news_list = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:5]:
            news_list.append(f"- {entry.title}")
    except Exception as e:
        print(f"Ошибка при чтении RSS ТАСС: {e}")
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
    url = "https://api.open-meteo.com/v1/forecast?latitude=55.75&longitude=37.62&current_weather=true&daily=temperature_2m_max,temperature_2m_min&timezone=Europe%2FMoscow"
    try:
        response = requests.get(url)
        data = response.json()
        
        current_temp = data['current_weather']['temperature']
        current_wind = data['current_weather']['windspeed']
        tomorrow_max = data['daily']['temperature_2m_max'][1]
        tomorrow_min = data['daily']['temperature_2m_min'][1]
        
        weather_text = (f"Сейчас в Москве: {current_temp}°C, ветер {current_wind} км/ч.\n"
                       f"Завтра: от {tomorrow_min}°C до {tomorrow_max}°C")
        return weather_text
    except Exception as e:
        print(f"Ошибка погоды: {e}")
        return "Не удалось получить погоду."

def get_gigachat_token():
    """Получаем токен доступа к GigaChat API"""
    print("Получаю токен GigaChat...")
    url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json'
    }
    
    data = {
        'scope': 'GIGACHAT_API_PERS',
    }
    
    try:
        response = requests.post(
            url, 
            headers=headers, 
            data=data, 
            auth=(GIGA_CREDENTIALS, GIGA_CREDENTIALS),
            verify=False
        )
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get('access_token')
        print(f"✅ Токен получен успешно")
        return access_token
    except Exception as e:
        print(f"❌ Ошибка получения токена: {e}")
        return None

def process_with_gigachat(news, weather):
    """Отправляем сырые данные в Gigachat через прямой HTTP запрос"""
    print("Обрабатываю данные в Gigachat...")
    
    # Шаг 1: Получаем токен
    access_token = get_gigachat_token()
    if not access_token:
        return f"🤖 Ошибка авторизации в GigaChat. Вот сырые данные:\n\n {weather}\n\n📰 Новости ТАСС:\n{news}"
    
    # Шаг 2: Отправляем запрос к Chat API
    url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
    
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }
    
    prompt = f"""Ты - редактор новостного Telegram-канала. 
Я дам тебе сырые данные. Твоя задача - написать из них короткий, интересный и структурированный пост для Telegram.
Используй эмодзи. Раздели на блоки: 🌤 Погода, 📰 Новости (ТАСС).
Не выдумывай того, чего нет в данных. 
ВАЖНО: Объем текста строго до 3500 символов (лимит Telegram).

Данные:
Погода: {weather}
Новости ТАСС: {news}"""
    
    payload = {
        "model": "GigaChat",
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.7,
        "max_tokens": 1500
    }
    
    try:
        response = requests.post(
            url, 
            headers=headers, 
            json=payload,
            verify=False
        )
        response.raise_for_status()
        result = response.json()
        message_content = result['choices'][0]['message']['content']
        print("✅ GigaChat успешно обработал данные")
        return message_content
        
    except Exception as e:
        print(f" Ошибка Gigachat: {e}")
        return f"🤖 Нейросеть временно недоступна, вот сырые данные:\n\n🌤 {weather}\n\n📰 Новости ТАСС:\n{news}"

def send_to_telegram(text):
    """Отправляем текст в Telegram"""
    print("Отправляю в Telegram...")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
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
            print("✅ Успешно отправлено!")
        else:
            print(f"❌ Ошибка Telegram: {response.text}")
    except Exception as e:
        print(f" Ошибка отправки: {e}")

if __name__ == "__main__":
    print("=== Бот запущен ===")
    
    raw_news = get_rss_news()
    raw_weather = get_weather()
    
    final_post = process_with_gigachat(raw_news, raw_weather)
    
    send_to_telegram(final_post)
    
    print("=== Бот завершил работу ===")
