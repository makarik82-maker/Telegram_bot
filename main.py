import os
import requests
import feedparser
import yfinance as yf
from gigachat import GigaChat
from datetime import datetime

# 1. Получаем наши секретные ключи из настроек GitHub
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GIGA_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS")

def get_rss_news():
    """Собираем новости из RSS"""
    print("Собираю новости...")
    # Возьмем новости с Хабра и РБК (технологии и бизнес)
    urls = [
        "https://habr.com/ru/rss/articles/?fl=ru",
        "https://rssexport.rbc.ru/rbcnews/news/20/full.rss"
    ]
    news_list = []
    for url in urls:
        try:
            feed = feedparser.parse(url)
            # Берем первые 3 новости из каждого источника
            for entry in feed.entries[:3]:
                news_list.append(f"- {entry.title}")
        except Exception as e:
            print(f"Ошибка при чтении RSS {url}: {e}")
    return "\n".join(news_list)

def get_weather():
    """Получаем погоду из Open-Meteo (бесплатно, без ключей)"""
    print("Получаю погоду...")
    # Координаты Москвы
    url = "https://api.open-meteo.com/v1/forecast?latitude=55.75&longitude=37.62&current_weather=true"
    try:
        response = requests.get(url)
        data = response.json()
        temp = data['current_weather']['temperature']
        wind = data['current_weather']['windspeed']
        return f"В Москве сейчас {temp}°C, ветер {wind} км/ч."
    except Exception as e:
        print(f"Ошибка погоды: {e}")
        return "Не удалось получить погоду."

def get_stocks():
    """Получаем данные по акциям через yfinance"""
    print("Получаю данные биржи...")
    # Возьмем Apple и Bitcoin для примера
    tickers = ["AAPL", "BTC-USD"]
    stock_info = []
    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            # Получаем последнюю цену
            hist = stock.history(period="1d")
            if not hist.empty:
                price = hist['Close'].iloc[-1]
                stock_info.append(f"- {ticker}: ${price:.2f}")
        except Exception as e:
            print(f"Ошибка биржи {ticker}: {e}")
    return "\n".join(stock_info)

def process_with_gigachat(news, weather, stocks):
    """Отправляем сырые данные в Gigachat, чтобы она сделала красивый пост"""
    print("Обрабатываю данные в Gigachat...")
    
    # Инициализируем Gigachat. scope="GIGACHAT_API_PERS" для физлиц/бесплатного доступа
    ai = GigaChat(credentials=GIGA_CREDENTIALS, scope="GIGACHAT_API_PERS", verify_ssl_certs=False)
    
    prompt = f"""
    Ты - редактор новостного Telegram-канала. 
    Я дам тебе сырые данные. Твоя задача - написать из них короткий, интересный и структурированный пост для Telegram.
    Используй эмодзи. Раздели на блоки: 🌤 Погода, 📈 Биржа, 📰 Новости.
    Не выдумывай того, чего нет в данных. 
    ВАЖНО: Объем текста строго до 3500 символов (лимит Telegram).
    
    Данные:
    Погода: {weather}
    Биржа: {stocks}
    Новости: {news}
    """
    
    try:
        response = ai.chat(prompt)
        return response.choices[0].message.content
    except Exception as e:
        print(f"Ошибка Gigachat: {e}")
        # Если нейросеть сломалась, отправим хотя бы сырые данные
        return f"🤖 Нейросеть занята, вот сырые данные:\n\n🌤 {weather}\n\n📈 {stocks}\n\n📰 {news}"

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
        "parse_mode": "HTML" # Чтобы нейросеть могла использовать жирный шрифт и т.д.
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
    raw_stocks = get_stocks()
    
    # 2. Прогоняем через нейросеть
    final_post = process_with_gigachat(raw_news, raw_weather, raw_stocks)
    
    # 3. Публикуем
    send_to_telegram(final_post)
    
    print("=== Бот завершил работу ===")
