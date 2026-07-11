"""
333 OIL — Telegram Bot + Mini App
pip install aiogram==3.13.0 aiohttp==3.9.5
"""

import asyncio
import json
import logging
import os
import aiohttp
from aiohttp import web
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, WebAppInfo,
    InlineKeyboardMarkup, InlineKeyboardButton,
    KeyboardButton, ReplyKeyboardMarkup,
    ReplyKeyboardRemove, WebAppData, ChatMemberUpdated
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
ADMIN_IDS = set()
for _x in os.environ.get("ADMIN_IDS", "").split(","):
    _x = _x.strip()
    if _x.isdigit():
        ADMIN_IDS.add(int(_x))
ADMIN_PIN = os.environ.get("ADMIN_PIN", "33333")

# Cloudflare Workers AI — для отчётов
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")
CF_MODEL = "@cf/meta/llama-3.1-8b-instruct"
REPORT_HOUR = int(os.environ.get("REPORT_HOUR", "23"))  # час по Ташкенту для авто-отчёта
TASHKENT_TZ = ZoneInfo("Asia/Tashkent")

# ========== ИНИЦИАЛИЗАЦИЯ ==========
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# ========== ХРАНИЛИЩЕ ==========
DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
USERS_FILE = os.path.join(DATA_DIR, "users.json")
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")
BASE_FILE = os.path.join(DATA_DIR, "base_location.json")
PIN_FILE = os.path.join(DATA_DIR, "driver_pin.json")
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

def get_lang(uid):
    return users.get(uid, {}).get('lang', 'ru')

orders = load_orders()
order_counter = [max([o['id'] for o in orders.values()], default=0)]
admin_users = set()

def is_admin(uid):
    return uid == ADMIN_ID or uid in ADMIN_IDS or uid in admin_users
waiting_pin = set()
fuel_stock = {   # Остатки топлива в литрах
    'АИ-92': 500,
    'АИ-95': 500,
    'АИ-98': 300,
    'Дизель': 400
}

FUEL_META_FILE = os.path.join(DATA_DIR, "fuel_meta.json")
DEFAULT_FUEL_META = {
    'АИ-92': {'price': 12500, 'desc': 'Стандарт • Для большинства авто'},
    'АИ-95': {'price': 13800, 'desc': 'Улучшенный • Иномарки'},
    'АИ-98': {'price': 15500, 'desc': 'Премиум • Спортивные авто'},
    'Дизель': {'price': 11900, 'desc': 'Для дизельных двигателей'},
}

def load_fuel_meta():
    merged = {k: dict(v) for k, v in DEFAULT_FUEL_META.items()}
    if os.path.exists(FUEL_META_FILE):
        with open(FUEL_META_FILE, "r") as f:
            saved = json.load(f)
        for k, v in saved.items():
            if k in merged:
                merged[k].update(v)
    return merged

def save_fuel_meta():
    with open(FUEL_META_FILE, "w") as f:
        json.dump(fuel_meta, f, ensure_ascii=False)

fuel_meta = load_fuel_meta()

# ========== ПУБЛИЧНЫЙ API (для мини-аппа) ==========
# Мини-апп на GitHub Pages не имеет доступа к памяти бота напрямую,
# поэтому бот открывает один простой read-only эндпоинт с остатками и базой.
PORT = int(os.environ.get("PORT", "8080"))
def load_base_location():
    if os.path.exists(BASE_FILE):
        with open(BASE_FILE, "r") as f:
            d = json.load(f)
            return d.get("lat"), d.get("lng"), d.get("updated_at")
    return (
        float(os.environ.get("BASE_LAT", "41.2995")),
        float(os.environ.get("BASE_LNG", "69.2401")),
        None
    )

def save_base_location(lat, lng):
    with open(BASE_FILE, "w") as f:
        json.dump({"lat": lat, "lng": lng, "updated_at": datetime.now().isoformat()}, f)

BASE_LAT, BASE_LNG, BASE_UPDATED_AT = load_base_location()

async def handle_stock(request):
    return web.json_response(
        {
            "stock": fuel_stock,
            "base_lat": BASE_LAT,
            "base_lng": BASE_LNG,
            "fuel_meta": fuel_meta,
        },
        headers={"Access-Control-Allow-Origin": "*"}
    )

