import asyncio
import sqlite3
import secrets
import time
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
import logging
import os
import aiohttp
import json

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "8974737212:AAFEHdqDDQfVQA9mHy6lihu13xvbcu2yk24"
OWNER_USERNAME = "@fuckwexide"
CRYPTO_BOT_TOKEN = "611404:AAKud0lWO0f4x6mWNAHye4PPfWsApwz3lLJ"
XROCK_WALLET = "UQA2nuvTbM1G8ltBYIpTIp0VUoxscixdbjVcT6TmxPukgSni"

# Цены подписок (в звёздах)
PRICES = {
    # Базовый доступ
    "vip_1m": 100,    # 1 месяц = 100⭐ (1$)
    "vip_3m": 270,    # 3 месяца = 270⭐ (2.7$) - скидка 10%
    "vip_6m": 480,    # 6 месяцев = 480⭐ (4.8$) - скидка 20%
    "vip_12m": 840,   # 12 месяцев = 840⭐ (8.4$) - скидка 30%
    
    # Премиум доступ
    "premium_1m": 700,    # 1 месяц = 700⭐ (7$)
    "premium_3m": 1890,   # 3 месяца = 1890⭐ (18.9$) - скидка 10%
    "premium_6m": 3360,   # 6 месяцев = 3360⭐ (33.6$) - скидка 20%
    "premium_12m": 5880,  # 12 месяцев = 5880⭐ (58.8$) - скидка 30%
}

# Цены в долларах для отображения
PRICES_USD = {
    "vip_1m": 1,
    "vip_3m": 2.7,
    "vip_6m": 4.8,
    "vip_12m": 8.4,
    "premium_1m": 7,
    "premium_3m": 18.9,
    "premium_6m": 33.6,
    "premium_12m": 58.8,
}

# Сроки в днях
DURATIONS = {
    "vip_1m": 30,
    "vip_3m": 90,
    "vip_6m": 180,
    "vip_12m": 365,
    "premium_1m": 30,
    "premium_3m": 90,
    "premium_6m": 180,
    "premium_12m": 365,
}

logging.basicConfig(level=logging.INFO)

# ========== БАЗА ДАННЫХ ==========
if os.path.exists("claude_fable.db"):
    os.remove("claude_fable.db")

