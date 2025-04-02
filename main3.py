import sqlite3
import imaplib
import email
import re
import logging
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)
from bs4 import BeautifulSoup

# Конфигурация
DATABASE_NAME = "codes.db"
IMAP_SERVER = "imap.gmail.com"
EMAIL_ACCOUNT = "gptacc717@gmail.com"
EMAIL_PASSWORD = "dbrx xesm oklv dvoa"
BOT_TOKEN = "8148180139:AAEOIN_sNcGt8LHZw79zOCG3y8onE--Q1ks"
ADMIN_IDS = {985462027}  # ID администраторов

# Настройка логов
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

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
            cursor = conn.execute(
                "DELETE FROM codes WHERE code = ?", 
                (code,)
            )
            return cursor.rowcount > 0

    def wipe_code(self, code: str) -> bool:
        with sqlite3.connect(DATABASE_NAME) as conn:
            cursor = conn.execute(
                "UPDATE codes SET used_at = NULL WHERE code = ?",
                (code,)
            )
            return cursor.rowcount > 0

    def validate_code(self, code: str) -> bool:
        self._cleanup_old_codes()
        
        with sqlite3.connect(DATABASE_NAME) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            try:
                cursor.execute("BEGIN TRANSACTION")
                cursor.execute(
                    "SELECT * FROM codes WHERE code = ?", 
                    (code,)
                )
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
            cursor = conn.execute("SELECT code, used_at FROM codes")
            codes = []
            for row in cursor.fetchall():
                code = row['code']
                used_at = datetime.fromisoformat(row['used_at']) if row['used_at'] else None
                status = "✅" if used_at and (datetime.now() - used_at > timedelta(minutes=1)) else "🔄"
                codes.append(f"{status} {code}")
            return codes

    def _cleanup_old_codes(self):
        if datetime.now() - self.last_cleanup < timedelta(hours=1):
            return
        
        cutoff = (datetime.now() - timedelta(days=1)).isoformat()
        with sqlite3.connect(DATABASE_NAME) as conn:
            conn.execute(
                "DELETE FROM codes WHERE created_at < ?",
                (cutoff,)
            )
        self.last_cleanup = datetime.now()

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
            ["🔑 Проверить доступ"]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие"
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if user.id in ADMIN_IDS:
        await update.message.reply_text(
            "Панель администратора:",
            reply_markup=get_admin_keyboard()
        )
    else:
        await update.message.reply_text(
            "Введите 6-значный код доступа:",
            reply_markup=ReplyKeyboardRemove()
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    code_manager = CodeManager()

    if user.id in ADMIN_IDS:
        if text == "📥 Добавить код":
            await update.message.reply_text("Введите новый 6-значный код:")
            context.user_data["action"] = "add"

        elif text == "❌ Удалить код":
            await update.message.reply_text("Введите код для удаления:")
            context.user_data["action"] = "delete"

        elif text == "🔄 Сбросить код":
            await update.message.reply_text("Введите код для сброса:")
            context.user_data["action"] = "wipe"

        elif text == "📋 Список кодов":
            codes = code_manager.get_codes_with_status()
            response = "📜 Список кодов:\n" + "\n".join(codes) if codes else "📭 Нет кодов"
            await update.message.reply_text(response)

        elif text == "🔑 Проверить доступ":
            await update.message.reply_text("Введите код доступа:")
            context.user_data["action"] = "check"

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
                        await update.message.reply_text(f"🔑 Код из письма: {email_code}")
                    else:
                        await update.message.reply_text("❌ Код не найден в почте")
                else:
                    await update.message.reply_text("⛔ Неверный код или время доступа истекло")

    else:
        code = text.strip()
        if code_manager.validate_code(code):
            email_code = get_email_code()
            if email_code:
                await update.message.reply_text(f"🔑 Код из письма: {email_code}")
            else:
                await update.message.reply_text("❌ Код не найден в почте")
        else:
            await update.message.reply_text("⛔ Неверный код или время доступа истекло")

def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()

if __name__ == "__main__":
    if not Path(DATABASE_NAME).exists():
        CodeManager().init_db()
        logger.info("База данных инициализирована")
    
    main()