async def handle_myinfo(request):
    """Отдаёт клиенту только ЕГО СОБСТВЕННЫЙ номер и историю заказов — без секрета,
    так как каждый получает данные только по своему же Telegram ID."""
    try:
        uid = int(request.query.get("uid", "0"))
    except ValueError:
        uid = 0
    user = users.get(uid, {})
    phone = user.get('phone', '')

    my_orders = [o for o in orders.values() if o.get('user_id') == uid]
    my_orders.sort(key=lambda o: o.get('created_at') or '', reverse=True)

    def brief(o):
        return {
            "fuel": o.get('fuel'),
            "liters": o.get('liters'),
            "total": o.get('total'),
            "status": o.get('status'),
            "created_at": o.get('created_at'),
        }

    return web.json_response(
        {
            "phone": phone,
            "last_order": brief(my_orders[0]) if my_orders else None,
            "history": [brief(o) for o in my_orders[:10]],
        },
        headers={"Access-Control-Allow-Origin": "*"}
    )

ADMIN_API_SECRET = os.environ.get("ADMIN_API_SECRET", "")

def _check_secret(request):
    return bool(ADMIN_API_SECRET) and request.query.get("secret", "") == ADMIN_API_SECRET

async def handle_orders(request):
    if not _check_secret(request):
        return web.json_response({"error": "unauthorized"}, status=401, headers={"Access-Control-Allow-Origin": "*"})
    active = [o for o in orders.values() if o.get('status') in ('new', 'accepted', 'enroute')]
    active.sort(key=lambda o: o['id'])
    result = [{
        "id": o['id'],
        "status": o['status'],
        "user_name": o.get('user_name', '—'),
        "phone": o.get('phone', '—'),
        "fuel": o.get('fuel'),
        "liters": o.get('liters'),
        "address": o.get('address') or o.get('location') or '—',
        "payment": o.get('payment'),
        "total": o.get('total'),
        "lat": o.get('lat'),
        "lng": o.get('lng'),
    } for o in active]
    return web.json_response(result, headers={"Access-Control-Allow-Origin": "*"})

async def handle_finance(request):
    if not _check_secret(request):
        return web.json_response({"error": "unauthorized"}, status=401, headers={"Access-Control-Allow-Origin": "*"})

    now_tk = datetime.now(timezone.utc).astimezone(TASHKENT_TZ)
    today = now_tk.date()
    month_start = today.replace(day=1)

    today_done, month_done = [], []
    by_payment = {}
    for o in orders.values():
        if o.get('status') != 'done':
            continue
        created = o.get('created_at')
        if not created:
            continue
        try:
            dt_tk = datetime.fromisoformat(created).replace(tzinfo=timezone.utc).astimezone(TASHKENT_TZ)
        except ValueError:
            continue
        if dt_tk.date() == today:
            today_done.append(o)
        if dt_tk.date() >= month_start:
            month_done.append(o)
            pay = o.get('payment') or 'Другое'
            by_payment[pay] = by_payment.get(pay, 0) + o.get('total', 0)

    return web.json_response({
        "today_revenue": sum(o.get('total', 0) for o in today_done),
        "today_orders": len(today_done),
        "month_revenue": sum(o.get('total', 0) for o in month_done),
        "month_orders": len(month_done),
        "by_payment": by_payment,
    }, headers={"Access-Control-Allow-Origin": "*"})

def load_driver_pin():
    if os.path.exists(PIN_FILE):
        with open(PIN_FILE, "r") as f:
            return json.load(f).get("pin", ADMIN_PIN)
    return ADMIN_PIN

def save_driver_pin(pin):
    with open(PIN_FILE, "w") as f:
        json.dump({"pin": pin}, f)

DRIVER_PIN = load_driver_pin()

async def handle_verify_pin(request):
    pin = request.query.get("pin", "")
    return web.json_response({"ok": pin == DRIVER_PIN}, headers={"Access-Control-Allow-Origin": "*"})

async def handle_change_pin(request):
    global DRIVER_PIN
    current = request.query.get("current", "")
    new = request.query.get("new", "")
    if current != DRIVER_PIN:
        return web.json_response({"ok": False, "error": "wrong_current"}, headers={"Access-Control-Allow-Origin": "*"})
    if not (new.isdigit() and len(new) == 5):
        return web.json_response({"ok": False, "error": "invalid_new"}, headers={"Access-Control-Allow-Origin": "*"})
    DRIVER_PIN = new
    save_driver_pin(new)
    return web.json_response({"ok": True}, headers={"Access-Control-Allow-Origin": "*"})

