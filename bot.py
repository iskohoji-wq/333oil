"""
333 OIL — Telegram Bot + Mini App
pip install aiogram==3.13.0 aiohttp==3.9.5
"""

import asyncio
import json
import logging
import os
import random
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, WebAppInfo,
    InlineKeyboardMarkup, InlineKeyboardButton,
    KeyboardButton, ReplyKeyboardMarkup,
    ReplyKeyboardRemove, WebAppData
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# ========== НАСТРОЙКИ ==========
import os
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MINI_APP_URL = os.environ.get("WEBAPP_URL", "https://iskohoji-wq.github.io/333oil/333oil-prototype.html")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
ADMIN_PIN = os.environ.get("ADMIN_PIN", "33333")

# ========== ИНИЦИАЛИЗАЦИЯ ==========
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# ========== ХРАНИЛИЩЕ ==========
USERS_FILE = "users.json"
ORDERS_FILE = "orders.json"
ORDER_MAX_AGE_DAYS = 7  # завершённые/отменённые заказы старше недели удаляются

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            data = json.load(f)
            return {int(k): v for k, v in data.items()}
    return {}

def save_users():
    with open(USERS_FILE, "w") as f:
        json.dump({str(k): v for k, v in users.items()}, f)

def load_orders():
    if os.path.exists(ORDERS_FILE):
        with open(ORDERS_FILE, "r") as f:
            data = json.load(f)
            return {int(k): v for k, v in data.items()}
    return {}

def save_orders():
    with open(ORDERS_FILE, "w") as f:
        json.dump({str(k): v for k, v in orders.items()}, f, ensure_ascii=False)

users = load_users()
      # user_id -> {phone, lang, name, verified}
orders = load_orders()
order_counter = [max([o['id'] for o in orders.values()], default=0)]
otp_codes = {}   # user_id -> code
admin_users = set()
waiting_pin = set()
fuel_stock = {   # Остатки топлива в литрах
    'АИ-92': 500,
    'АИ-95': 500,
    'АИ-98': 300,
    'Дизель': 400
}

# ========== FSM ==========
class RegStates(StatesGroup):
    waiting_phone = State()
    waiting_otp = State()

# ========== КЛАВИАТУРЫ ==========

def kb_lang():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
            InlineKeyboardButton(text="🇺🇿 O'zbek", callback_data="lang_uz")
        ]
    ])

def kb_main(lang='ru'):
    if lang == 'uz':
        return ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="⛽ Yoqilg'i buyurtma", web_app=WebAppInfo(url=MINI_APP_URL))],
            [KeyboardButton(text="📍 Buyurtma holati"), KeyboardButton(text="📋 Tarix")],
            [KeyboardButton(text="🇷🇺 Русский")]
        ], resize_keyboard=True)
    else:
        return ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="⛽ Заказать топливо", web_app=WebAppInfo(url=MINI_APP_URL))],
            [KeyboardButton(text="📍 Статус заказа"), KeyboardButton(text="📋 История")],
            [KeyboardButton(text="🇺🇿 O'zbek")]
        ], resize_keyboard=True)

def kb_admin():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📋 Новые заказы"), KeyboardButton(text="🔵 В работе")],
        [KeyboardButton(text="✅ Выполненные"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="🛢️ Остатки топлива"), KeyboardButton(text="💰 Цены")],
        [KeyboardButton(text="🔒 Выйти из админки")]
    ], resize_keyboard=True)

def kb_order_actions(order_id, lat=None, lng=None):
    rows = [
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{order_id}"),
            InlineKeyboardButton(text="📞 Позвонить", callback_data=f"call_{order_id}")
        ],
        [
            InlineKeyboardButton(text="🚗 Выехал", callback_data=f"enroute_{order_id}"),
            InlineKeyboardButton(text="⛽ Выполнен", callback_data=f"done_{order_id}")
        ],
        [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{order_id}")]
    ]
    if lat and lng:
        rows.insert(0, [InlineKeyboardButton(
            text="🗺️ Открыть на карте",
            url=f"https://maps.google.com/?q={lat},{lng}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_client_track(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"client_cancel_{order_id}")]
    ])

def kb_share_phone(lang='ru'):
    label = "📱 Поделиться номером" if lang == 'ru' else "📱 Raqamni ulashish"
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=label, request_contact=True)]
    ], resize_keyboard=True, one_time_keyboard=True)

# ========== ХЕНДЛЕРЫ ==========

