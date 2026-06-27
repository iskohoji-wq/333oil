"""
333 OIL — Telegram Bot + Mini App
Требования: pip install aiogram==3.* aiohttp
"""

import asyncio
import json
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, WebAppInfo,
    InlineKeyboardMarkup, InlineKeyboardButton,
    KeyboardButton, ReplyKeyboardMarkup,
    ReplyKeyboardRemove, WebAppData
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "ТВОЙ_ТОКЕН_ЗДЕСЬ"          # Вставь токен от @BotFather
MINI_APP_URL = "ССЫЛКА_НА_REPLIT_ЗДЕСЬ" # Вставь URL твоего Replit проекта
ADMIN_ID = 0                             # Вставь свой Telegram ID (узнай у @userinfobot)

# ========== ИНИЦИАЛИЗАЦИЯ ==========
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# Хранение активных заказов (в памяти, для продакшена — SQLite)
orders = {}
order_counter = [0]

# ========== КЛАВИАТУРЫ ==========

def kb_main(lang='ru'):
    """Главная клавиатура клиента"""
    if lang == 'uz':
        btn_order = "⛽ Yoqilg'i buyurtma"
        btn_status = "📍 Buyurtma holati"
        btn_history = "📋 Tarix"
        btn_lang = "🇷🇺 Русский"
    else:
        btn_order = "⛽ Заказать топливо"
        btn_status = "📍 Статус заказа"
        btn_history = "📋 История"
        btn_lang = "🇺🇿 O'zbek"

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(
                text=btn_order,
                web_app=WebAppInfo(url=MINI_APP_URL)
            )],
            [KeyboardButton(text=btn_status),
             KeyboardButton(text=btn_history)],
            [KeyboardButton(text=btn_lang)]
        ],
        resize_keyboard=True
    )

def kb_admin():
    """Клавиатура администратора"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Новые заказы"),
             KeyboardButton(text="🔵 В работе")],
            [KeyboardButton(text="✅ Выполненные"),
             KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="💰 Изменить цены"),
             KeyboardButton(text="🛢️ Остаток топлива")],
            [KeyboardButton(text="🔒 Выйти из админки")]
        ],
        resize_keyboard=True
    )

def kb_order_actions(order_id):
    """Кнопки управления заказом для администратора"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{order_id}"),
            InlineKeyboardButton(text="📞 Позвонить", callback_data=f"call_{order_id}")
        ],
        [
            InlineKeyboardButton(text="🚗 Выехал", callback_data=f"enroute_{order_id}"),
            InlineKeyboardButton(text="⛽ Выполнен", callback_data=f"done_{order_id}")
        ],
        [
            InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{order_id}")
        ]
    ])