class Database:
    def __init__(self):
        self.conn = sqlite3.connect("claude_fable.db")
        self.cursor = self.conn.cursor()
        self._create_tables()
    
    def _create_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TEXT,
                vip_until TEXT,
                premium_until TEXT,
                tokens INTEGER DEFAULT 0,
                total_spent INTEGER DEFAULT 0,
                access_token TEXT UNIQUE,
                chat_history TEXT DEFAULT '[]'
            )
        ''')
        self.conn.commit()
    
    def add_user(self, user_id, username, first_name):
        access_token = secrets.token_hex(32)
        self.cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, created_at, access_token) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, first_name, datetime.now().isoformat(), access_token)
        )
        self.conn.commit()
        return access_token
    
    def get_user(self, user_id):
        self.cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return self.cursor.fetchone()
    
    def get_user_by_token(self, token):
        self.cursor.execute("SELECT * FROM users WHERE access_token = ?", (token,))
        return self.cursor.fetchone()
    
    def verify_token(self, token):
        self.cursor.execute("SELECT user_id FROM users WHERE access_token = ?", (token,))
        result = self.cursor.fetchone()
        return result[0] if result else None
    
    def set_vip(self, user_id, days):
        until = (datetime.now() + timedelta(days=days)).isoformat()
        self.cursor.execute("UPDATE users SET vip_until = ? WHERE user_id = ?", (until, user_id))
        self.conn.commit()
        new_token = secrets.token_hex(32)
        self.cursor.execute("UPDATE users SET access_token = ? WHERE user_id = ?", (new_token, user_id))
        self.conn.commit()
        return new_token
    
    def set_premium(self, user_id, days):
        until = (datetime.now() + timedelta(days=days)).isoformat()
        self.cursor.execute("UPDATE users SET premium_until = ? WHERE user_id = ?", (until, user_id))
        self.conn.commit()
        new_token = secrets.token_hex(32)
        self.cursor.execute("UPDATE users SET access_token = ? WHERE user_id = ?", (new_token, user_id))
        self.conn.commit()
        return new_token
    
    def add_tokens(self, user_id, amount):
        self.cursor.execute("UPDATE users SET tokens = tokens + ? WHERE user_id = ?", (amount, user_id))
        self.conn.commit()
    
    def get_tokens(self, user_id):
        self.cursor.execute("SELECT tokens FROM users WHERE user_id = ?", (user_id,))
        result = self.cursor.fetchone()
        return result[0] if result else 0
    
    def add_spent(self, user_id, amount):
        self.cursor.execute("UPDATE users SET total_spent = total_spent + ? WHERE user_id = ?", (amount, user_id))
        self.conn.commit()
    
    def get_access_token(self, user_id):
        self.cursor.execute("SELECT access_token FROM users WHERE user_id = ?", (user_id,))
        result = self.cursor.fetchone()
        return result[0] if result else None

db = Database()

# ========== CRYPTOBOT API ==========
class CryptoBotAPI:
    def __init__(self, token):
        self.token = token
        self.base_url = "https://pay.crypt.bot/api"
    
    async def create_invoice(self, amount, currency="USDT", description="Claude Fable 5"):
        url = f"{self.base_url}/createInvoice"
        payload = {
            "asset": currency,
            "amount": str(amount),
            "description": description,
            "hidden_message": "Спасибо за доверие! 🚀",
            "payload": f"crypto_{int(time.time())}"
        }
        headers = {"Crypto-Pay-API-Token": self.token, "Content-Type": "application/json"}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('ok'):
                            return data['result']
                    return None
        except:
            return None

crypto_api = CryptoBotAPI(CRYPTO_BOT_TOKEN)

# ========== КЛАВИАТУРЫ ==========

def create_main_menu():
    builder = InlineKeyboardBuilder()
    
    btn1 = InlineKeyboardButton(text="💬 НАЧАТЬ ЧАТ С NEURAL", callback_data="start_chat")
    btn1.style = "success"
    builder.row(btn1)
    
    btn2 = InlineKeyboardButton(text="🔹 БАЗОВЫЙ ДОСТУП", callback_data="buy_vip_menu")
    btn2.style = "primary"
    btn3 = InlineKeyboardButton(text="⭐ ПРЕМИУМ ДОСТУП", callback_data="buy_premium_menu")
    btn3.style = "primary"
    builder.row(btn2, btn3)
    
    btn4 = InlineKeyboardButton(text="💰 КУПИТЬ ТОКЕНЫ (30$)", callback_data="buy_tokens")
    btn4.style = "success"
    builder.row(btn4)
    
    btn5 = InlineKeyboardButton(text="🔑 МОЙ ТОКЕН", callback_data="my_token")
    btn5.style = "primary"
    btn6 = InlineKeyboardButton(text="👤 ПРОФИЛЬ", callback_data="profile")
    btn6.style = "default"
    builder.row(btn5, btn6)
    
    btn7 = InlineKeyboardButton(text="📊 СТАТИСТИКА", callback_data="stats")
    btn7.style = "default"
    btn8 = InlineKeyboardButton(text="❓ ПОМОЩЬ", callback_data="help")
    btn8.style = "default"
    builder.row(btn7, btn8)
    
    return builder.as_markup()

def create_subscription_keyboard(plan_type):
    """Клавиатура выбора срока подписки"""
    builder = InlineKeyboardBuilder()
    
    if plan_type == "vip":
        plans = [
            ("1 месяц - 100⭐ (1$)", "vip_1m"),
            ("3 месяца - 270⭐ (2.7$)", "vip_3m"),
            ("6 месяцев - 480⭐ (4.8$)", "vip_6m"),
            ("12 месяцев - 840⭐ (8.4$)", "vip_12m"),
        ]
        title = "🔹 БАЗОВЫЙ ДОСТУП"
    else:
        plans = [
            ("1 месяц - 700⭐ (7$)", "premium_1m"),
            ("3 месяца - 1890⭐ (18.9$)", "premium_3m"),
            ("6 месяцев - 3360⭐ (33.6$)", "premium_6m"),
            ("12 месяцев - 5880⭐ (58.8$)", "premium_12m"),
        ]
        title = "⭐ ПРЕМИУМ ДОСТУП"
    
    for text, callback in plans:
        btn = InlineKeyboardButton(text=text, callback_data=f"buy_{callback}")
        btn.style = "primary"
        builder.row(btn)
    
    btn_back = InlineKeyboardButton(text="🔙 Назад", callback_data="back_menu")
    btn_back.style = "default"
    builder.row(btn_back)
    
    return builder.as_markup(), title

def create_payment_keyboard(product, price):
    builder = InlineKeyboardBuilder()
    
    btn1 = InlineKeyboardButton(text="⭐ Telegram Stars", callback_data=f"stars_{product}_{price}")
    btn1.style = "success"
    builder.row(btn1)
    
    btn2 = InlineKeyboardButton(text="🪙 XROCK (TON)", callback_data=f"xrock_{product}_{price}")
    btn2.style = "primary"
    builder.row(btn2)
    
    btn3 = InlineKeyboardButton(text="💳 CryptoBot USDT", callback_data=f"crypto_{product}_{price}")
    btn3.style = "primary"
    builder.row(btn3)
    
    btn4 = InlineKeyboardButton(text="🔙 Назад", callback_data="back_menu")
    btn4.style = "default"
    builder.row(btn4)
    
    return builder.as_markup()

def create_back_keyboard():
    builder = InlineKeyboardBuilder()
    btn = InlineKeyboardButton(text="🔙 Назад", callback_data="back_menu")
    btn.style = "default"
    builder.row(btn)
    return builder.as_markup()

# ========== ОБРАБОТЧИКИ ==========

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "Не указан"
    first_name = message.from_user.first_name or "Пользователь"
    
    access_token = db.add_user(user_id, username, first_name)
    
    text = f"""
✨ <b>CLAUDE FABLE 5</b> ✨

Привет, <b>{first_name}</b>! 👋

🤖 <b>ПЛАТФОРМА ИСКУССТВЕННОГО ИНТЕЛЛЕКТА</b>

🔥 <b>ДОСТУПНЫЕ МОДЕЛИ:</b>

<b>🔹 БАЗОВЫЙ ДОСТУП:</b>
• 🧠 <b>Claude Sonnet 4.6</b>
• 🧠 <b>Claude Opus 4.7</b>
• 🧠 <b>Claude Opus 4.8</b>
• 🧠 <b>Gemini 2.0 Flash</b>
• 🧠 <b>DeepSeek-V3</b>

<b>⭐ ПРЕМИУМ ДОСТУП:</b>
• 🧠 <b>Claude Sonnet 4.6</b>
• 🧠 <b>Claude Opus 4.7</b>
• 🧠 <b>Claude Opus 4.8</b>
• 🧠 <b>Gemini 2.0 Flash</b>
• 🧠 <b>DeepSeek-V3</b>
• 🧠 <b>GPT-4 Turbo</b>
• 🧠 <b>Mistral Large</b>
• 🧠 <b>Llama 3.1 405B</b>
• 🧠 <b>Claude Fable 5</b> — эксклюзивная модель

🔑 <b>ВАШ ТОКЕН ДОСТУПА:</b>
<code>{access_token}</code>

Сохраните токен! Он понадобится для входа в чат.

💬 Нажми "НАЧАТЬ ЧАТ С NEURAL" чтобы общаться!

👑 Владелец: {OWNER_USERNAME}
"""
    
    await message.answer(
        text,
        reply_markup=create_main_menu(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "back_menu")
async def back_to_menu(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "📋 <b>ГЛАВНОЕ МЕНЮ</b>\n\nВыберите действие:",
        reply_markup=create_main_menu(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "my_token")
async def show_token(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    token = db.get_access_token(user_id)
    
    if token:
        await callback.message.edit_text(
            f"🔑 <b>ВАШ ТОКЕН ДОСТУПА</b>\n\n"
            f"<code>{token}</code>\n\n"
            f"⚠️ Не передавайте токен никому!\n"
            f"Он нужен для доступа к нейросетям.",
            reply_markup=create_back_keyboard(),
            parse_mode="HTML"
        )
    else:
        await callback.answer("❌ Токен не найден!", show_alert=True)
    await callback.answer()

@dp.callback_query(F.data == "buy_vip_menu")
async def buy_vip_menu(callback: types.CallbackQuery):
    keyboard, title = create_subscription_keyboard("vip")
    await callback.message.edit_text(
        f"{title}\n\nВыберите срок подписки:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "buy_premium_menu")
async def buy_premium_menu(callback: types.CallbackQuery):
    keyboard, title = create_subscription_keyboard("premium")
    await callback.message.edit_text(
        f"{title}\n\nВыберите срок подписки:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("buy_vip_") or F.data.startswith("buy_premium_"))
async def buy_subscription(callback: types.CallbackQuery):
    product = callback.data.replace("buy_", "")
    price = PRICES.get(product, 100)
    days = DURATIONS.get(product, 30)
    usd_price = PRICES_USD.get(product, 1)
    
    is_premium = "premium" in product
    plan_name = "⭐ ПРЕМИУМ" if is_premium else "🔹 БАЗОВЫЙ"
    
    if is_premium:
        models = """• Claude Sonnet 4.6
• Claude Opus 4.7
• Claude Opus 4.8
• Gemini 2.0 Flash
• DeepSeek-V3
• GPT-4 Turbo
• Mistral Large
• Llama 3.1 405B
• Claude Fable 5"""
        limits = "БЕЗЛИМИТ"
    else:
        models = """• Claude Sonnet 4.6
• Claude Opus 4.7
• Claude Opus 4.8
• Gemini 2.0 Flash
• DeepSeek-V3"""
        limits = "50 запросов/день"
    
    months = days // 30
    
    text = f"""
{plan_name}

🧠 <b>Модели:</b>
{models}

📅 {months} {'месяц' if months == 1 else 'месяца' if months < 5 else 'месяцев'} ({days} дней)
🔍 {limits}
⚡ {'Максимальная скорость' if is_premium else 'Стандартная скорость'}
{'🆘 Приоритетная поддержка' if is_premium else ''}

💰 Цена: {price}⭐ ({usd_price}$)

✅ После оплаты вы получите ТОКЕН ДОСТУПА!
"""
    
    await callback.message.edit_text(
        text,
        reply_markup=create_payment_keyboard(product, price),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "buy_tokens")
async def buy_tokens(callback: types.CallbackQuery):
    text = """
💰 <b>ТОКЕНЫ</b>

💎 50.000.000 токенов
🎯 Внутренняя валюта Claude Fable 5
⚡ Доступ к эксклюзивным функциям

💰 Цена: 3000⭐ (30$)
"""
    await callback.message.edit_text(
        text,
        reply_markup=create_payment_keyboard("tokens", TOKENS_PRICE),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "profile")
async def profile(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    
    if user:
        tokens = user[6] if len(user) > 6 else 0
        spent = user[7] if len(user) > 7 else 0
        access_token = user[8] if len(user) > 8 else None
        
        status = "🔸 БЕСПЛАТНЫЙ"
        models = "❌ Нет доступа"
        
        # Проверяем премиум
        if user[5] and datetime.fromisoformat(user[5]) > datetime.now():
            status = "⭐ ПРЕМИУМ"
            models = "✅ Все 9 моделей AI"
            until = datetime.fromisoformat(user[5])
            days_left = (until - datetime.now()).days
            status += f" (осталось {days_left} дн.)"
        # Проверяем базовый
        elif user[4] and datetime.fromisoformat(user[4]) > datetime.now():
            status = "🔹 БАЗОВЫЙ"
            models = "✅ 5 моделей AI"
            until = datetime.fromisoformat(user[4])
            days_left = (until - datetime.now()).days
            status += f" (осталось {days_left} дн.)"
        
        text = f"""
👤 <b>ПРОФИЛЬ</b>

📛 Имя: {callback.from_user.first_name}
🆔 ID: {user_id}

💎 Статус: {status}

🧠 <b>ДОСТУПНЫЕ МОДЕЛИ:</b>
{models}

💰 Токенов: {tokens:,}
💸 Потрачено: {spent}⭐

🔑 <b>ТОКЕН ДОСТУПА:</b>
<code>{access_token}</code>

📅 Дата регистрации: {user[3][:10] if user[3] else "Неизвестно"}

👑 Владелец: {OWNER_USERNAME}
"""
    else:
        text = "❌ Профиль не найден"
    
    await callback.message.edit_text(
        text,
        reply_markup=create_back_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "stats")
async def stats(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    
    if user:
        tokens = user[6] if len(user) > 6 else 0
        spent = user[7] if len(user) > 7 else 0
        
        text = f"""
📊 <b>СТАТИСТИКА</b>

💰 Токенов: {tokens:,}
💸 Потрачено: {spent}⭐
📦 Покупок: {spent // 100 if spent > 0 else 0}

📈 <b>АКТИВНОСТЬ:</b>
{'▰' * min(10, spent // 100)}{'▱' * max(0, 10 - spent // 100)}
"""
    else:
        text = "❌ Статистика не найдена"
    
    await callback.message.edit_text(
        text,
        reply_markup=create_back_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "help")
async def help(callback: types.CallbackQuery):
    text = """
❓ <b>ПОМОЩЬ</b>

📌 <b>КАК КУПИТЬ:</b>
1. Выбери тариф в меню
2. Выбери срок подписки
3. Выбери способ оплаты
4. Оплати и получи токен доступа!

🔑 <b>ТОКЕН ДОСТУПА:</b>
После оплаты ты получишь уникальный токен.
Используй его для входа в чат с нейросетью.

💳 <b>СПОСОБЫ ОПЛАТЫ:</b>
⭐ Telegram Stars
🪙 XROCK (TON)
💳 CryptoBot USDT

📅 <b>ДОСТУПНЫЕ СРОКИ:</b>
• 1 месяц
• 3 месяца (скидка 10%)
• 6 месяцев (скидка 20%)
• 12 месяцев (скидка 30%)

🧠 <b>МОДЕЛИ:</b>

🔹 <b>БАЗОВЫЙ:</b>
• Claude Sonnet 4.6
• Claude Opus 4.7
• Claude Opus 4.8
• Gemini 2.0 Flash
• DeepSeek-V3

⭐ <b>ПРЕМИУМ:</b>
• Все модели базового +
• GPT-4 Turbo
• Mistral Large
• Llama 3.1 405B
• Claude Fable 5

❓ <b>ВОПРОСЫ:</b>
👑 Владелец: {OWNER_USERNAME}
"""
    
    await callback.message.edit_text(
        text,
        reply_markup=create_back_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "start_chat")
async def start_chat(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    
    # Проверяем доступ
    has_access = False
    if user[4] and datetime.fromisoformat(user[4]) > datetime.now():
        has_access = True
    if user[5] and datetime.fromisoformat(user[5]) > datetime.now():
        has_access = True
    
    if not has_access:
        await callback.message.edit_text(
            "❌ <b>НЕТ ДОСТУПА!</b>\n\n"
            "У вас нет активной подписки.\n"
            "Приобретите доступ в меню:\n"
            "🔹 Базовый - 1$\n"
            "⭐ Премиум - 7$",
            reply_markup=create_back_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    await callback.message.edit_text(
        "💬 <b>ЧАТ С NEURAL</b>\n\n"
        "Просто отправьте мне сообщение и я отвечу!\n"
        "Я использую все доступные модели AI.\n\n"
        "🔑 Ваш токен доступа:\n"
        f"<code>{db.get_access_token(user_id)}</code>\n\n"
        "⬇️ Пишите сообщение в этот чат!",
        reply_markup=create_back_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

# ========== ОБРАБОТКА ОПЛАТЫ ==========

@dp.callback_query(F.data.startswith("stars_"))
async def pay_stars(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    product = parts[1]
    price = int(parts[2])
    
    product_names = {
        "vip_1m": "Базовый - 1 месяц",
        "vip_3m": "Базовый - 3 месяца",
        "vip_6m": "Базовый - 6 месяцев",
        "vip_12m": "Базовый - 12 месяцев",
        "premium_1m": "Премиум - 1 месяц",
        "premium_3m": "Премиум - 3 месяца",
        "premium_6m": "Премиум - 6 месяцев",
        "premium_12m": "Премиум - 12 месяцев",
        "tokens": "50.000.000 токенов"
    }
    
    payload = f"{product}_{callback.from_user.id}_{int(time.time())}"
    
    try:
        price_label = LabeledPrice(label=product_names.get(product, "Claude Fable 5"), amount=price)
        
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title="Claude Fable 5",
            description=product_names.get(product, "Подписка"),
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[price_label]
        )
        
        await callback.message.delete()
        await callback.answer("💳 Счет создан!")
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка: {e}",
            reply_markup=create_back_keyboard()
        )
        await callback.answer()

@dp.callback_query(F.data.startswith("xrock_"))
async def pay_xrock(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    product = parts[1]
    price = int(parts[2])
    
    product_names = {
        "vip_1m": "Базовый - 1 месяц",
        "vip_3m": "Базовый - 3 месяца",
        "vip_6m": "Базовый - 6 месяцев",
        "vip_12m": "Базовый - 12 месяцев",
        "premium_1m": "Премиум - 1 месяц",
        "premium_3m": "Премиум - 3 месяца",
        "premium_6m": "Премиум - 6 месяцев",
        "premium_12m": "Премиум - 12 месяцев",
        "tokens": "50.000.000 токенов"
    }
    
    usd_price = price / 100
    
    text = f"""
🪙 <b>ОПЛАТА XROCK</b>

📥 <b>Адрес для пополнения:</b>
<code>{XROCK_WALLET}</code>

💰 <b>Сумма:</b> {usd_price}$ (в XROCK)
🌐 <b>Сеть:</b> TON

<b>Товар:</b> {product_names.get(product, "Claude Fable 5")}

<b>Инструкция:</b>
1. Отправьте XROCK на адрес
2. Напишите в поддержку @fuckwexide
3. После подтверждения получите ТОКЕН ДОСТУПА!

⚠️ Отправляйте только XROCK через сеть TON!
"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Скопировать адрес", callback_data=f"copy_xrock")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_menu")]
    ])
    
    keyboard.inline_keyboard[0][0].style = "success"
    keyboard.inline_keyboard[1][0].style = "default"
    
    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "copy_xrock")
async def copy_xrock(callback: types.CallbackQuery):
    await callback.answer(f"✅ Адрес скопирован: {XROCK_WALLET}", show_alert=True)

@dp.callback_query(F.data.startswith("crypto_"))
async def pay_crypto(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    product = parts[1]
    price = int(parts[2])
    
    product_names = {
        "vip_1m": "Базовый - 1 месяц",
        "vip_3m": "Базовый - 3 месяца",
        "vip_6m": "Базовый - 6 месяцев",
        "vip_12m": "Базовый - 12 месяцев",
        "premium_1m": "Премиум - 1 месяц",
        "premium_3m": "Премиум - 3 месяца",
        "premium_6m": "Премиум - 6 месяцев",
        "premium_12m": "Премиум - 12 месяцев",
        "tokens": "50.000.000 токенов"
    }
    
    await callback.message.edit_text(
        "⏳ Создаю счет...",
        reply_markup=create_back_keyboard(),
        parse_mode="HTML"
    )
    
    amount = price / 100
    invoice = await crypto_api.create_invoice(
        amount=amount,
        currency="USDT",
        description=f"Claude Fable 5 - {product_names.get(product, product)}"
    )
    
    if invoice:
        text = f"""
💳 <b>ОПЛАТА CRYPTOBOT</b>

💎 <b>Товар:</b> {product_names.get(product, "Claude Fable 5")}
💰 Сумма: {amount}$ (USDT)
🆔 Инвойс: {invoice['invoice_id']}

Нажми на кнопку для оплаты и получи ТОКЕН ДОСТУПА!
"""
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить USDT", url=invoice['pay_url'])],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_menu")]
        ])
        
        keyboard.inline_keyboard[0][0].style = "success"
        keyboard.inline_keyboard[1][0].style = "default"
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text(
            "❌ Ошибка создания счета. Попробуйте позже.",
            reply_markup=create_back_keyboard(),
            parse_mode="HTML"
        )
    
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout_handler(query: PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    user_id = message.from_user.id
    payload = message.successful_payment.invoice_payload
    amount = message.successful_payment.total_amount
    
    db.add_spent(user_id, amount)
    
    product = payload.split("_")[0]  # vip, premium, tokens
    if product in ["vip", "premium"]:
        # Получаем срок из payload
        parts = payload.split("_")
        if len(parts) >= 2:
            period = parts[1]  # 1m, 3m, 6m, 12m
            days = DURATIONS.get(f"{product}_{period}", 30)
            
            if product == "vip":
                new_token = db.set_vip(user_id, days)
                plan_name = "🔹 БАЗОВЫЙ"
                models = """• Claude Sonnet 4.6
• Claude Opus 4.7
• Claude Opus 4.8
• Gemini 2.0 Flash
• DeepSeek-V3"""
            else:
                new_token = db.set_premium(user_id, days)
                plan_name = "⭐ ПРЕМИУМ"
                models = """• Claude Sonnet 4.6
• Claude Opus 4.7
• Claude Opus 4.8
• Gemini 2.0 Flash
• DeepSeek-V3
• GPT-4 Turbo
• Mistral Large
• Llama 3.1 405B
• Claude Fable 5"""
            
            months = days // 30
            text = f"""
{plan_name} ДОСТУП АКТИВИРОВАН!

🧠 <b>Модели:</b>
{models}

📅 {months} {'месяц' if months == 1 else 'месяца' if months < 5 else 'месяцев'} ({days} дней)

🔑 <b>ВАШ ТОКЕН ДОСТУПА:</b>
<code>{new_token}</code>

Сохраните токен! Он нужен для входа в чат.

💬 Нажми "НАЧАТЬ ЧАТ С NEURAL" чтобы общаться!
"""
        else:
            text = "✅ Оплата прошла успешно!"
    else:
        # Токены
        db.add_tokens(user_id, 50000000)
        text = f"""
💰 <b>ТОКЕНЫ НАЧИСЛЕНЫ!</b>

💎 +50.000.000 токенов
🎯 Внутренняя валюта Claude Fable 5

🔑 <b>ВАШ ТОКЕН ДОСТУПА:</b>
<code>{db.get_access_token(user_id)}</code>

Спасибо за покупку! 🚀
"""
    
    await message.answer(
        text,
        reply_markup=create_main_menu(),
        parse_mode="HTML"
    )

# ========== ОБРАБОТКА СООБЩЕНИЙ В ЧАТЕ ==========

@dp.message(F.text & ~F.text.startswith('/'))
async def chat_with_neural(message: types.Message):
    user_id = message.from_user.id
    user = db.get_user(user_id)
    
    # Проверяем доступ
    has_access = False
    if user[4] and datetime.fromisoformat(user[4]) > datetime.now():
        has_access = True
    if user[5] and datetime.fromisoformat(user[5]) > datetime.now():
        has_access = True
    
    if not has_access:
        await message.answer(
            "❌ У вас нет активной подписки!\n"
            "Приобретите доступ в меню /start"
        )
        return
    
    # Проверяем токен
    token = db.get_access_token(user_id)
    if not token:
        await message.answer(
            "❌ Ошибка: токен доступа не найден!\n"
            "Обратитесь в поддержку."
        )
        return
    
    # Отправляем "печатает"
    await bot.send_chat_action(user_id, "typing")
    await asyncio.sleep(1)
    
    # Ответ от нейросети (имитация)
    responses = [
        "🤖 Привет! Я Claude Fable 5. Чем могу помочь?",
        "🧠 Отличный вопрос! Дай мне подумать...",
        "💡 Интересно! Вот что я думаю по этому поводу...",
        "🚀 Ого! Это сложный вопрос. Я использую все свои модели для ответа.",
        "✨ Классный вопрос! Мои нейросети уже работают над ответом.",
        "🔮 Я вижу глубину этого вопроса. Вот мой ответ:",
        "💎 Отличная мысль! Как ИИ нового поколения, я предлагаю такое решение:",
        "⚡ Быстрый ответ от Claude Fable 5:"
    ]
    
    response = random.choice(responses) + "\n\n" + message.text[::-1]
    
    # Сохраняем историю
    db.add_chat_message(user_id, "user", message.text)
    db.add_chat_message(user_id, "assistant", response)
    
    await message.answer(
        f"{response}\n\n"
        f"🔑 Токен: <code>{token[:8]}...{token[-8:]}</code>",
        parse_mode="HTML"
    )

# ========== ЗАПУСК ==========

async def main():
    print("🚀 Claude Fable 5 запущен!")
    print(f"👑 Владелец: {OWNER_USERNAME}")
    print("🧠 Модели:")
    print("  • Базовый: 5 моделей (Claude Sonnet 4.6, Opus 4.7, Opus 4.8, Gemini 2.0 Flash, DeepSeek-V3)")
    print("  • Премиум: 9 моделей (+ GPT-4 Turbo, Mistral Large, Llama 3.1 405B, Claude Fable 5)")
    print("📅 Сроки подписки: 1, 3, 6, 12 месяцев")
    print("💳 3 способа оплаты: Stars, XROCK, CryptoBot")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
