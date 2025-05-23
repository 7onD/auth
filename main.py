import sqlite3
import imaplib
import email
import re
import logging
from datetime import datetime, timedelta
from pathlib import Path
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ConversationHandler
)
from bs4 import BeautifulSoup

# Конфигурация
DATABASE_NAME = "codes.db"
IMAP_SERVER = "imap.gmail.com"
EMAIL_ACCOUNT = "sd05102005@gmail.com"
EMAIL_PASSWORD = "zflb xrcu ljoj grlg"
BOT_TOKEN = "8148180139:AAEOIN_sNcGt8LHZw79zOCG3y8onE--Q1ks"
# 8148180139:AAEOIN_sNcGt8LHZw79zOCG3y8onE--Q1ks  // MAIN
# 7328709610:AAHp-b29RzAzXE_tJ-hmMdAASi0WuyBVXrk  // TEST
ADMIN_IDS = {985462027}

# Состояния
(
    ADD_CODE, DELETE_CODE, WIPE_CODE,
    START_SESSION, WAITING_FEEDBACK,
    WAITING_REPLY, SEND_MESSAGE_ID,
    SEND_MESSAGE_TEXT, CONFIRM_DELETE_ALL
) = range(9)

# Логи
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

from telegram.ext import CallbackContext
async def global_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return

    data = update.callback_query.data
    if data == "confirm_delete_all":
        await confirm_delete_all(update, context)
    elif data == "cancel_delete_all":
        await cancel_delete_all(update, context)


