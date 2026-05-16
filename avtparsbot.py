import asyncio
import re
import sqlite3
from hydrogram import Client, filters
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

# --- НАСТРОЙКИ ---
API_ID =   # С сайта my.telegram.org
API_HASH = ""  # С сайта my.telegram.org
BOT_TOKEN = ""  # От @BotFather


app = Client("universal_avito_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Словарь для хранения активных задач мониторинга {user_id: {query: max_price}}
active_monitors = {}


# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ---
def init_db():
    with sqlite3.connect('avito_ads.db', timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ads (id TEXT PRIMARY KEY)
        ''')
        conn.commit()
    print("💾 База данных SQLite успешно проверена и готова к работе.")


init_db()


# --- ФУНКЦИЯ ПРОВЕРКИ И ЗАПИСИ В БАЗУ ---
def check_new_ad(ad_id):
    ad_id = str(ad_id).strip()
    if not ad_id:
        return False

    with sqlite3.connect('avito_ads.db', timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM ads WHERE id = ?', (ad_id,))
        result = cursor.fetchone()

        if result is not None:
            return False  # Это старое объявление, мы его уже видели

        try:
            cursor.execute('INSERT INTO ads (id) VALUES (?)', (ad_id,))
            conn.commit()  # Жестко сохраняем на диск ноута
            print(f"📌 База запомнила новый ID: {ad_id}")
            return True
        except sqlite3.IntegrityError:
            return False


# --- УНИВЕРСАЛЬНАЯ ФУНКЦИЯ ПАРСИНГА ---
async def get_avito_data(query, max_price):
    results = []
    async with async_playwright() as p:
        user_data = r"C:\Users\user\Desktop\bot_profile"
        browser_context = await p.chromium.launch_persistent_context(
            user_data,
            headless=False,  # Видим окно, чтобы если что помочь боту с капчей
            args=["--disable-blink-features=AutomationControlled"]
        )

        page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()

        # Маскировка stealth
        try:
            from playwright_stealth import stealth_async
            await stealth_async(page)
        except ImportError:
            pass

        # Формируем ссылку
        url = f"https://avito.ru/?q={query.replace(' ', '+')}"

        try:
            await page.goto(url)

            # Ожидание появления блоков объявлений
            try:
                await page.wait_for_selector("div[class*='iva-item-root']", timeout=15000)
            except:
                await page.wait_for_selector("[data-marker='item']", timeout=5000)

            # Человеческое поведение: скроллим страницу
            await page.mouse.wheel(0, 800)
            await asyncio.sleep(5)

            items = await page.query_selector_all("div[class*='iva-item-root']")
            if not items:
                items = await page.query_selector_all("[data-marker='item']")

            print(f"📦 [{query}] Найдено блоков на экране: {len(items)}")

            for item in items[:10]:  # Смотрим топ-10 свежих объявлений
                try:
                    title_el = await item.query_selector("[data-marker='item-title']")
                    if not title_el:
                        title_el = await item.query_selector("h3")
                    title = await title_el.inner_text() if title_el else "Без названия"

                    price_el = await item.query_selector("[data-marker='item-price']")
                    if not price_el:
                        price_el = await item.query_selector("span[class*='price-price']")
                    price_text = await price_el.inner_text() if price_el else "Цена не указана"

                    # Извлекаем чистую цифру цены для фильтрации
                    price_digits = int(re.sub(r'\D', '', price_text)) if price_text != "Цена не указана" else 0

                    link_el = await item.query_selector("a")
                    href = await link_el.get_attribute("href") if link_el else ""
                    ad_link = "avito.ru" + href

                    # Железобетонное извлечение ID из ссылки (вычищаем только цифры)
                    digits_only = re.sub(r'\D', '', href)
                    # --- ЖЕСТКИЙ СИСТЕМНЫЙ ID ---
                    # Забираем внутренний ID Авито прямо из атрибутов блока товара
                    ad_id = await item.get_attribute("data-id")

                    # Если data-id не найден, пробуем вытащить его из маркера ссылки
                    if not ad_id:
                        item_id_attr = await item.get_attribute("data-item-id")
                        ad_id = item_id_attr if item_id_attr else ""

                    # Если вообще ничего не нашлось (рекламный баннер), берем чистые цифры ссылки
                    if not ad_id:
                        digits_only = re.sub(r'\D', '', href)
                        ad_id = digits_only if digits_only else href.strip()

                    if not ad_id:
                        continue

                    print(f"🔎 Проверяем реальный ID Авито: {ad_id} | {title}")

                    if not ad_id:
                        continue

                    print(f"🔎 Проверяем в базе ID: {ad_id} для товара: {title}")

                    # Разделение логики: при max_price == 0 (команда /find) база данных отключается,
                    # чтобы ты всегда видел результаты. При /monitor база включается.
                    is_new = True if max_price == 0 else check_new_ad(ad_id)

                    if is_new:
                        if max_price == 0 or (0 < price_digits <= max_price):
                            results.append(
                                f"✨ **НАЙДЕН ТОВАР!**\n\n"
                                f"📦 {title}\n"
                                f"💰 `{price_text}`\n"
                                f"🔗 [Открыть на Авито]({ad_link})"
                            )
                        else:
                            print(f"Пропуск по цене ({price_text}): {title}")
                    else:
                        print(f"Старое объявление в базе: {title}")
                except Exception as e:
                    print(f"Ошибка элемента: {e}")
                    continue
        except Exception as e:
            print(f"Ошибка Playwright: {e}")
        finally:
            await browser_context.close()

    return results


# --- КОМАНДА /find (РАЗОВЫЙ ПОИСК БЕЗ ФИЛЬТРА БАЗЫ) ---
@app.on_message(filters.command("find") & filters.private)
async def find_command(client, message):
    query = message.text.replace("/find", "").strip()
    if not query:
        await message.reply_text("❌ Напиши запрос, например:\n`/find rtx 3060` или `/find белый корпус`")
        return

    wait_msg = await message.reply_text(f"🔍 Ищу **{query}** на Авито Челябинск...")
    data = await get_avito_data(query, max_price=0)

    if data:
        # Чтобы не спамить личку, склеиваем топ-5 результатов в одно сообщение
        await wait_msg.edit_text("\n\n".join(data[:5]), disable_web_page_preview=True)
    else:
        await wait_msg.edit_text("🤫 На первой странице ничего не нашлось. Возможно, стоит пройти капчу.")


# --- КОМАНДА /monitor (УНИВЕРСАЛЬНЫЙ СНАЙПЕР-МОНИТОРИНГ) ---
@app.on_message(filters.command("monitor") & filters.private)
async def monitor_command(client, message):
    try:
        args = message.text.replace("/monitor", "").strip().split(",")
        query = args[0].strip()
        max_price = int(args[1].strip())
    except:
        await message.reply_text(
            "❌ Неправильный формат! Напиши вот так:\n`/monitor rtx 3060, 23000` или `/monitor белый корпус, 4000`")
        return

    user_id = message.from_user.id
    if user_id not in active_monitors:
        active_monitors[user_id] = {}

    active_monitors[user_id][query] = max_price
    await message.reply_text(
        f"🚀 Снайпер-мониторинг для **{query}** запущен!\nИщу объявления до `{max_price} ₽`. Проверка каждые 10 минут.")

    while query in active_monitors.get(user_id, {}):
        try:
            data = await get_avito_data(query, max_price)
            if data:
                for card_msg in data:
                    await message.reply_text(card_msg)
        except Exception as e:
            print(f"Ошибка мониторинга {query}: {e}")

        await asyncio.sleep(600)  # Проверка раз в 10 минут


# --- КОМАНДА /stop (ОСТАНОВКА МОНИТОРИНГА) ---
@app.on_message(filters.command("stop") & filters.private)
async def stop_command(client, message):
    user_id = message.from_user.id
    if user_id in active_monitors:
        active_monitors[user_id].clear()
        await message.reply_text("🛑 Все твои авто-мониторинги остановлены.")
    else:
        await message.reply_text("🤔 У тебя не было активных задач для слежения.")


if __name__ == "__main__":
    print("🚀 Снайпер-бот успешно запущен в PyCharm!")
    app.run()
