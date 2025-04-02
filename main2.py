import sqlite3
import imaplib
import email
import re
from datetime import datetime, timedelta
from pathlib import Path

DATABASE_NAME = "codes.db"
IMAP_SERVER = "imap.gmail.com"
EMAIL_ACCOUNT = "sd05102005@gmail.com"
EMAIL_PASSWORD = "cktr pnnk flpn mngr"

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
                print(f"Ошибка БД: {e}")
                return False

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
    match = re.search(r'\b\d{6}\b', text)
    return match.group(0) if match else None

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
            if part.get_content_type() == "text/plain":
                body = part.get_payload(decode=True).decode(errors='ignore')
                if code := extract_verification_code(body):
                    return code
        return None
    except Exception as e:
        print(f"Ошибка почты: {e}")
        return None
    finally:
        try:
            mail.close()
            mail.logout()
        except:
            pass

def start_session(code_manager: CodeManager):
    while True:
        user_code = input("Введите код доступа: ").strip()
        
        if not user_code:
            print("Пожалуйста, введите код!")
            continue
            
        if code_manager.validate_code(user_code):
            print("Доступ разрешен! Проверяем почту...")
            if email_code := get_email_code():
                print(f"Код из письма: {email_code}")
            else:
                print("Код не найден в последнем письме")
            break
        else:
            print("Неверный код или время доступа истекло")

def show_help():
    print("\nДоступные команды:")
    print("  add code <6-цифр>    - Добавить новый код")
    print("  delete code <6-цифр> - Удалить код")
    print("  code wipe <6-цифр>   - Сбросить использование кода")
    print("  start                - Начать проверку кода")
    print("  list                 - Показать все коды")
    print("  help                 - Показать справку")
    print("  exit                 - Выйти\n")

def main():
    code_manager = CodeManager()
    print("Терминал безопасного доступа")
    show_help()

    while True:
        try:
            command = input("> ").strip().lower()
            if not command:
                continue

            parts = command.split()
            cmd = parts[0]

            if cmd == "add" and len(parts) >= 3 and parts[1] == "code":
                code = parts[2]
                if code_manager.add_code(code):
                    print(f"Код {code} добавлен")
                else:
                    print("Некорректный код или он уже существует")

            elif cmd == "delete" and len(parts) >= 3 and parts[1] == "code":
                code = parts[2]
                if code_manager.delete_code(code):
                    print(f"Код {code} удален")
                else:
                    print("Код не найден")

            elif cmd == "code" and len(parts) >= 3 and parts[1] == "wipe":
                code = parts[2]
                if code_manager.wipe_code(code):
                    print(f"Код {code} сброшен")
                else:
                    print("Код не найден")

            elif cmd == "start":
                start_session(code_manager)

            elif cmd == "list":
                with sqlite3.connect(DATABASE_NAME) as conn:
                    cursor = conn.execute("SELECT code FROM codes")
                    codes = [row[0] for row in cursor.fetchall()]
                    print("Зарегистрированные коды:", ", ".join(codes) if codes else "Нет кодов")

            elif cmd == "help":
                show_help()

            elif cmd == "exit":
                print("Выход...")
                break

            else:
                print("Неизвестная команда. Введите 'help' для справки")

        except Exception as e:
            print(f"Ошибка: {e}")

if __name__ == "__main__":
    if not Path(DATABASE_NAME).exists():
        CodeManager().init_db()
        print("База данных инициализирована")
        
    main()