class CodeManager:
    def __init__(self):
        self.init_db()
        self.last_cleanup = datetime.min

    def init_db(self):
        with sqlite3.connect(DATABASE_NAME) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS codes (
                    code TEXT PRIMARY KEY,
                    created_at DATETIME NOT NULL,
                    used_at DATETIME
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    admin_id INTEGER,
                    reply TEXT,
                    created_at DATETIME NOT NULL,
                    status TEXT DEFAULT 'open'
                )
            ''')

    def add_code(self, code: str) -> bool:
        if not re.match(r'^\d{6}$', code):
            return False
        with sqlite3.connect(DATABASE_NAME) as conn:
            try:
                conn.execute(
                    "INSERT INTO codes (code, created_at) VALUES (?, ?)",
                    (code, datetime.now().isoformat())
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def delete_code(self, code: str) -> bool:
        with sqlite3.connect(DATABASE_NAME) as conn:
            cursor = conn.execute("DELETE FROM codes WHERE code = ?", (code,))
            return cursor.rowcount > 0

    def wipe_code(self, code: str) -> bool:
        with sqlite3.connect(DATABASE_NAME) as conn:
            cursor = conn.execute(
                "UPDATE codes SET used_at = NULL WHERE code = ?",
                (code,)
            )
            return cursor.rowcount > 0

    def validate_code(self, code: str) -> bool:
        with sqlite3.connect(DATABASE_NAME) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            try:
                cursor.execute("BEGIN TRANSACTION")
                cursor.execute("SELECT * FROM codes WHERE code = ?", (code,))
                code_entry = cursor.fetchone()
                if not code_entry:
                    return False
                now = datetime.now()
                used_at = datetime.fromisoformat(code_entry['used_at']) if code_entry['used_at'] else None
                if not used_at:
                    cursor.execute(
                        "UPDATE codes SET used_at = ? WHERE code = ?",
                        (now.isoformat(), code)
                    )
                    conn.commit()
                    return True
                else:
                    return (now - used_at) <= timedelta(minutes=1)
            except Exception as e:
                conn.rollback()
                logger.error(f"DB Error: {e}")
                return False

    def get_codes_with_status(self):
        with sqlite3.connect(DATABASE_NAME) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT code, created_at, used_at FROM codes")
            codes = []
            for row in cursor.fetchall():
                code = row['code']
                used_at = datetime.fromisoformat(row['used_at']) if row['used_at'] else None
                status = "✅" if used_at and (datetime.now() - used_at > timedelta(minutes=1)) else "🔄"
                time_info = ""
                if used_at:
                    delta = datetime.now() - used_at
                    time_info = " • " + self.humanize_time_delta(delta)
                codes.append(f"{status} {code}{time_info}")
            return codes

    @staticmethod
    def humanize_time_delta(delta: timedelta) -> str:
        total_seconds = int(delta.total_seconds())
        periods = [
            ('год', 'года', 'лет', 365*24*3600),
            ('месяц', 'месяца', 'месяцев', 30*24*3600),
            ('день', 'дня', 'дней', 24*3600),
            ('час', 'часа', 'часов', 3600),
            ('минуту', 'минуты', 'минут', 60)
        ]
        parts = []
        for unit_name, plural2, plural5, seconds in periods:
            if total_seconds >= seconds:
                value, total_seconds = divmod(total_seconds, seconds)
                if value == 1:
                    parts.append(f"{value} {unit_name}")
                elif 2 <= value <= 4:
                    parts.append(f"{value} {plural2}")
                else:
                    parts.append(f"{value} {plural5}")
        return " ".join(parts) + " назад" if parts else "только что"

    def add_feedback(self, user_id: int, message: str) -> int:
        with sqlite3.connect(DATABASE_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO feedback (user_id, message, created_at) VALUES (?, ?, ?)",
                (user_id, message, datetime.now().isoformat())
            )
            return cursor.lastrowid

    def add_reply(self, feedback_id: int, admin_id: int, reply: str) -> bool:
        with sqlite3.connect(DATABASE_NAME) as conn:
            cursor = conn.execute(
                "UPDATE feedback SET admin_id = ?, reply = ?, status = 'closed' WHERE id = ?",
                (admin_id, reply, feedback_id)
            )
            return cursor.rowcount > 0

    def get_open_requests(self):
        with sqlite3.connect(DATABASE_NAME) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM feedback WHERE status = 'open'")
            return cursor.fetchall()

def extract_verification_code(text: str) -> str | None:
    try:
        soup = BeautifulSoup(text, "html.parser")
        clean_text = soup.get_text(separator=" ", strip=True)
        clean_text = clean_text.replace("\xa0", " ").replace("\u200b", "")
        clean_text = re.sub(r"\s+", " ", clean_text)
        patterns = [
            r"(?<!\d)(?:\d[-\.\s]*?){5}\d(?!\d)",
            r"\b\d{6}\b",
            r"(?i)(?:код|code)[:\s]*?(\d{6})",
            r"(?:№|#)\s*?(\d{6})"
        ]
        for pattern in patterns:
            match = re.search(pattern, clean_text)
            if match:
                code = re.sub(r"[^\d]", "", match.group(0))
                if len(code) == 6:
                    return code
        return None
    except Exception as e:
        logger.error(f"Ошибка извлечения кода: {e}")
        return None

def get_email_code() -> str | None:
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        mail.select("inbox")
        _, data = mail.search(None, "ALL")
        latest_email_id = data[0].split()[-1]
        _, data = mail.fetch(latest_email_id, "(RFC822)")
        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)
        for part in msg.walk():
            content_type = part.get_content_type()
            charset = part.get_content_charset() or "utf-8"
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                text = payload.decode(charset, errors="replace")
                if code := extract_verification_code(text):
                    return code
            except Exception as e:
                logger.error(f"Ошибка декодирования: {e}")
                continue
        return None
    except Exception as e:
        logger.error(f"Ошибка почты: {e}")
        return None
    finally:
        try:
            mail.close()
            mail.logout()
        except:
            pass

def get_admin_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["📥 Добавить код", "❌ Удалить код"],
            ["🔄 Сбросить код", "📋 Список кодов"],
            ["📩 Открытые запросы", "🔑 Проверить доступ"],
            ["✉️ Отправить сообщение пользователю", "🧹 Удалить все сообщения"]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие"
    )

# Отправка сообщения по ID
async def send_message_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = int(update.message.text.strip())
        context.user_data["target_user_id"] = user_id
        await update.message.reply_text("Введите сообщение для отправки:")
        return SEND_MESSAGE_TEXT
    except ValueError:
        await update.message.reply_text("❌ Неверный ID. Введите числовой ID пользователя.")
        return SEND_MESSAGE_ID

async def send_message_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = context.user_data.get("target_user_id")
    text = update.message.text
    try:
        await context.bot.send_message(user_id, f"📩 Сообщение от администратора:\n{text}")
        await update.message.reply_text("✅ Сообщение отправлено!")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка отправки: {e}")
    return ConversationHandler.END

# Удаление всех сообщений
async def confirm_delete_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id in ADMIN_IDS:
        with sqlite3.connect(DATABASE_NAME) as conn:
            conn.execute("DELETE FROM feedback")
        await query.message.delete()
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="✅ Все сообщения удалены.",
            reply_markup=get_admin_keyboard()
        )
    else:
        await query.edit_message_text("⛔ У вас нет прав для этой операции.")

async def cancel_delete_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="❌ Отменено.",
        reply_markup=get_admin_keyboard()
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id in ADMIN_IDS:
        await update.message.reply_text("Панель администратора:", reply_markup=get_admin_keyboard())
    else:
        await update.message.reply_text(
            "Здравствуйте!\nВведите 6-значный код доступа, полученный после покупки:",
            reply_markup=ReplyKeyboardRemove()
        )
    return ConversationHandler.END

async def feedback_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Напишите ваше сообщение для администратора:")
    return WAITING_FEEDBACK

async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message.text
    db = CodeManager()
    feedback_id = db.add_feedback(user.id, message)
    for admin_id in ADMIN_IDS:
        keyboard = [[InlineKeyboardButton("📨 Ответить", callback_data=f"reply_{user.id}_{feedback_id}")]]
        await context.bot.send_message(
            admin_id,
            f"✉️ Новое обращение #{feedback_id}\n👤 Пользователь: {user.id}\n📄 Сообщение:\n{message}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    await update.message.reply_text("✅ Ваше сообщение отправлено администраторам!")
    return ConversationHandler.END

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, user_id, feedback_id = query.data.split('_')
    context.user_data['replying_to'] = (int(user_id), int(feedback_id))
    await query.message.reply_text("Введите ваш ответ:")
    return WAITING_REPLY

async def handle_reply_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user
    reply_text = update.message.text
    user_id, feedback_id = context.user_data['replying_to']
    db = CodeManager()
    if db.add_reply(feedback_id, admin.id, reply_text):
        await context.bot.send_message(user_id, f"📩 Ответ от администратора:\n{reply_text}")
        await update.message.reply_text("✅ Ответ отправлен пользователю!")
    else:
        await update.message.reply_text("❌ Ошибка отправки ответа!")
    del context.user_data['replying_to']
    return ConversationHandler.END

async def show_open_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = CodeManager()
    requests = db.get_open_requests()
    if not requests:
        await update.message.reply_text("❌ Нет открытых запросов")
        return
    for req in requests:
        keyboard = [[InlineKeyboardButton("📨 Ответить", callback_data=f"reply_{req['user_id']}_{req['id']}")]]
        await update.message.reply_text(
            f"✉️ Обращение #{req['id']}\n👤 Пользователь: {req['user_id']}\n📄 Сообщение:\n{req['message']}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    code_manager = CodeManager()

    if user.id in ADMIN_IDS:
        if text == "📥 Добавить код":
            await update.message.reply_text("Введите новый 6-значный код:")
            context.user_data["action"] = "add"
            return ADD_CODE
        elif text == "❌ Удалить код":
            await update.message.reply_text("Введите код для удаления:")
            context.user_data["action"] = "delete"
            return DELETE_CODE
        elif text == "🔄 Сбросить код":
            await update.message.reply_text("Введите код для сброса:")
            context.user_data["action"] = "wipe"
            return WIPE_CODE
        elif text == "📋 Список кодов":
            codes = code_manager.get_codes_with_status()
            response = "📜 Список кодов:\n" + "\n".join(codes) if codes else "📭 Нет кодов"
            await update.message.reply_text(response)
        elif text == "🔑 Проверить доступ":
            await update.message.reply_text("Введите код доступа:")
            context.user_data["action"] = "check"
            return START_SESSION
        elif text == "📩 Открытые запросы":
            await show_open_requests(update, context)
        elif text == "✉️ Отправить сообщение пользователю":
            await update.message.reply_text("Введите ID пользователя:")
            context.user_data["action"] = "send_message_id"
            return SEND_MESSAGE_ID
        elif text == "🧹 Удалить все сообщения":
            keyboard = ReplyKeyboardMarkup(
                [["✅ Подтвердить удаление", "❌ Отмена удаления"]],
                resize_keyboard=True
            )
            await update.message.reply_text(
                "Вы уверены, что хотите удалить все сообщения?",
                reply_markup=keyboard
            )
            return CONFIRM_DELETE_ALL

        elif text == "✅ Подтвердить удаление":
            with sqlite3.connect(DATABASE_NAME) as conn:
                conn.execute("DELETE FROM feedback")
            await update.message.reply_text(
                "✅ Все сообщения удалены.",
                reply_markup=get_admin_keyboard()
            )
            return ConversationHandler.END

        elif text == "❌ Отмена удаления":
            await update.message.reply_text(
                "❌ Удаление отменено.",
                reply_markup=get_admin_keyboard()
            )
            return ConversationHandler.END



        elif context.user_data.get("action") == "send_message_id":
            try:
                user_id = int(text.strip())
                context.user_data["target_user_id"] = user_id
                context.user_data["action"] = "send_message_text"
                await update.message.reply_text("Введите сообщение для отправки:")
                return SEND_MESSAGE_TEXT
            except ValueError:
                await update.message.reply_text("❌ Неверный ID. Введите числовой ID пользователя.")
                return SEND_MESSAGE_ID

        elif context.user_data.get("action") == "send_message_text":
            user_id = context.user_data.get("target_user_id")
            try:
                await context.bot.send_message(user_id, f"📩 Сообщение от администратора:\n{text}")
                await update.message.reply_text("✅ Сообщение отправлено!")
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка отправки: {e}")
            context.user_data.pop("action", None)
            context.user_data.pop("target_user_id", None)
            return ConversationHandler.END

        elif "action" in context.user_data:
            action = context.user_data.pop("action")
            code = text.strip()
            if action == "add":
                if code_manager.add_code(code):
                    await update.message.reply_text(f"✅ Код {code} добавлен")
                else:
                    await update.message.reply_text("❌ Ошибка добавления кода")
            elif action == "delete":
                if code_manager.delete_code(code):
                    await update.message.reply_text(f"✅ Код {code} удален")
                else:
                    await update.message.reply_text("❌ Код не найден")
            elif action == "wipe":
                if code_manager.wipe_code(code):
                    await update.message.reply_text(f"✅ Код {code} сброшен")
                else:
                    await update.message.reply_text("❌ Код не найден")
            elif action == "check":
                if code_manager.validate_code(code):
                    email_code = get_email_code()
                    if email_code:
                        await update.message.reply_text(f"🔑 Код из письма: {email_code}\n\nСпасибо за покупку!")
                    else:
                        await update.message.reply_text("❌ Код не найден в почте\nПожалуйста, попробуйте заново")
                else:
                    await update.message.reply_text("⛔ Неверный код или время доступа истекло")
            return ConversationHandler.END
    else:
        code = text.strip()
        if code_manager.validate_code(code):
            email_code = get_email_code()
            if email_code:
                await update.message.reply_text(f"🔑 Код из письма: {email_code}\n\nСпасибо за покупку!")
            else:
                await update.message.reply_text("❌ Код не найден в почте\nПожалуйста, попробуйте заново")
        else:
            await update.message.reply_text("⛔ Неверный код или время доступа истекло")
        return ConversationHandler.END

def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    main_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ADD_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)],
            DELETE_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)],
            WIPE_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)],
            START_SESSION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)],
            SEND_MESSAGE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_message_id)],
            SEND_MESSAGE_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_message_text)],
            CONFIRM_DELETE_ALL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)],

        },
        fallbacks=[]
    )

    feedback_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("feedback", feedback_start),
            CallbackQueryHandler(handle_admin_reply, pattern=r"^reply_\d+_\d+$")
        ],
        states={
            WAITING_FEEDBACK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_feedback)],
            WAITING_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reply_message)]
        },
        fallbacks=[]
    )

    application.add_handler(feedback_conv_handler)
    application.add_handler(main_conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(global_callback_handler))


    application.run_polling()

if __name__ == "__main__":
    if not Path(DATABASE_NAME).exists():
        CodeManager().init_db()
        logger.info("База данных инициализирована")
    main()
