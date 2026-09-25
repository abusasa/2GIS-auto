import os
import sys
import json
import time
import signal
import logging
import argparse
import re
import requests

collected_numbers = set()
output_filename = "phones.txt"

MAX_RETRIES = 5
BASE_BACKOFF = 2
MAX_PAGES_PER_CATEGORY = 500
CHECKPOINT_EVERY_PAGES = 1
MIN_PHONE_DIGITS = 10
MAX_PHONE_DIGITS = 15

def setup_logger():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    fh = logging.FileHandler('app.log', encoding='utf-8')
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

def save_data(quiet=False):
    if not collected_numbers:
        if not quiet:
            logging.info("Нет данных для сохранения.")
        return

    if not quiet:
        logging.info(f"Начато сохранение {len(collected_numbers)} уникальных номеров в файл {output_filename}...")

    tmp_filename = f"{output_filename}.tmp"
    try:
        with open(tmp_filename, 'w', encoding='utf-8') as f:
            for number in sorted(collected_numbers):
                f.write(f"{number}\n")
        os.replace(tmp_filename, output_filename)
        if not quiet:
            logging.info("Данные успешно сохранены.")
    except Exception as e:
        logging.error(f"Ошибка при сохранении файла: {e}")

def signal_handler(sig, frame):
    logging.warning("\nПолучен сигнал прерывания (Ctrl+C). Остановка сбора...")
    save_data()
    sys.exit(0)

def load_config():
    global output_filename

    parser = argparse.ArgumentParser(description="Сбор номеров телефонов из 2GIS API (Catalog API 3.0)")
    parser.add_argument("-c", "--config", type=str, default="config.json", help="Путь к конфигурационному файлу")
    parser.add_argument("-k", "--api_key", type=str, help="API ключ 2GIS")
    parser.add_argument("-t", "--city", type=str, help="Город для поиска")
    parser.add_argument("-o", "--output", type=str, help="Имя выходного файла")
    parser.add_argument("-cat", "--categories", type=str, nargs='+', help="Список категорий (через пробел)")

    args = parser.parse_args()

    config = {}
    if os.path.exists(args.config):
        try:
            with open(args.config, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception as e:
            logging.error(f"Ошибка чтения {args.config}: {e}")
            sys.exit(1)

    api_key = args.api_key or config.get("api_key")
    city = args.city or config.get("city")
    categories = args.categories or config.get("categories", [])
    output_filename = args.output or config.get("output_file", "phones.txt")

    if not api_key:
        logging.error("API-ключ не задан! Укажите его в config.json или через флаг -k.")
        sys.exit(1)
    if not city or not categories:
        logging.error("Не задан город или категории поиска.")
        sys.exit(1)

    return api_key, city, categories

def clean_phone(raw_phone):
    digits = re.sub(r'\D', '', raw_phone)
    if MIN_PHONE_DIGITS <= len(digits) <= MAX_PHONE_DIGITS:
        return digits
    return None

def fetch_places(api_key, city, categories):
    base_url = "https://catalog.api.2gis.com/3.0/items"

    for category in categories:
        logging.info(f"--- Начало сбора по категории: '{category}' в городе '{city}' ---")
        query = f"{category} {city}"
        page = 1
        page_size = 10

        try:
            while True:
                if page > MAX_PAGES_PER_CATEGORY:
                    logging.warning(f"Достигнут предел в {MAX_PAGES_PER_CATEGORY} страниц. Остановка.")
                    break

                params = {
                    'q': query,
                    'key': api_key,
                    'page': page,
                    'page_size': page_size,
                    'fields': 'items.contact_groups'
                }

                response = None
                attempt = 0

                while attempt <= MAX_RETRIES:
                    try:
                        response = requests.get(base_url, params=params, timeout=15)
                    except requests.exceptions.RequestException as e:
                        attempt += 1
                        if attempt > MAX_RETRIES:
                            logging.error(f"Сетевая ошибка: {e}. Превышено число попыток.")
                            response = None
                            break
                        wait = BASE_BACKOFF * (2 ** (attempt - 1))
                        time.sleep(wait)
                        continue

                    if response.status_code == 403:
                        logging.error("Ошибка 403: неверный API-ключ или превышен лимит запросов.")
                        return

                    if response.status_code == 429:
                        attempt += 1
                        if attempt > MAX_RETRIES:
                            response = None
                            break
                        retry_after = response.headers.get('Retry-After')
                        wait = float(retry_after) if retry_after and retry_after.isdigit() else BASE_BACKOFF * (2 ** (attempt - 1))
                        time.sleep(wait)
                        continue

                    if response.status_code >= 500:
                        attempt += 1
                        if attempt > MAX_RETRIES:
                            response = None
                            break
                        wait = BASE_BACKOFF * (2 ** (attempt - 1))
                        time.sleep(wait)
                        continue

                    if response.status_code != 200:
                        logging.warning(f"Код {response.status_code}: {response.text[:300]}")
                        response = None
                        break

                    break

                if response is None:
                    break

                try:
                    data = response.json()
                except ValueError as e:
                    logging.error(f"Не удалось разобрать JSON-ответ: {e}")
                    break
                
                meta = data.get('meta', {})
                if meta and meta.get('code') != 200:
                    error_msg = meta.get('error', {}).get('message', '')
                    # Мягкая обработка лимита страниц для демо-ключей
                    if "Length of parameter 'page' should be from 1 to 5" in error_msg:
                        logging.warning("Достигнут лимит API-ключа (максимум 5 страниц). Сбор по этой категории завершен.")
                        break
                    else:
                        logging.error(f"ОШИБКА ОТ 2GIS: {meta}")
                        break

                result = data.get('result', {})
                items = result.get('items', [])
                total = result.get('total', 0)

                if not items:
                    logging.warning(f"Заведений нет. Полный ответ сервера: {data}")
                    break

                # --- ВЫВОД ПЕРВОГО ЗАВЕДЕНИЯ ДЛЯ ПРОВЕРКИ НАЛИЧИЯ КОНТАКТОВ ---
                if page == 1 and items:
                    logging.info(f"Структура данных от 2GIS (проверка наличия contact_groups):\n{json.dumps(items[0], indent=2, ensure_ascii=False)}")
                # -------------------------------------------------------------

                for item in items:
                    contact_groups = item.get('contact_groups', [])
                    for group in contact_groups:
                        contacts = group.get('contacts', [])
                        for contact in contacts:
                            if contact.get('type') == 'phone':
                                raw_phone = contact.get('value', '')
                                clean = clean_phone(raw_phone)
                                if clean:
                                    collected_numbers.add(clean)

                logging.info(f"Категория '{category}' | Страница {page} | Собрано уник. номеров: {len(collected_numbers)}")

                if page % CHECKPOINT_EVERY_PAGES == 0:
                    save_data(quiet=True)

                if page * page_size >= total:
                    break

                page += 1
                time.sleep(0.5)

        except Exception as e:
            logging.error(f"Непредвиденная ошибка при обработке категории '{category}': {e}")
            save_data(quiet=True)
            continue
def main():
    signal.signal(signal.SIGINT, signal_handler)
    setup_logger()

    api_key, city, categories = load_config()
    logging.info(f"Запуск скрипта. Город: {city}. Категории: {', '.join(categories)}")

    try:
        fetch_places(api_key, city, categories)
    except Exception as e:
        logging.error(f"Критическая ошибка в основном цикле: {e}")
    finally:
        save_data()

if __name__ == "__main__":
    main()