@dp.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    await state.clear()

    # Если уже зарегистрирован
    if uid in users and users[uid].get('verified'):
        lang = users[uid].get('lang', 'ru')
        if lang == 'uz':
            text = "⛽ <b>333 OIL</b>ga xush kelibsiz!\n\nYoqilg'i buyurtma qilish uchun tugmani bosing 👇"
        else:
            text = "⛽ Добро пожаловать в <b>333 OIL</b>!\n\nНажмите кнопку чтобы заказать топливо 👇"
        await msg.answer(text, reply_markup=kb_main(lang))
        return

    # Новый пользователь — выбор языка
    await msg.answer(
        "🇷🇺 Выберите язык\n🇺🇿 Tilni tanlang",
        reply_markup=kb_lang()
    )

@dp.callback_query(F.data.startswith("lang_"))
async def cb_lang(cq: CallbackQuery, state: FSMContext):
    uid = cq.from_user.id
    lang = cq.data.split("_")[1]

    if uid not in users:
        users[uid] = {}
    users[uid]['lang'] = lang
    save_users()

    users[uid]['name'] = cq.from_user.full_name
    users[uid]['username'] = cq.from_user.username or 'нет'
    save_users()


    await cq.message.delete()

    if lang == 'uz':
        text = "📱 Telefon raqamingizni yuboring — ro'yxatdan o'tish uchun"
    else:
        text = "📱 Отправьте ваш номер телефона для регистрации"

    await cq.message.answer(text, reply_markup=kb_share_phone(lang))
    await state.set_state(RegStates.waiting_phone)
    await cq.answer()

