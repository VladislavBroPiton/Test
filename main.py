import os
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai  # Новая библиотека
from aiohttp import web
import psycopg2  # Вместо asyncpg
from psycopg2.extras import RealDictCursor
import urllib.parse

# --- КОНФИГУРАЦИЯ ---
TELEGRAM_TOKEN = "ВАШ_ТОКЕН_BOTFATHER"
GEMINI_API_KEY = "ВАШ_КЛЮЧ_GOOGLE_AI_STUDIO"
DATABASE_URL = "ВАША_ССЫЛКА_ИЗ_NEON"  # Пример: postgresql://...

# Настройка Gemini (новый синтаксис)
client = genai.Client(api_key=GEMINI_API_KEY)
model = client.models.generate_content  # Типы моделей: 'gemini-1.5-flash', 'gemini-2.0-flash'

# --- БАЗА ДАННЫХ (ПСИХОПГ) ---
def get_db_connection():
    """Создаёт подключение к базе данных"""
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """Создаём таблицы при первом запуске"""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT UNIQUE,
                    username TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    user_id INT REFERENCES users(id),
                    content TEXT,
                    role TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS reactions (
                    id SERIAL PRIMARY KEY,
                    user_id INT REFERENCES users(id),
                    reaction TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            conn.commit()
        conn.close()
        print("✅ База данных инициализирована")
    except Exception as e:
        print(f"❌ Ошибка при инициализации базы: {e}")

# --- КОМАНДЫ БОТА ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я AI-помощник с базой данных PostgreSQL.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    text = update.message.text

    try:
        # Запись пользователя и сообщения
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Upsert пользователя
            cur.execute("""
                INSERT INTO users (telegram_id, username) VALUES (%s, %s)
                ON CONFLICT (telegram_id) DO NOTHING
            """, (user.id, user.first_name))
            
            # Получаем ID пользователя
            cur.execute("SELECT id FROM users WHERE telegram_id = %s", (user.id,))
            row = cur.fetchone()
            user_id = row[0] if row else None

            if user_id:
                # Сохраняем сообщение пользователя
                cur.execute("INSERT INTO messages (user_id, content, role) VALUES (%s, %s, 'user')", (user_id, text))
        
        conn.commit()  # Фиксируем изменения
        conn.close()

        # Генерация ответа ИИ
        response = model(
            model='gemini-1.5-flash',
            contents=f"Ты — вежливый продавец. Клиент: {text}"
        )
        answer = response.text

        # Запись ответа ИИ
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("INSERT INTO messages (user_id, content, role) VALUES (%s, %s, 'ai')", (user_id, answer))
        conn.commit()
        conn.close()

        await update.message.reply_text(answer)

    except Exception as e:
        print(f"Ошибка в боте: {e}")
        await update.message.reply_text("Произошла техническая ошибка. Пожалуйста, попробуйте позже.")

async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reaction_event = update.message_reaction
    if reaction_event:
        user = reaction_event.user
        reaction = reaction_event.new_reaction[0].emoji if reaction_event.new_reaction else None
        
        if reaction in ["❤️", "👍", "🔥"]:
            conn = get_db_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM users WHERE telegram_id = %s", (user.id,))
                    row = cur.fetchone()
                    if row:
                        user_id = row[0]
                        cur.execute("INSERT INTO reactions (user_id, reaction) VALUES (%s, %s)", (user_id, reaction))
                        conn.commit()
                        print(f"✅ Реакция {reaction} от {user.id} сохранена.")
            except Exception as e:
                print(f"Ошибка при сохранении реакции: {e}")
            finally:
                conn.close()

# --- ВЕБ-СЕРВЕР ДЛЯ HEALTH CHECK ---
async def handle_health(request):
    return web.Response(text="Бот живой!")

async def start_http_server():
    app = web.Application()
    app.router.add_get('/health', handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 10000)
    await site.start()
    print("🚀 HTTP сервер запущен на порту 10000")
    await asyncio.Event().wait()

# --- ЗАПУСК ---
async def main():
    # 1. Инициализация базы
    init_db()

    # 2. Создание приложения бота
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.REACTION, handle_reaction))

    # 3. Запускаем бота и веб-сервер параллельно
    await asyncio.gather(
        application.start_polling(allowed_updates=Update.ALL_TYPES),
        start_http_server()
    )

if __name__ == "__main__":
    asyncio.run(main())
