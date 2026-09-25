import os
import sys
import json
import time
import signal
import logging
import argparse
import re
import requests

# Глобальная переменная для хранения уникальных номеров 
# (позволяет сохранить данные при прерывании скрипта через Ctrl+C)
collected_numbers = set()
output_filename = "phones.txt"

def setup_logger():
    """Настройка логирования в консоль и файл app.log"""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # Файловый хэндлер
    fh = logging.FileHandler('app.log', encoding='utf-8')
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # Консольный хэндлер
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

def save_data():
    """Сохранение собранных данных в файл"""
    if not collected_numbers:
        logging.info("Нет данных для сохранения.")
        return

    logging.info(f"Начато сохранение {len(collected_numbers)} уникальных номеров в файл {output_filename}...")
    try:
        with open(output_filename, 'w', encoding='utf-8') as f:
            for number in sorted(collected_numbers):
                f.write(f"{number}\n")
        logging.info("Данные успешно сохранены.")
    except Exception as e:
        logging.error(f"Ошибка при сохранении файла: {e}")

def signal_handler(sig, frame):
    """Обработчик прерывания (Ctrl+C)"""
    logging.warning("\nПолучен сигнал прерывания (Ctrl+C). Остановка сбора...")
    save_data()
    sys.exit(0)

def load_config():
    """Загрузка конфигурации из JSON и CLI с приоритетом CLI"""
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

    # Переопределение параметров из CLI
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

def fetch_places(api_key, city, categories):
    """Основной цикл запросов к API 2GIS"""
    base_url = "https://catalog.api.2gis.com/3.0/items"
    
    for category in categories:
        logging.info(f"--- Начало сбора по категории: '{category}' в городе '{city}' ---")
        query = f"{category} {city}"
        page = 1
        page_size = 50 # Максимум для 2GIS API

        while True:
            params = {
                'q': query,
                'key': api_key,
                'page': page,
                'page_size': page_size,
                'fields': 'items.contact_groups' # Критически важно для получения контактов
            }
            
            try:
                response = requests.get(base_url, params=params, timeout=15)
                
                if response.status_code == 403:
                    logging.error("Ошибка 403: Неверный API ключ или превышен лимит запросов.")
                    return
                elif response.status_code != 200:
                    logging.warning(f"Код {response.status_code}: {response.text}")
                    break
                
                data = response.json()
                result = data.get('result', {})
                items = result.get('items', [])
                total = result.get('total', 0)
                
                if not items:
                    logging.info("Больше нет заведений в этой категории.")
                    break
                
                for item in items:
                    contact_groups = item.get('contact_groups', [])
                    for group in contact_groups:
                        contacts = group.get('contacts', [])
                        for contact in contacts:
                            if contact.get('type') == 'phone':
                                raw_phone = contact.get('value', '')
                                # Очистка номера: оставляем только цифры
                                clean_phone = re.sub(r'\D', '', raw_phone)
                                if clean_phone:
                                    collected_numbers.add(clean_phone)
                
                logging.info(f"Категория '{category}' | Страница {page} | Собрано уник. номеров: {len(collected_numbers)}")
                
                # Проверка пагинации
                if page * page_size >= total:
                    break
                
                page += 1
                time.sleep(0.5) # Задержка для предотвращения блокировки (Rate Limit)

            except requests.exceptions.RequestException as e:
                logging.error(f"Сетевая ошибка при запросе страницы {page}: {e}")
                time.sleep(5) # Пауза перед возможным продолжением

def main():
    signal.signal(signal.SIGINT, signal_handler)
    setup_logger()
    
    api_key, city, categories = load_config()
    logging.info(f"Запуск скрипта. Город: {city}. Категории: {', '.join(categories)}")
    
    fetch_places(api_key, city, categories)
    save_data()

if __name__ == "__main__":
    main()