@dp.message(RegStates.waiting_phone)
async def handle_phone(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    lang = users.get(uid, {}).get('lang', 'ru')

    phone = None
    if msg.contact:
        phone = msg.contact.phone_number
        if not phone.startswith('+'):
            phone = '+' + phone
    elif msg.text and (msg.text.startswith('+') or msg.text.isdigit()):
        phone = msg.text.strip()
    else:
        if lang == 'uz':
            await msg.answer("📱 Iltimos raqamni yuboring", reply_markup=kb_share_phone(lang))
        else:
            await msg.answer("📱 Пожалуйста отправьте номер телефона", reply_markup=kb_share_phone(lang))
        return

    users[uid]['phone'] = phone

    # Генерируем OTP
    code = str(random.randint(1000, 9999))
    otp_codes[uid] = code

    # В реальном проекте тут отправка SMS через Eskiz/PlayMobile
    # Сейчас показываем код прямо (для теста)
    if lang == 'uz':
        text = (
            f"📲 <b>{phone}</b> raqamiga kod yuborildi.\n\n"
            f"🔑 Test uchun kod: <code>{code}</code>\n\n"
            "Kodni kiriting:"
        )
    else:
        text = (
            f"📲 Код отправлен на <b>{phone}</b>\n\n"
            f"🔑 Код для теста: <code>{code}</code>\n\n"
            "Введите код:"
        )

    await msg.answer(text, reply_markup=ReplyKeyboardRemove())
    await state.set_state(RegStates.waiting_otp)

@dp.message(RegStates.waiting_otp)
async def handle_otp(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    lang = users.get(uid, {}).get('lang', 'ru')
    code = msg.text.strip() if msg.text else ''

    if code == otp_codes.get(uid):
        users[uid]['verified'] = True
        otp_codes.pop(uid, None)
        await state.clear()

        if lang == 'uz':
            text = (
                f"✅ <b>Ro'yxatdan o'tdingiz!</b>\n\n"
                f"📱 Raqam: {users[uid].get('phone')}\n\n"
                "⛽ Endi yoqilg'i buyurtma qilishingiz mumkin 👇"
            )
        else:
            text = (
                f"✅ <b>Регистрация прошла успешно!</b>\n\n"
                f"📱 Номер: {users[uid].get('phone')}\n\n"
                "⛽ Теперь вы можете заказать топливо 👇"
            )
        await msg.answer(text, reply_markup=kb_main(lang))
    else:
        if lang == 'uz':
            await msg.answer("❌ Noto'g'ri kod. Qayta kiriting:")
        else:
            await msg.answer("❌ Неверный код. Попробуйте ещё раз:")

@dp.message(Command("admin"))
async def cmd_admin(msg: Message):
    waiting_pin.add(msg.from_user.id)
    await msg.answer("🔐 Введите PIN:", reply_markup=ReplyKeyboardRemove())

@dp.message(Command("id"))
async def cmd_id(msg: Message):
    await msg.answer(f"Ваш ID: <code>{msg.from_user.id}</code>")

@dp.message(Command("reset"))
async def cmd_reset(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    users.pop(uid, None)
    save_users()
    await state.clear()
    await msg.answer(
        "🔄 Регистрация сброшена.\nНажмите /start, чтобы пройти заново — снова появится выбор языка.",
        reply_markup=ReplyKeyboardRemove()
    )

# ========== WEB APP ==========

@dp.message(F.web_app_data)
async def handle_webapp(msg: Message):
    uid = msg.from_user.id
    lang = users.get(uid, {}).get('lang', 'ru')
    phone = users.get(uid, {}).get('phone', '—')

    try:
        data = json.loads(msg.web_app_data.data)
    except:
        await msg.answer("❌ Ошибка данных")
        return

    if data.get('action') == 'new_order':
        order_counter[0] += 1
        oid = order_counter[0]
        lat = data.get('lat')
        lng = data.get('lng')
        loc_text = data.get('location', '—')
        delivery_fee = data.get('delivery_fee', 0)
        total = data.get('total', 0)

        order = {
            'id': oid,
            'user_id': uid,
            'user_name': msg.from_user.full_name,
            'username': msg.from_user.username or '—',
            'phone': phone,
            'fuel': data.get('fuel', '—'),
            'liters': data.get('liters', 0),
            'price_per_liter': data.get('price_per_liter', 0),
            'total': total,
            'delivery_fee': delivery_fee,
            'payment': data.get('payment', '—'),
            'address': data.get('address', '—'),
            'location': loc_text,
            'lat': lat,
            'lng': lng,
            'status': 'new',
            'created_at': datetime.now().isoformat()
        }
        orders[oid] = order
        save_orders()

        # Клиенту
        if lang == 'uz':
            client_text = (
                f"✅ <b>Buyurtma #{oid} qabul qilindi!</b>\n\n"
                f"⛽ {order['fuel']} — {order['liters']} litr\n"
                f"💰 {order['total']:,} so'm\n"
                f"🚚 Yetkazish: {delivery_fee:,} so'm\n"
                f"💳 {order['payment']}\n"
                f"📍 {loc_text}\n\n"
                "⏳ Haydovchi tez orada chiqadi..."
            )
        else:
            client_text = (
                f"✅ <b>Заказ #{oid} принят!</b>\n\n"
                f"⛽ {order['fuel']} — {order['liters']} литров\n"
                f"💰 {order['total']:,} сўм\n"
                f"🚚 Доставка: {delivery_fee:,} сўм\n"
                f"💳 {order['payment']}\n"
                f"📍 {loc_text}\n\n"
                "⏳ Водитель скоро выедет..."
            )
        await msg.answer(client_text, reply_markup=kb_client_track(oid))

        # Админу
        if ADMIN_ID:
            maps_link = f"https://maps.google.com/?q={lat},{lng}" if lat and lng else None
            admin_text = (
                f"🔴 <b>НОВЫЙ ЗАКАЗ #{oid}</b>\n\n"
                f"👤 {order['user_name']} (@{order['username']})\n"
                f"📱 <b>{phone}</b>\n"
                f"🆔 {uid}\n\n"
                f"⛽ {order['fuel']} — {order['liters']} л\n"
                f"💰 {total:,} сўм\n"
                f"🚚 Доставка: {delivery_fee:,} сўм\n"
                f"💳 {order['payment']}\n"
                f"📍 {loc_text}"
            )
            if maps_link:
                admin_text += f"\n🗺️ <a href='{maps_link}'>Открыть на карте</a>"
            try:
                await bot.send_message(
                    ADMIN_ID, admin_text,
                    reply_markup=kb_order_actions(oid, lat, lng),
                    disable_web_page_preview=True
                )
            except Exception as e:
                logging.error(f"Ошибка отправки админу: {e}")

    elif data.get('action') == 'update_stock':
        # Обновление остатков из Mini App
        if uid == ADMIN_ID or uid in admin_users:
            stock = data.get('stock', {})
            for fuel, amount in stock.items():
                if fuel in fuel_stock:
                    fuel_stock[fuel] = int(amount)

# ========== КЛИЕНТСКИЕ КНОПКИ ==========

@dp.message(F.text.in_(["📍 Статус заказа", "📍 Buyurtma holati"]))
async def client_status(msg: Message):
    uid = msg.from_user.id
    lang = users.get(uid, {}).get('lang', 'ru')
    user_orders = [o for o in orders.values() if o['user_id'] == uid]
    if not user_orders:
        text = "У вас нет активных заказов." if lang == 'ru' else "Faol buyurtmalar yo'q."
        await msg.answer(text)
        return
    last = user_orders[-1]
    status_map = {
        'new': '🔴 Ожидает',
        'accepted': '🔵 Принят',
        'enroute': '🚗 Водитель едет',
        'done': '✅ Выполнен',
        'cancelled': '❌ Отменён'
    }
    status = status_map.get(last['status'], '—')
    await msg.answer(
        f"📋 <b>Заказ #{last['id']}</b>\n\n"
        f"⛽ {last['fuel']} — {last['liters']} л\n"
        f"💰 {last['total']:,} сўм\n"
        f"🔄 {status}"
    )

@dp.message(F.text.in_(["📋 История", "📋 Tarix"]))
async def client_history(msg: Message):
    uid = msg.from_user.id
    lang = users.get(uid, {}).get('lang', 'ru')
    user_orders = [o for o in orders.values() if o['user_id'] == uid]
    if not user_orders:
        text = "История пуста." if lang == 'ru' else "Tarix bo'sh."
        await msg.answer(text)
        return
    lines = [f"#{o['id']} • {o['fuel']} {o['liters']}л • {o['total']:,} сўм" for o in reversed(user_orders[-5:])]
    await msg.answer("📋 <b>История:</b>\n\n" + "\n".join(lines))

@dp.message(F.text.in_(["🇺🇿 O'zbek", "🇷🇺 Русский"]))
async def switch_lang(msg: Message):
    uid = msg.from_user.id
    if "O'zbek" in msg.text:
        if uid not in users:
            users[uid] = {}
        users[uid]['lang'] = 'uz'
        await msg.answer("✅ Til: O'zbek", reply_markup=kb_main('uz'))
    else:
        if uid not in users:
            users[uid] = {}
        users[uid]['lang'] = 'ru'
        await msg.answer("✅ Язык: Русский", reply_markup=kb_main('ru'))

# ========== АДМИН КНОПКИ ==========

@dp.message(lambda m: m.from_user.id in waiting_pin)
async def handle_pin(msg: Message):
    uid = msg.from_user.id
    if msg.text and msg.text.strip() == ADMIN_PIN:
        waiting_pin.discard(uid)
        admin_users.add(uid)
        await msg.answer("✅ Добро пожаловать в панель администратора!", reply_markup=kb_admin())
    else:
        waiting_pin.discard(uid)
        await msg.answer("❌ Неверный PIN. /admin для повтора", reply_markup=kb_main())

@dp.message(F.text == "📋 Новые заказы")
async def admin_new(msg: Message):
    if msg.from_user.id not in admin_users:
        return
    new = [o for o in orders.values() if o['status'] == 'new']
    if not new:
        await msg.answer("📋 Новых заказов нет.")
        return
    for o in new:
        text = (
            f"🔴 <b>Заказ #{o['id']}</b>\n"
            f"👤 {o['user_name']} (@{o['username']})\n"
            f"📱 <b>{o['phone']}</b>\n"
            f"⛽ {o['fuel']} — {o['liters']} л\n"
            f"💰 {o['total']:,} сўм\n"
            f"🚚 Доставка: {o.get('delivery_fee',0):,} сўм\n"
            f"💳 {o['payment']}\n"
            f"📍 {o['location']}"
        )
        lat, lng = o.get('lat'), o.get('lng')
        if lat and lng:
            text += f"\n🗺️ <a href='https://maps.google.com/?q={lat},{lng}'>Карта</a>"
        await msg.answer(text, reply_markup=kb_order_actions(o['id'], lat, lng), disable_web_page_preview=True)

@dp.message(F.text == "🔵 В работе")
async def admin_active(msg: Message):
    if msg.from_user.id not in admin_users:
        return
    active = [o for o in orders.values() if o['status'] in ['accepted','enroute']]
    if not active:
        await msg.answer("🔵 Нет заказов в работе.")
        return
    for o in active:
        await msg.answer(
            f"🔵 <b>Заказ #{o['id']}</b>\n"
            f"👤 {o['user_name']} | 📱 {o['phone']}\n"
            f"⛽ {o['fuel']} — {o['liters']} л\n"
            f"💰 {o['total']:,} сўм",
            reply_markup=kb_order_actions(o['id'], o.get('lat'), o.get('lng'))
        )

@dp.message(F.text == "✅ Выполненные")
async def admin_done(msg: Message):
    if msg.from_user.id not in admin_users:
        return
    done = [o for o in orders.values() if o['status'] == 'done']
    if not done:
        await msg.answer("✅ Нет выполненных заказов.")
        return
    total = sum(o['total'] for o in done)
    lines = [f"#{o['id']} {o['fuel']} {o['liters']}л — {o['total']:,} сўм" for o in done[-10:]]
    await msg.answer(f"✅ <b>Выполненные</b>\n\n" + "\n".join(lines) + f"\n\n💰 <b>Итого: {total:,} сўм</b>")

@dp.message(F.text == "📊 Статистика")
async def admin_stats(msg: Message):
    if msg.from_user.id not in admin_users:
        return
    total = len(orders)
    done = len([o for o in orders.values() if o['status'] == 'done'])
    revenue = sum(o['total'] for o in orders.values() if o['status'] == 'done')
    await msg.answer(
        f"📊 <b>Статистика 333 OIL</b>\n\n"
        f"📋 Всего заказов: {total}\n"
        f"✅ Выполнено: {done}\n"
        f"💰 Выручка: {revenue:,} сўм"
    )

@dp.message(F.text == "🛢️ Остатки топлива")
async def admin_stock(msg: Message):
    if msg.from_user.id not in admin_users:
        return
    lines = [f"{k}: <b>{v} л</b>" for k, v in fuel_stock.items()]
    text = "🛢️ <b>Остатки топлива:</b>\n\n" + "\n".join(lines)
    text += "\n\nДля изменения отправьте:\n<code>stock АИ-92 350</code>"
    await msg.answer(text)

@dp.message(F.text.startswith("stock "))
async def update_stock(msg: Message):
    if msg.from_user.id not in admin_users:
        return
    parts = msg.text.split()
    if len(parts) < 3:
        await msg.answer("❌ Формат: <code>stock АИ-92 350</code>")
        return
    fuel = parts[1]
    if fuel == 'АИ':
        fuel = parts[1] + ' ' + parts[2]
        amount = parts[3] if len(parts) > 3 else '0'
    else:
        amount = parts[2]
    try:
        amount = int(amount)
        if fuel in fuel_stock:
            fuel_stock[fuel] = amount
            await msg.answer(f"✅ {fuel}: {amount} л обновлено!")
        else:
            await msg.answer(f"❌ Неизвестное топливо: {fuel}\nДоступно: {', '.join(fuel_stock.keys())}")
    except:
        await msg.answer("❌ Неверный формат числа")

@dp.message(F.text == "🔒 Выйти из админки")
async def admin_logout(msg: Message):
    uid = msg.from_user.id
    admin_users.discard(uid)
    lang = users.get(uid, {}).get('lang', 'ru')
    await msg.answer("🔒 Вы вышли.", reply_markup=kb_main(lang))

# ========== INLINE CALLBACKS ==========

@dp.callback_query(F.data.startswith("accept_"))
async def cb_accept(cq: CallbackQuery):
    oid = int(cq.data.split("_")[1])
    if oid not in orders:
        await cq.answer("Заказ не найден")
        return
    orders[oid]['status'] = 'accepted'
    save_orders()
    o = orders[oid]
    try:
        await bot.send_message(o['user_id'],
            f"✅ <b>Заказ #{oid} принят!</b>\n🚗 Водитель скоро выедет.")
    except: pass
    await cq.message.edit_text(cq.message.text + "\n\n✅ <b>ПРИНЯТ</b>",
        reply_markup=kb_order_actions(oid, o.get('lat'), o.get('lng')),
        disable_web_page_preview=True)
    await cq.answer("✅ Принят!")

@dp.callback_query(F.data.startswith("enroute_"))
async def cb_enroute(cq: CallbackQuery):
    oid = int(cq.data.split("_")[1])
    if oid not in orders:
        await cq.answer("Заказ не найден")
        return
    orders[oid]['status'] = 'enroute'
    save_orders()
    o = orders[oid]
    try:
        await bot.send_message(o['user_id'],
            f"🚗 <b>Водитель выехал!</b>\nЗаказ #{oid} — едем к вам!")
    except: pass
    await cq.answer("🚗 Выехал!")

@dp.callback_query(F.data.startswith("done_"))
async def cb_done(cq: CallbackQuery):
    oid = int(cq.data.split("_")[1])
    if oid not in orders:
        await cq.answer("Заказ не найден")
        return
    orders[oid]['status'] = 'done'
    o = orders[oid]
    # Уменьшаем остаток
    if o['fuel'] in fuel_stock:
        fuel_stock[o['fuel']] = max(0, fuel_stock[o['fuel']] - o['liters'])
    save_orders()
    try:
        await bot.send_message(o['user_id'],
            f"⛽ <b>Заказ #{oid} выполнен!</b>\n✅ {o['fuel']} — {o['liters']} л залито\n💰 {o['total']:,} сўм\n\nСпасибо! 🙏")
    except: pass
    await cq.message.edit_text(cq.message.text + f"\n\n✅ <b>ВЫПОЛНЕН</b>")
    await cq.answer("🎉 Выполнен!")

@dp.callback_query(F.data.startswith("cancel_"))
async def cb_cancel(cq: CallbackQuery):
    oid = int(cq.data.split("_")[1])
    if oid not in orders:
        await cq.answer("Заказ не найден")
        return
    orders[oid]['status'] = 'cancelled'
    save_orders()
    o = orders[oid]
    try:
        await bot.send_message(o['user_id'], f"❌ <b>Заказ #{oid} отменён.</b>")
    except: pass
    await cq.message.edit_text(cq.message.text + "\n\n❌ <b>ОТМЕНЁН</b>")
    await cq.answer("❌ Отменён")

@dp.callback_query(F.data.startswith("call_"))
async def cb_call(cq: CallbackQuery):
    oid = int(cq.data.split("_")[1])
    if oid not in orders:
        await cq.answer("Заказ не найден")
        return
    o = orders[oid]
    await cq.answer(f"📱 {o['phone']} | @{o['username']}", show_alert=True)

@dp.callback_query(F.data.startswith("client_cancel_"))
async def cb_client_cancel(cq: CallbackQuery):
    oid = int(cq.data.split("_")[2])
    if oid not in orders:
        await cq.answer("Заказ не найден")
        return
    o = orders[oid]
    if o['status'] in ['enroute', 'done', 'cancelled']:
        await cq.answer("Невозможно отменить на этом этапе", show_alert=True)
        return
    orders[oid]['status'] = 'cancelled'
    save_orders()
    await cq.message.edit_text("❌ Заказ отменён.")
    if ADMIN_ID:
        await bot.send_message(ADMIN_ID, f"❌ Клиент отменил заказ #{oid}")
    await cq.answer("❌ Отменён")
# ========== РАССЫЛКА ==========
# ========== СПИСОК ПОЛЬЗОВАТЕЛЕЙ ==========
@dp.message(Command("users"))
async def cmd_users(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    if not users:
        await msg.answer("Пользователей нет")
        return
    text = f"👥 Всего пользователей: {len(users)}\n\n"
    for uid, data in users.items():
        name = data.get('name', 'Неизвестно')
        phone = data.get('phone', 'нет')
        lang = data.get('lang', 'ru')
        username = data.get('username', 'нет')
        text += f"👤 {name}\n🆔 {uid}\n👤 @{username}\n📞 {phone}\n🌐 {lang}\n➖➖➖➖➖➖\n"
    await msg.answer(text)


@dp.message(Command("broadcast"))
async def cmd_broadcast(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    text = msg.text.replace("/broadcast", "").strip()
    if not text:
        await msg.answer("Напишите текст: /broadcast Ваше сообщение")
        return
    count = 0
    for uid in users:
        try:
            await bot.send_message(uid, text)
            count += 1
        except:
            pass
    await msg.answer(f"✅ Отправлено {count} пользователям")

# ========== ОЧИСТКА СТАРЫХ ЗАКАЗОВ ==========
async def cleanup_old_orders():
    """Раз в сутки удаляет завершённые/отменённые заказы старше недели."""
    while True:
        await asyncio.sleep(24 * 60 * 60)
        cutoff = datetime.now() - timedelta(days=ORDER_MAX_AGE_DAYS)
        to_delete = []
        for oid, o in orders.items():
            if o.get('status') in ('done', 'cancelled'):
                created = o.get('created_at')
                if not created:
                    continue
                try:
                    if datetime.fromisoformat(created) < cutoff:
                        to_delete.append(oid)
                except ValueError:
                    continue
        if to_delete:
            for oid in to_delete:
                orders.pop(oid, None)
            save_orders()
            logging.info(f"Очистка: удалено старых заказов — {len(to_delete)}")

# ========== ЗАПУСК ==========
async def main():
    logging.info("333 OIL Bot запущен!")
    asyncio.create_task(cleanup_old_orders())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
