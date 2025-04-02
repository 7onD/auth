import imaplib
import email
import re
from email.header import decode_header

# Конфигурация почтового ящика (замените на свои данные)
IMAP_SERVER = "imap.gmail.com"
EMAIL_ACCOUNT = "sd05102005@gmail.com"
EMAIL_PASSWORD = "cktr pnnk flpn mngr"
TARGET_CODE = "777"  # Код, который нужно проверить

def extract_verification_code(text):
    # Ищем шестизначный код в тексте
    match = re.search(r'\b\d{6}\b', text)
    return match.group(0) if match else None

def get_latest_email_code():
    try:
        # Подключаемся к серверу
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        mail.select("inbox")

        # Ищем последнее письмо
        result, data = mail.search(None, "ALL")
        if result != "OK":
            print("Письма не найдены")
            return None

        latest_email_id = data[0].split()[-1]
        result, data = mail.fetch(latest_email_id, "(RFC822)")
        if result != "OK":
            print("Ошибка получения письма")
            return None

        # Парсим письмо
        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)
        code = None

        # Обрабатываем части письма
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            
            if "attachment" not in content_disposition:
                try:
                    body = part.get_payload(decode=True).decode()
                except:
                    continue
                
                # Ищем код в текстовой части
                code = extract_verification_code(body)
                if code:
                    break

        mail.close()
        mail.logout()
        return code

    except Exception as e:
        print(f"Ошибка: {str(e)}")
        return None

def main():
    input_code = input("Введите код: ")
    
    if input_code == TARGET_CODE:
        print("Код верный. Проверяем почту...")
        verification_code = get_latest_email_code()
        
        if verification_code:
            print(f"Найден код в последнем письме: {verification_code}")
        else:
            print("Не удалось найти код в последнем письме")
    else:
        print("Неверный код")

if __name__ == "__main__":
    main()