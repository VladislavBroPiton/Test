import os
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai
import asyncpg
from aiohttp import web

# --- КОНФИГУРАЦИЯ ---
TELEGRAM_TOKEN = "ВАШ_ТОКЕН_BOTFATHER"
GEMINI_API_KEY = "ВАШ_КЛЮЧ_GOOGLE_AI_STUDIO"
DATABASE_URL = "postgresql://user:pass@ep-xxx.neon.tech/dbname"  # Из настроек Neon

# Настройка Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')  # Бесплатный тариф

# --- БАЗА ДАННЫХ (NEON) ---
async def init_db():
    """Создаём таблицы при первом запуске"""
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("""
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
            role TEXT,  -- 'user' или 'ai'
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS reactions (
            id SERIAL PRIMARY KEY,
            user_id INT REFERENCES users(id),
            reaction TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    await conn.close()
    print("✅ База данных инициализирована")

# --- ФУНКЦИИ БОТА ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я AI-помощник с базой данных Neon.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    text = update.message.text

    # 1. Запись пользователя в базу
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        user_id = await conn.fetchval("""
            INSERT INTO users (telegram_id, username) 
            VALUES ($1, $2) ON CONFLICT (telegram_id) DO NOTHING 
            RETURNING id
        """, user.id, user.first_name)
        
        if user_id is None:
            # Если пользователь уже есть, получаем его ID
            user_id = await conn.fetchval("SELECT id FROM users WHERE telegram_id = $1", user.id)

        # Записываем сообщение пользователя
        await conn.execute("INSERT INTO messages (user_id, content, role) VALUES ($1, $2, 'user')", user_id, text)

        # 2. Генерация ответа ИИ
        response = model.generate_content(f"Ты — вежливый продавец. Клиент: {text}")
        answer = response.text

        # Записываем ответ ИИ
        await conn.execute("INSERT INTO messages (user_id, content, role) VALUES ($1, $2, 'ai')", user_id, answer)
        
        await update.message.reply_text(answer)
    finally:
        await conn.close()

async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Срабатывает, когда кто-то ставит реакцию на сообщение бота"""
    reaction_event = update.message_reaction
    if reaction_event:
        user = reaction_event.user
        reaction = reaction_event.new_reaction[0].emoji if reaction_event.new_reaction else None
        
        if reaction in ["❤️", "👍", "🔥"]:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                user_id = await conn.fetchval("SELECT id FROM users WHERE telegram_id = $1", user.id)
                if user_id:
                    await conn.execute("INSERT INTO reactions (user_id, reaction) VALUES ($1, $2)", user_id, reaction)
                    print(f"✅ Реакция {reaction} от {user.id} сохранена.")
            finally:
                await conn.close()

# --- ВЕБ-СЕРВЕР ДЛЯ ПРОБУЖДЕНИЯ (HEALTH CHECK) ---
async def handle_health(request):
    return web.Response(text="Бот живой!")

async def start_http_server():
    app = web.Application()
    app.router.add_get('/health', handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 10000)  # Render обычно использует порт 10000
    await site.start()
    print("🚀 HTTP сервер запущен на порту 10000")
    await asyncio.Event().wait()  # Держим процесс живым

# --- ЗАПУСК БОТА + СЕРВЕРА ---
async def main():
    # 1. Инициализация базы
    await init_db()

    # 2. Создание приложения бота
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # Обработчик реакций (бота надо сделать админом в группе)
    application.add_handler(MessageHandler(filters.REACTION, handle_reaction)) 

    # 3. Запускаем бота И веб-сервер параллельно
    await asyncio.gather(
        application.start_polling(allowed_updates=Update.ALL_TYPES),
        start_http_server()  # Эта функция блокируется, но работает в параллели
    )

if __name__ == "__main__":
    asyncio.run(main())