def kb_client_track(order_id):
    """Кнопки для клиента во время отслеживания"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📍 Открыть карту", url=f"https://maps.google.com/?q=41.2995,69.2401")],
        [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"client_cancel_{order_id}")]
    ])

# ========== ПОЛЬЗОВАТЕЛИ ==========
user_lang = {}   # user_id -> 'ru' или 'uz'
admin_users = set()  # user_id который вошёл как админ

ADMIN_PIN = "33333"  # Поменяй в настройках бота
waiting_pin = set()  # user_id ожидающие ввода PIN

# ========== ХЕНДЛЕРЫ ==========

@dp.message(CommandStart())
async def cmd_start(msg: Message):
    uid = msg.from_user.id
    lang = user_lang.get(uid, 'ru')

    if lang == 'uz':
        text = (
            "⛽ <b>333 OIL</b> ga xush kelibsiz!\n\n"
            "🚗 Benzin to'g'ridan-to'g'ri mashinangizga yetkazamiz.\n\n"
            "Buyurtma berish uchun tugmani bosing 👇"
        )
    else:
        text = (
            "⛽ Добро пожаловать в <b>333 OIL</b>!\n\n"
            "🚗 Доставляем бензин прямо к вашей машине.\n\n"
            "Нажмите кнопку чтобы сделать заказ 👇"
        )

    await msg.answer(text, reply_markup=kb_main(lang))


@dp.message(Command("admin"))
async def cmd_admin(msg: Message):
    """Вход в панель администратора"""
    uid = msg.from_user.id
    waiting_pin.add(uid)
    await msg.answer(
        "🔐 Введите 5-значный PIN-код для входа в панель администратора:",
        reply_markup=ReplyKeyboardRemove()
    )


@dp.message(Command("id"))
async def cmd_id(msg: Message):
    """Узнать свой Telegram ID"""
    await msg.answer(f"Ваш Telegram ID: <code>{msg.from_user.id}</code>")


# ========== ОБРАБОТКА WEB APP ==========

@dp.message(F.web_app_data)
async def handle_webapp_data(msg: Message):
    """Получаем данные от Mini App когда клиент подтвердил заказ"""
    uid = msg.from_user.id
    lang = user_lang.get(uid, 'ru')

    try:
        data = json.loads(msg.web_app_data.data)
    except Exception:
        await msg.answer("❌ Ошибка данных. Попробуйте снова.")
        return

    if data.get('action') == 'new_order':
        # Создаём заказ
        order_counter[0] += 1
        order_id = order_counter[0]

        order = {
            'id': order_id,
            'user_id': uid,
            'user_name': msg.from_user.full_name,
            'user_username': msg.from_user.username or '—',
            'fuel': data.get('fuel', '—'),
            'liters': data.get('liters', 0),
            'price_per_liter': data.get('price_per_liter', 0),
            'total': data.get('total', 0),
            'payment': data.get('payment', '—'),
            'address': data.get('address', '—'),
            'location': data.get('location', '—'),
            'lang': data.get('lang', 'ru'),
            'status': 'new'
        }
        orders[order_id] = order

        # Уведомление клиенту
        if lang == 'uz':
            client_text = (
                f"✅ <b>Buyurtma #{order_id} qabul qilindi!</b>\n\n"
                f"⛽ {order['fuel']} — {order['liters']} litr\n"
                f"💰 {order['total']:,} so'm\n"
                f"💳 {order['payment']}\n"
                f"📍 {order['address'] or order['location']}\n\n"
                f"⏳ Haydovchi tez orada chiqadi..."
            )
        else:
            client_text = (
                f"✅ <b>Заказ #{order_id} принят!</b>\n\n"
                f"⛽ {order['fuel']} — {order['liters']} литров\n"
                f"💰 {order['total']:,} сўм\n"
                f"💳 {order['payment']}\n"
                f"📍 {order['address'] or order['location']}\n\n"
                f"⏳ Водитель скоро выедет..."
            )

        await msg.answer(client_text, reply_markup=kb_client_track(order_id))

        # Уведомление администратору
        if ADMIN_ID:
            admin_text = (
                f"🔴 <b>НОВЫЙ ЗАКАЗ #{order_id}</b>\n\n"
                f"👤 {order['user_name']} (@{order['user_username']})\n"
                f"📱 ID: <code>{uid}</code>\n\n"
                f"⛽ {order['fuel']} — {order['liters']} л\n"
                f"💰 {order['total']:,} сўм\n"
                f"💳 {order['payment']}\n"
                f"📍 {order['address'] or order['location']}"
            )
            try:
                await bot.send_message(
                    ADMIN_ID,
                    admin_text,
                    reply_markup=kb_order_actions(order_id)
                )
            except Exception as e:
                logging.error(f"Не удалось отправить уведомление админу: {e}")


# ========== PIN ВХОД ==========

@dp.message(lambda msg: msg.from_user.id in waiting_pin)
async def handle_pin(msg: Message):
    uid = msg.from_user.id
    pin = msg.text.strip() if msg.text else ''

    if pin == ADMIN_PIN:
        waiting_pin.discard(uid)
        admin_users.add(uid)
        await msg.answer(
            "✅ <b>Добро пожаловать в панель администратора!</b>\n\n"
            "Здесь вы можете управлять заказами 333 OIL.",
            reply_markup=kb_admin()
        )
    else:
        waiting_pin.discard(uid)
        await msg.answer(
            "❌ Неверный PIN. Попробуйте снова через /admin",
            reply_markup=kb_main()
        )


# ========== КНОПКИ КЛИЕНТА ==========

@dp.message(F.text.in_(["⛽ Статус заказа", "📍 Buyurtma holati", "📍 Статус заказа"]))
async def client_status(msg: Message):
    uid = msg.from_user.id
    lang = user_lang.get(uid, 'ru')
    # Ищем последний заказ пользователя
    user_orders = [o for o in orders.values() if o['user_id'] == uid]
    if not user_orders:
        text = "У вас нет активных заказов." if lang == 'ru' else "Sizda faol buyurtmalar yo'q."
        await msg.answer(text)
        return

    last = user_orders[-1]
    status_map = {
        'new': '🔴 Ожидает водителя',
        'accepted': '🔵 Водитель едет к вам',
        'enroute': '🚗 Водитель в пути',
        'done': '✅ Выполнен',
        'cancelled': '❌ Отменён'
    }
    status = status_map.get(last['status'], '—')
    text = (
        f"📋 <b>Заказ #{last['id']}</b>\n\n"
        f"⛽ {last['fuel']} — {last['liters']} л\n"
        f"💰 {last['total']:,} сўм\n"
        f"📍 {last['address'] or last['location']}\n"
        f"🔄 Статус: {status}"
    )
    await msg.answer(text)


@dp.message(F.text.in_(["📋 История", "📋 Tarix"]))
async def client_history(msg: Message):
    uid = msg.from_user.id
    lang = user_lang.get(uid, 'ru')
    user_orders = [o for o in orders.values() if o['user_id'] == uid]
    if not user_orders:
        text = "История заказов пуста." if lang == 'ru' else "Buyurtmalar tarixi bo'sh."
        await msg.answer(text)
        return

    lines = []
    for o in reversed(user_orders[-5:]):
        lines.append(f"#{o['id']} • {o['fuel']} {o['liters']}л • {o['total']:,} сўм")
    await msg.answer("📋 <b>История заказов:</b>\n\n" + "\n".join(lines))


@dp.message(F.text.in_(["🇺🇿 O'zbek", "🇷🇺 Русский"]))
async def switch_lang(msg: Message):
    uid = msg.from_user.id
    if "O'zbek" in msg.text:
        user_lang[uid] = 'uz'
        await msg.answer(
            "✅ Til o'zgartirildi: O'zbek\n\n"
            "⛽ <b>333 OIL</b> ga xush kelibsiz!",
            reply_markup=kb_main('uz')
        )
    else:
        user_lang[uid] = 'ru'
        await msg.answer(
            "✅ Язык изменён: Русский\n\n"
            "⛽ Добро пожаловать в <b>333 OIL</b>!",
            reply_markup=kb_main('ru')
        )


# ========== КНОПКИ АДМИНИСТРАТОРА ==========

@dp.message(F.text == "📋 Новые заказы")
async def admin_new_orders(msg: Message):
    if msg.from_user.id not in admin_users:
        return
    new_orders = [o for o in orders.values() if o['status'] == 'new']
    if not new_orders:
        await msg.answer("📋 Новых заказов нет.")
        return
    for o in new_orders:
        text = (
            f"🔴 <b>Заказ #{o['id']}</b>\n"
            f"👤 {o['user_name']}\n"
            f"⛽ {o['fuel']} — {o['liters']} л\n"
            f"💰 {o['total']:,} сўм • {o['payment']}\n"
            f"📍 {o['address'] or o['location']}"
        )
        await msg.answer(text, reply_markup=kb_order_actions(o['id']))


@dp.message(F.text == "🔵 В работе")
async def admin_active_orders(msg: Message):
    if msg.from_user.id not in admin_users:
        return
    active = [o for o in orders.values() if o['status'] in ['accepted', 'enroute']]
    if not active:
        await msg.answer("🔵 Нет заказов в работе.")
        return
    for o in active:
        status = "Принят" if o['status'] == 'accepted' else "Еду"
        text = (
            f"🔵 <b>Заказ #{o['id']}</b> — {status}\n"
            f"👤 {o['user_name']}\n"
            f"⛽ {o['fuel']} — {o['liters']} л\n"
            f"💰 {o['total']:,} сўм\n"
            f"📍 {o['address'] or o['location']}"
        )
        await msg.answer(text, reply_markup=kb_order_actions(o['id']))


@dp.message(F.text == "✅ Выполненные")
async def admin_done_orders(msg: Message):
    if msg.from_user.id not in admin_users:
        return
    done = [o for o in orders.values() if o['status'] == 'done']
    if not done:
        await msg.answer("✅ Выполненных заказов нет.")
        return
    total_sum = sum(o['total'] for o in done)
    lines = [f"#{o['id']} {o['fuel']} {o['liters']}л — {o['total']:,} сўм" for o in done[-10:]]
    await msg.answer(
        f"✅ <b>Выполненные заказы</b>\n\n" +
        "\n".join(lines) +
        f"\n\n💰 <b>Итого: {total_sum:,} сўм</b>"
    )


@dp.message(F.text == "📊 Статистика")
async def admin_stats(msg: Message):
    if msg.from_user.id not in admin_users:
        return
    total = len(orders)
    done = len([o for o in orders.values() if o['status'] == 'done'])
    revenue = sum(o['total'] for o in orders.values() if o['status'] == 'done')
    fuel_stats = {}
    for o in orders.values():
        fuel_stats[o['fuel']] = fuel_stats.get(o['fuel'], 0) + o['liters']

    fuel_lines = "\n".join([f"  {k}: {v} л" for k, v in fuel_stats.items()])
    await msg.answer(
        f"📊 <b>Статистика 333 OIL</b>\n\n"
        f"📋 Всего заказов: {total}\n"
        f"✅ Выполнено: {done}\n"
        f"💰 Выручка: {revenue:,} сўм\n\n"
        f"⛽ По видам топлива:\n{fuel_lines or '  нет данных'}"
    )


@dp.message(F.text == "🔒 Выйти из админки")
async def admin_logout(msg: Message):
    uid = msg.from_user.id
    admin_users.discard(uid)
    lang = user_lang.get(uid, 'ru')
    await msg.answer("🔒 Вы вышли из панели администратора.", reply_markup=kb_main(lang))


# ========== CALLBACK КНОПКИ (inline) ==========

@dp.callback_query(F.data.startswith("accept_"))
async def cb_accept(cq: CallbackQuery):
    order_id = int(cq.data.split("_")[1])
    if order_id not in orders:
        await cq.answer("Заказ не найден")
        return
    orders[order_id]['status'] = 'accepted'
    o = orders[order_id]
    # Уведомить клиента
    try:
        await bot.send_message(
            o['user_id'],
            f"✅ <b>Заказ #{order_id} принят!</b>\n\n"
            f"🚗 Водитель скоро выедет к вам.\n"
            f"📍 {o['address'] or o['location']}"
        )
    except Exception:
        pass
    await cq.message.edit_text(
        cq.message.text + "\n\n✅ <b>ПРИНЯТ</b>",
        reply_markup=kb_order_actions(order_id)
    )
    await cq.answer("✅ Заказ принят!")


@dp.callback_query(F.data.startswith("enroute_"))
async def cb_enroute(cq: CallbackQuery):
    order_id = int(cq.data.split("_")[1])
    if order_id not in orders:
        await cq.answer("Заказ не найден")
        return
    orders[order_id]['status'] = 'enroute'
    o = orders[order_id]
    try:
        await bot.send_message(
            o['user_id'],
            f"🚗 <b>Водитель выехал!</b>\n\n"
            f"Заказ #{order_id} • {o['fuel']} {o['liters']} л\n"
            f"⏱ Ожидайте, водитель едет к вам.",
            reply_markup=kb_client_track(order_id)
        )
    except Exception:
        pass
    await cq.answer("🚗 Статус обновлён — Еду!")


@dp.callback_query(F.data.startswith("done_"))
async def cb_done(cq: CallbackQuery):
    order_id = int(cq.data.split("_")[1])
    if order_id not in orders:
        await cq.answer("Заказ не найден")
        return
    orders[order_id]['status'] = 'done'
    o = orders[order_id]
    try:
        await bot.send_message(
            o['user_id'],
            f"⛽ <b>Заказ #{order_id} выполнен!</b>\n\n"
            f"✅ {o['fuel']} — {o['liters']} л залито\n"
            f"💰 {o['total']:,} сўм\n\n"
            f"Спасибо что выбрали 333 OIL! 🙏"
        )
    except Exception:
        pass
    await cq.message.edit_text(
        cq.message.text + f"\n\n✅ <b>ВЫПОЛНЕН • {o['total']:,} сўм</b>"
    )
    await cq.answer("🎉 Заказ выполнен!")


@dp.callback_query(F.data.startswith("cancel_"))
async def cb_cancel(cq: CallbackQuery):
    order_id = int(cq.data.split("_")[1])
    if order_id not in orders:
        await cq.answer("Заказ не найден")
        return
    orders[order_id]['status'] = 'cancelled'
    o = orders[order_id]
    try:
        await bot.send_message(
            o['user_id'],
            f"❌ <b>Заказ #{order_id} отменён.</b>\n\n"
            f"Если возникли вопросы — напишите нам."
        )
    except Exception:
        pass
    await cq.message.edit_text(cq.message.text + "\n\n❌ <b>ОТМЕНЁН</b>")
    await cq.answer("❌ Заказ отменён")


@dp.callback_query(F.data.startswith("call_"))
async def cb_call(cq: CallbackQuery):
    order_id = int(cq.data.split("_")[1])
    if order_id not in orders:
        await cq.answer("Заказ не найден")
        return
    o = orders[order_id]
    await cq.answer(f"Клиент: {o['user_name']} (@{o['user_username']})", show_alert=True)


@dp.callback_query(F.data.startswith("client_cancel_"))
async def cb_client_cancel(cq: CallbackQuery):
    order_id = int(cq.data.split("_")[2])
    if order_id not in orders:
        await cq.answer("Заказ не найден")
        return
    o = orders[order_id]
    if o['status'] in ['done', 'cancelled']:
        await cq.answer("Невозможно отменить — заказ уже завершён", show_alert=True)
        return
    if o['status'] in ['enroute']:
        await cq.answer("Невозможно отменить — водитель уже в пути", show_alert=True)
        return
    orders[order_id]['status'] = 'cancelled'
    await cq.message.edit_text("❌ Заказ отменён.")
    # Уведомить администратора
    if ADMIN_ID:
        await bot.send_message(ADMIN_ID, f"❌ Клиент отменил заказ #{order_id}")
    await cq.answer("❌ Заказ отменён")


# ========== ЗАПУСК ==========
async def main():
    logging.info("333 OIL Bot запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