async def handle_update_stock(request):
    if not _check_secret(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401, headers={"Access-Control-Allow-Origin": "*"})
    fuel = request.query.get("fuel", "")
    amount = request.query.get("amount", "")
    if fuel not in fuel_stock:
        return web.json_response({"ok": False, "error": "unknown_fuel"}, headers={"Access-Control-Allow-Origin": "*"})
    try:
        fuel_stock[fuel] = int(amount)
    except ValueError:
        return web.json_response({"ok": False, "error": "invalid_amount"}, headers={"Access-Control-Allow-Origin": "*"})
    return web.json_response({"ok": True}, headers={"Access-Control-Allow-Origin": "*"})

async def handle_update_price(request):
    if not _check_secret(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401, headers={"Access-Control-Allow-Origin": "*"})
    fuel = request.query.get("fuel", "")
    price = request.query.get("price", "")
    if fuel not in fuel_meta:
        return web.json_response({"ok": False, "error": "unknown_fuel"}, headers={"Access-Control-Allow-Origin": "*"})
    try:
        fuel_meta[fuel]['price'] = int(price)
        save_fuel_meta()
    except ValueError:
        return web.json_response({"ok": False, "error": "invalid_price"}, headers={"Access-Control-Allow-Origin": "*"})
    return web.json_response({"ok": True}, headers={"Access-Control-Allow-Origin": "*"})

async def handle_update_desc(request):
    if not _check_secret(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401, headers={"Access-Control-Allow-Origin": "*"})
    fuel = request.query.get("fuel", "")
    desc = request.query.get("desc", "")
    if fuel not in fuel_meta:
        return web.json_response({"ok": False, "error": "unknown_fuel"}, headers={"Access-Control-Allow-Origin": "*"})
    fuel_meta[fuel]['desc'] = desc
    save_fuel_meta()
    return web.json_response({"ok": True}, headers={"Access-Control-Allow-Origin": "*"})

async def handle_broadcast(request):
    if not _check_secret(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401, headers={"Access-Control-Allow-Origin": "*"})
    text = request.query.get("text", "").strip()
    if not text:
        return web.json_response({"ok": False, "error": "empty_text"}, headers={"Access-Control-Allow-Origin": "*"})
    count = 0
    for uid, data in list(users.items()):
        if data.get('blocked'):
            continue
        try:
            await bot.send_message(uid, text)
            count += 1
        except Exception:
            users[uid]['blocked'] = True
    save_users()
    return web.json_response({"ok": True, "sent": count}, headers={"Access-Control-Allow-Origin": "*"})

async def handle_report(request):
    if not _check_secret(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401, headers={"Access-Control-Allow-Origin": "*"})
    try:
        text = await generate_daily_report()
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, headers={"Access-Control-Allow-Origin": "*"})
    return web.json_response({"ok": True, "report": text}, headers={"Access-Control-Allow-Origin": "*"})

async def handle_users(request):
    if not _check_secret(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401, headers={"Access-Control-Allow-Origin": "*"})
    active = [d for d in users.values() if not d.get('blocked')]
    blocked_count = len(users) - len(active)
    result = [{
        "name": d.get('name', '—'),
        "username": d.get('username', 'нет'),
        "phone": d.get('phone', '—'),
        "lang": d.get('lang', 'ru'),
    } for d in active]
    return web.json_response({"active": result, "blocked_count": blocked_count}, headers={"Access-Control-Allow-Origin": "*"})

async def run_web_server():
    app = web.Application()
    app.router.add_get('/stock', handle_stock)
    app.router.add_get('/myinfo', handle_myinfo)
    app.router.add_get('/orders', handle_orders)
    app.router.add_get('/finance', handle_finance)
    app.router.add_get('/verify-pin', handle_verify_pin)
    app.router.add_get('/change-pin', handle_change_pin)
    app.router.add_get('/update-stock', handle_update_stock)
    app.router.add_get('/update-price', handle_update_price)
    app.router.add_get('/update-desc', handle_update_desc)
    app.router.add_get('/broadcast', handle_broadcast)
    app.router.add_get('/report', handle_report)
    app.router.add_get('/users', handle_users)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logging.info(f"HTTP API запущен на порту {PORT} (GET /stock, /orders, /finance) — база: {BASE_LAT}, {BASE_LNG}")

