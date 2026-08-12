import os
import requests
import feedparser
import yfinance as yf
from gigachat import GigaChat
from datetime import datetime, timedelta

# 1. Получаем наши секретные ключи из настроек GitHub
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GIGA_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS")

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

def get_moex_stocks():
    """Получаем данные по акциям MOEX и находим ТОП 3 роста и падения"""
    print("Получаю данные MOEX...")
    
    # Список популярных акций MOEX
    tickers = [
        "SBER.ME",    # Сбербанк
        "GAZP.ME",    # Газпром
        "LKOH.ME",    # Лукойл
        "YNDX.ME",    # Яндекс
        "TCSG.ME",    # Тинькофф
        "ROSN.ME",    # Роснефть
        "NVTK.ME",    # Новатэк
        "PLZL.ME",    # Полюс Золото
        "SNGS.ME",    # Сургутнефтегаз
        "GMKN.ME",    # Норникель
        "MTSS.ME",    # МТС
        "AFKS.ME",    # АФК Система
        "MGNT.ME",    # Магнит
        "ALRS.ME",    # АЛРОСА
        "FEES.ME",    # ФСК ЕЭС
    ]
    
    stocks_data = []
    
    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            # Получаем данные за сегодня (1 день)
            hist = stock.history(period="1d", interval="1m")
            
            if not hist.empty and len(hist) > 1:
                # Первая и последняя цена за день
                open_price = hist['Open'].iloc[0]
                close_price = hist['Close'].iloc[-1]
                
                # Считаем процент изменения
                change_percent = ((close_price - open_price) / open_price) * 100
                
                stocks_data.append({
                    'ticker': ticker.replace('.ME', ''),
                    'change': change_percent,
                    'price': close_price
                })
        except Exception as e:
            print(f"Ошибка получения данных {ticker}: {e}")
            continue
    
    # Сортируем по изменению
    stocks_data.sort(key=lambda x: x['change'], reverse=True)
    
    # ТОП 3 роста
    top_gainers = stocks_data[:3]
    # ТОП 3 падения (последние 3)
    top_losers = stocks_data[-3:][::-1]  # Переворачиваем, чтобы самое большое падение было первым
    
    gainers_text = "\n".join([f"- {s['ticker']}: {s['change']:+.2f}% ({s['price']:.2f} ₽)" for s in top_gainers])
    losers_text = "\n".join([f"- {s['ticker']}: {s['change']:+.2f}% ({s['price']:.2f} ₽)" for s in top_losers])
    
    return f" ТОП-3 роста:\n{gainers_text}\n\n📉 ТОП-3 падения:\n{losers_text}"

def process_with_gigachat(news, weather, stocks):
    """Отправляем сырые данные в Gigachat, чтобы она сделала красивый пост"""
    print("Обрабатываю данные в Gigachat...")
    
    # Инициализируем Gigachat с указанием модели
    ai = GigaChat(
        credentials=GIGA_CREDENTIALS, 
        scope="GIGACHAT_API_PERS", 
        verify_ssl_certs=False,
        model="GigaChat"
    )
    
    prompt = f"""
    Ты - редактор новостного Telegram-канала. 
    Я дам тебе сырые данные. Твоя задача - написать из них короткий, интересный и структурированный пост для Telegram.
    Используй эмодзи. Раздели на блоки: 🌤 Погода, 📈 Биржа (MOEX), 📰 Новости (ТАСС).
    Не выдумывай того, чего нет в данных. 
    ВАЖНО: Объем текста строго до 3500 символов (лимит Telegram).
    Для биржи выдели отдельно лидеров роста и падения.
    
    Данные:
    Погода: {weather}
    Биржа MOEX: {stocks}
    Новости ТАСС: {news}
    """
    
    try:
        response = ai.chat(prompt)
        return response.choices[0].message.content
    except Exception as e:
        print(f"Ошибка Gigachat: {e}")
        # Если нейросеть сломалась, отправим хотя бы сырые данные
        return f"🤖 Нейросеть занята, вот сырые данные:\n\n {weather}\n\n📈 Биржа MOEX:\n{stocks}\n\n Новости ТАСС:\n{news}"

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
    raw_stocks = get_moex_stocks()
    
    # 2. Прогоняем через нейросеть
    final_post = process_with_gigachat(raw_news, raw_weather, raw_stocks)
    
    # 3. Публикуем
    send_to_telegram(final_post)
    
    print("=== Бот завершил работу ===")