# ========== FSM ==========
class RegStates(StatesGroup):
    waiting_phone = State()

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
        rows.insert(0, [
            InlineKeyboardButton(
                text="🗺️ Google",
                url=f"https://www.google.com/maps/dir/?api=1&destination={lat},{lng}&travelmode=driving"
            ),
            InlineKeyboardButton(
                text="🟡 Yandex",
                url=f"https://yandex.ru/maps/?pt={lng},{lat}&z=16&l=map"
            )
        ])
        rows.insert(1, [
            InlineKeyboardButton(
                text="🟢 2ГИС",
                url=f"https://2gis.uz/routeSearch/rsType/car/to/{lng},{lat}"
            ),
            InlineKeyboardButton(
                text="🍎 Apple",
                url=f"https://maps.apple.com/?daddr={lat},{lng}&dirflg=d"
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_client_track(order_id, lang='ru'):
    text = "❌ Bekor qilish" if lang == 'uz' else "❌ Отменить заказ"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=f"client_cancel_{order_id}")]
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
        # Подтягиваем/обновляем имя и username на случай, если их не было
        # (старые записи) или пользователь сменил их в Telegram
        users[uid]['name'] = msg.from_user.full_name
        users[uid]['username'] = msg.from_user.username or 'нет'
        save_users()

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

    # Принимаем ТОЛЬКО номер через кнопку "Поделиться номером",
    # и только если это номер именно этого пользователя (Telegram уже его подтвердил)
    if not msg.contact or msg.contact.user_id != uid:
        if lang == 'uz':
            await msg.answer(
                "📱 Iltimos, pastdagi tugma orqali raqamingizni yuboring.\nQo'lda kiritib bo'lmaydi.",
                reply_markup=kb_share_phone(lang)
            )
        else:
            await msg.answer(
                "📱 Пожалуйста, отправьте номер через кнопку ниже.\nВручную ввести нельзя.",
                reply_markup=kb_share_phone(lang)
            )
        return

    phone = msg.contact.phone_number
    if not phone.startswith('+'):
        phone = '+' + phone

    users[uid]['phone'] = phone
    users[uid]['verified'] = True
    save_users()
    await state.clear()

    if lang == 'uz':
        text = (
            f"✅ <b>Ro'yxatdan o'tdingiz!</b>\n\n"
            f"📱 Raqam: {phone}\n\n"
            "⛽ Endi yoqilg'i buyurtma qilishingiz mumkin 👇"
        )
    else:
        text = (
            f"✅ <b>Регистрация прошла успешно!</b>\n\n"
            f"📱 Номер: {phone}\n\n"
            "⛽ Теперь вы можете заказать топливо 👇"
        )
    await msg.answer(text, reply_markup=kb_main(lang))

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
        fuel = data.get('fuel', '—')
        liters = data.get('liters', 0)
        available = fuel_stock.get(fuel, 0)

        if liters <= 0 or liters > available:
            if lang == 'uz':
                await msg.answer(
                    f"❌ Yetarli {fuel} yo'q.\n"
                    f"Hozir mavjud: {available} litr.\n"
                    "Iltimos, kamroq miqdor tanlang."
                )
            else:
                await msg.answer(
                    f"❌ Недостаточно {fuel} в наличии.\n"
                    f"Сейчас доступно: {available} л.\n"
                    "Выберите меньшее количество."
                )
            return

        # Резервируем топливо сразу, чтобы следующие заказы видели актуальный остаток
        fuel_stock[fuel] -= liters

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
            'fuel': fuel,
            'liters': liters,
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
        await msg.answer(client_text, reply_markup=kb_client_track(oid, lang))

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
        if is_admin(uid):
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
    if not is_admin(msg.from_user.id):
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
    if not is_admin(msg.from_user.id):
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
    if not is_admin(msg.from_user.id):
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
    if not is_admin(msg.from_user.id):
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

@dp.message(F.location)
async def admin_update_location(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    global BASE_LAT, BASE_LNG, BASE_UPDATED_AT
    BASE_LAT = msg.location.latitude
    BASE_LNG = msg.location.longitude
    BASE_UPDATED_AT = datetime.now().isoformat()
    save_base_location(BASE_LAT, BASE_LNG)
    await msg.answer(
        f"📍 <b>Локация обновлена!</b>\n"
        f"Теперь расстояние до клиентов в мини-аппе считается от этой точки.\n\n"
        f"Координаты: {BASE_LAT:.5f}, {BASE_LNG:.5f}"
    )

@dp.message(F.text == "🛢️ Остатки топлива")
@dp.message(Command("stock"))
async def admin_stock(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    lines = [f"{k}: <b>{v} л</b>" for k, v in fuel_stock.items()]
    text = "🛢️ <b>Остатки топлива:</b>\n\n" + "\n".join(lines)
    text += "\n\nДля изменения отправьте (можно несколько строк сразу):\n<code>stock АИ-92 350</code>"
    if BASE_UPDATED_AT:
        text += f"\n\n📍 Точка отсчёта обновлена: {BASE_UPDATED_AT[:16].replace('T', ' ')}"
    else:
        text += "\n\n📍 Точка отсчёта: по умолчанию (отправьте геолокацию, чтобы обновить)"
    await msg.answer(text)

@dp.message(F.text.startswith("stock "))
async def update_stock(msg: Message):
    if not is_admin(msg.from_user.id):
        return

    updated = []
    errors = []

    for line in msg.text.strip().split('\n'):
        line = line.strip()
        if not line.lower().startswith('stock '):
            continue
        parts = line.split()
        if len(parts) < 3:
            errors.append(f"❌ Формат: <code>stock АИ-92 350</code> ({line})")
            continue
        fuel = parts[1]
        amount = parts[2]
        try:
            amount = int(amount)
            if fuel in fuel_stock:
                fuel_stock[fuel] = amount
                updated.append(f"✅ {fuel}: {amount} л")
            else:
                errors.append(f"❌ Неизвестное топливо: {fuel} (доступно: {', '.join(fuel_stock.keys())})")
        except ValueError:
            errors.append(f"❌ Неверное число в строке: {line}")

    if not updated and not errors:
        await msg.answer("❌ Формат: <code>stock АИ-92 350</code>\nМожно несколько строк сразу.")
        return

    await msg.answer("\n".join(updated + errors))

@dp.message(F.text.startswith("price "))
async def update_price(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.split(None, 2)
    if len(parts) < 3:
        await msg.answer("❌ Формат: <code>price АИ-92 15000</code>")
        return
    fuel, amount = parts[1], parts[2]
    if fuel not in fuel_meta:
        await msg.answer(f"❌ Неизвестное топливо: {fuel} (доступно: {', '.join(fuel_meta.keys())})")
        return
    try:
        fuel_meta[fuel]['price'] = int(amount)
        save_fuel_meta()
        await msg.answer(f"✅ {fuel}: цена {int(amount):,} сум/л".replace(",", " "))
    except ValueError:
        await msg.answer("❌ Неверное число")

@dp.message(F.text.startswith("desc "))
async def update_desc(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.split(None, 2)
    if len(parts) < 3:
        await msg.answer("❌ Формат: <code>desc АИ-92 Ваш текст описания</code>")
        return
    fuel, text = parts[1], parts[2]
    if fuel not in fuel_meta:
        await msg.answer(f"❌ Неизвестное топливо: {fuel} (доступно: {', '.join(fuel_meta.keys())})")
        return
    fuel_meta[fuel]['desc'] = text
    save_fuel_meta()
    await msg.answer(f"✅ {fuel}: описание обновлено — «{text}»")

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
    lang = get_lang(o['user_id'])
    try:
        if lang == 'uz':
            await bot.send_message(o['user_id'],
                f"✅ <b>Buyurtma #{oid} qabul qilindi!</b>\n🚗 Haydovchi tez orada chiqadi.")
        else:
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
    lang = get_lang(o['user_id'])
    try:
        if lang == 'uz':
            await bot.send_message(o['user_id'],
                f"🚗 <b>Haydovchi yo'lda!</b>\nBuyurtma #{oid} — sizga kelyapmiz!")
        else:
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
    save_orders()
    lang = get_lang(o['user_id'])
    try:
        if lang == 'uz':
            await bot.send_message(o['user_id'],
                f"⛽ <b>Buyurtma #{oid} bajarildi!</b>\n✅ {o['fuel']} — {o['liters']} litr quyildi\n💰 {o['total']:,} so'm\n\nRahmat! 🙏")
        else:
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
    o = orders[oid]
    if o['fuel'] in fuel_stock:
        fuel_stock[o['fuel']] += o.get('liters', 0)
    orders[oid]['status'] = 'cancelled'
    save_orders()
    lang = get_lang(o['user_id'])
    try:
        if lang == 'uz':
            await bot.send_message(o['user_id'], f"❌ <b>Buyurtma #{oid} bekor qilindi.</b>")
        else:
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
    if o['fuel'] in fuel_stock:
        fuel_stock[o['fuel']] += o.get('liters', 0)
    orders[oid]['status'] = 'cancelled'
    save_orders()
    await cq.message.edit_text("❌ Заказ отменён.")
    if ADMIN_ID:
        await bot.send_message(ADMIN_ID, f"❌ Клиент отменил заказ #{oid}")
    await cq.answer("❌ Отменён")
# ========== РАССЫЛКА ==========
# ========== СПИСОК ПОЛЬЗОВАТЕЛЕЙ ==========
@dp.my_chat_member()
async def track_block_status(event: ChatMemberUpdated):
    """Telegram сообщает боту, когда пользователь его блокирует/разблокирует."""
    uid = event.chat.id
    if uid not in users:
        return
    new_status = event.new_chat_member.status
    if new_status == 'kicked':
        users[uid]['blocked'] = True
        save_users()
    elif new_status == 'member':
        users[uid]['blocked'] = False
        save_users()

@dp.message(Command("users"))
async def cmd_users(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    active = {uid: d for uid, d in users.items() if not d.get('blocked')}
    blocked_count = len(users) - len(active)
    if not active:
        await msg.answer("Активных пользователей нет" + (f" (заблокировали бота: {blocked_count})" if blocked_count else ""))
        return
    text = f"👥 Активных пользователей: {len(active)}\n"
    if blocked_count:
        text += f"🚫 Заблокировали бота: {blocked_count}\n"
    text += "\n"
    for uid, data in active.items():
        name = data.get('name', 'Неизвестно')
        phone = data.get('phone', 'нет')
        lang = data.get('lang', 'ru')
        username = data.get('username', 'нет')
        text += f"👤 {name}\n🆔 {uid}\n👤 @{username}\n📞 {phone}\n🌐 {lang}\n➖➖➖➖➖➖\n"
    await msg.answer(text)


@dp.message(Command("broadcast"))
async def cmd_broadcast(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    text = msg.text.replace("/broadcast", "").strip()
    if not text:
        await msg.answer("Напишите текст: /broadcast Ваше сообщение")
        return
    count = 0
    for uid, data in users.items():
        if data.get('blocked'):
            continue
        try:
            await bot.send_message(uid, text)
            count += 1
        except Exception:
            users[uid]['blocked'] = True
    save_users()
    await msg.answer(f"✅ Отправлено {count} пользователям")

# ========== AI-ОТЧЁТЫ (Cloudflare Workers AI) ==========

def collect_daily_stats():
    """Собирает статистику за сегодня (по времени Ташкента)."""
    now_tk = datetime.now(timezone.utc).astimezone(TASHKENT_TZ)
    today = now_tk.date()

    today_orders = []
    for o in orders.values():
        created = o.get('created_at')
        if not created:
            continue
        try:
            dt_tk = datetime.fromisoformat(created).replace(tzinfo=timezone.utc).astimezone(TASHKENT_TZ)
        except ValueError:
            continue
        if dt_tk.date() == today:
            today_orders.append(o)

    done = [o for o in today_orders if o.get('status') == 'done']
    cancelled = [o for o in today_orders if o.get('status') == 'cancelled']
    active = [o for o in today_orders if o.get('status') in ('new', 'accepted', 'enroute')]

    revenue = sum(o.get('total', 0) for o in done)
    fuel_breakdown = {}
    for o in done:
        fuel_breakdown[o['fuel']] = fuel_breakdown.get(o['fuel'], 0) + o.get('liters', 0)

    return {
        'date': today.strftime('%d.%m.%Y'),
        'total_orders': len(today_orders),
        'done': len(done),
        'cancelled': len(cancelled),
        'active': len(active),
        'revenue': revenue,
        'fuel_breakdown': fuel_breakdown,
        'stock': dict(fuel_stock),
    }

async def ask_workers_ai(prompt: str) -> str:
    """Отправляет промпт в Cloudflare Workers AI и возвращает текстовый ответ."""
    if not CF_ACCOUNT_ID or not CF_API_TOKEN:
        raise RuntimeError("CF_ACCOUNT_ID / CF_API_TOKEN не заданы")

    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}
    payload = {
        "messages": [
            {"role": "system", "content": (
                "Ты аналитик службы доставки топлива 333 OIL в Ташкенте. "
                "Пиши очень кратко (3-5 предложений), по-деловому, на русском языке, "
                "без вступлений и общих фраз — только суть и конкретика по цифрам."
            )},
            {"role": "user", "content": prompt}
        ]
    }
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            data = await resp.json()
            if not data.get("success"):
                raise RuntimeError(f"Workers AI error: {data.get('errors')}")
            return data["result"]["response"].strip()

async def generate_daily_report() -> str:
    """Формирует полный текст отчёта: цифры + AI-сводка."""
    s = collect_daily_stats()

    stock_lines = "\n".join(f"  • {f}: {v} л" for f, v in s['stock'].items())
    low_stock = [f for f, v in s['stock'].items() if v < 100]

    if s['total_orders'] == 0:
        base = (
            f"📊 <b>Отчёт за {s['date']}</b>\n\n"
            f"Заказов сегодня не было.\n\n"
            f"⛽ Остатки:\n{stock_lines}"
        )
        if low_stock:
            base += f"\n\n⚠️ Заканчивается: {', '.join(low_stock)}"
        return base

    fuel_lines = "\n".join(f"- {f}: {l} л" for f, l in s['fuel_breakdown'].items()) or "нет"
    prompt = (
        f"Данные за {s['date']}:\n"
        f"Всего заказов: {s['total_orders']}\n"
        f"Выполнено: {s['done']}, отменено: {s['cancelled']}, в процессе: {s['active']}\n"
        f"Выручка: {s['revenue']} сум\n"
        f"Продано топлива:\n{fuel_lines}\n"
        f"Остатки на складе:\n{stock_lines}\n\n"
        f"Дай короткую сводку дня для владельца. Если какого-то топлива меньше 100 литров — "
        f"обязательно отметь, что пора пополнить запас."
    )

    try:
        ai_summary = await ask_workers_ai(prompt)
    except Exception as e:
        logging.error(f"Workers AI недоступен: {e}")
        ai_summary = "⚠️ AI-сводка временно недоступна, но цифры ниже точные."

    text = (
        f"📊 <b>Отчёт за {s['date']}</b>\n\n"
        f"{ai_summary}\n\n"
        f"—\n"
        f"📦 Заказов: {s['total_orders']} (✅{s['done']} · ❌{s['cancelled']} · ⏳{s['active']})\n"
        f"💰 Выручка: {s['revenue']:,} сум\n"
        f"⛽ Остатки:\n{stock_lines}"
    )
    return text

async def daily_report_task():
    """Раз в сутки, в REPORT_HOUR по Ташкенту, шлёт отчёт всем админам."""
    while True:
        now = datetime.now(TASHKENT_TZ)
        next_run = now.replace(hour=REPORT_HOUR, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())

        recipients = {ADMIN_ID} | ADMIN_IDS if ADMIN_ID else set(ADMIN_IDS)
        if recipients:
            try:
                report = await generate_daily_report()
                for rid in recipients:
                    try:
                        await bot.send_message(rid, report)
                    except Exception as e:
                        logging.error(f"Ошибка отправки отчёта {rid}: {e}")
            except Exception as e:
                logging.error(f"Ошибка формирования отчёта: {e}")

@dp.message(Command("report"))
async def cmd_report(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    wait_msg = await msg.answer("⏳ Собираю отчёт...")
    try:
        report = await generate_daily_report()
        await wait_msg.edit_text(report)
    except Exception as e:
        await wait_msg.edit_text(f"❌ Ошибка: {e}")

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
    logging.info(f"Данные хранятся в: {os.path.abspath(DATA_DIR)} (пользователей: {len(users)}, заказов: {len(orders)})")
    asyncio.create_task(cleanup_old_orders())
    asyncio.create_task(daily_report_task())
    asyncio.create_task(run_web_server())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
