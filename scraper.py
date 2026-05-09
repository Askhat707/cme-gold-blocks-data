# scraper.py — рабочий оригинал + авто-дата, дозапись, фильтр экспирации, expiry_date, бэкап
import os, time, hashlib, pickle, re, shutil
from datetime import datetime, date, timedelta
import pandas as pd
import pytz
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

# ========== НАСТРОЙКИ ==========
yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
URL = (
    "https://www.cmegroup.com/clearing/operations-and-deliveries/"
    "accepted-trade-types/block-data.html"
    f"#tradeDate={yesterday}&subGroups=35&exchanges=COMEX&foi=O"
)
TABLE_WAIT_TIMEOUT = 45
SESSION_FILE = "cme_session.pkl"
CSV_FILE = "data.csv"

CT_TZ = pytz.timezone("US/Central")
TARGET_TZ = pytz.timezone("Asia/Almaty")  # UTC+5

# ========== КАЛЕНДАРЬ ЭКСПИРАЦИЙ (Gold Options) ==========
EXPIRY_DATES = {
    "OGM6": "2026-05-26",
    "OGN6": "2026-06-25",
    "OGQ6": "2026-07-28",
    "OGU6": "2026-08-26",
    "OGV6": "2026-09-24",
    "OGX6": "2026-10-27",
    "OGZ6": "2026-11-24",
    "OGG7": "2027-01-26",
    "OGH7": "2027-02-23",
    "OGJ7": "2027-03-24",
    "OGF7": "2026-12-28",
    "OGK7": "2027-04-27",
    "OGM7": "2027-05-25",
    "OGN7": "2027-06-24",
    "OGQ7": "2027-07-27",
    "OGU7": "2027-08-26",
    "OGV7": "2027-09-27",
    "OGX7": "2027-10-26",
    "OGZ7": "2027-11-23",
    "OGF8": "2027-12-28",
    "OGG8": "2028-01-26",
    "OGM8": "2028-05-25",
    "OGZ8": "2028-11-27",
    "OGM9": "2029-05-24",
    "G1MK6": "2026-05-04",
    "G2MK6": "2026-05-11",
    "G3MK6": "2026-05-18",
    "G1MM6": "2026-06-01",
    "G4TJ6": "2026-04-28",
    "G1TK6": "2026-05-05",
    "G2TK6": "2026-05-12",
    "G3TK6": "2026-05-19",
    "G5WJ6": "2026-04-29",
    "G1WK6": "2026-05-06",
    "G2WK6": "2026-05-13",
    "G3WK6": "2026-05-20",
    "G5RJ6": "2026-04-30",
    "G1RK6": "2026-05-07",
    "G2RK6": "2026-05-14",
    "G3RK6": "2026-05-21",
    "OG1K6": "2026-05-01",
    "OG2K6": "2026-05-08",
    "OG3K6": "2026-05-15",
    "OG4K6": "2026-05-22",
}

# ========== ДРАЙВЕР ==========
def create_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,720")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-background-networking")
    opts.add_argument("--disable-sync")
    opts.add_argument("--disable-translate")
    opts.add_argument("--disable-application-cache")
    opts.add_argument("--disable-client-side-phishing-detection")
    opts.add_argument("--disable-default-apps")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--disable-logging")
    opts.add_argument("--log-level=3")
    opts.add_argument("--silent")
    opts.add_argument("--disk-cache-size=1")
    opts.add_argument("--media-cache-size=1")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    )
    opts.add_argument(f"user-agent={ua}")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
        'source': '''
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
        '''
    })
    return driver

def load_session(driver):
    if not os.path.exists(SESSION_FILE):
        return False
    with open(SESSION_FILE, "rb") as f:
        session = pickle.load(f)
    cookies = session.get("cookies", [])
    cdp_cookies = []
    for c in cookies:
        cdp_cookie = {
            'name': c.get('name'),
            'value': c.get('value'),
            'domain': c.get('domain'),
            'path': c.get('path', '/'),
            'secure': c.get('secure', False),
            'httpOnly': c.get('httpOnly', False),
        }
        if 'expiry' in c and c['expiry']:
            cdp_cookie['expires'] = c['expiry']
        cdp_cookies.append(cdp_cookie)
    try:
        driver.execute_cdp_cmd('Storage.setCookies', {'cookies': cdp_cookies})
    except:
        for c in cookies:
            try:
                driver.add_cookie(c)
            except:
                pass
    driver.refresh()
    time.sleep(2)
    local_storage_str = session.get("local_storage", "{}")
    session_storage_str = session.get("session_storage", "{}")
    driver.execute_script("""
        const ls = arguments[0];
        const ss = arguments[1];
        try {
            const localObj = JSON.parse(ls);
            Object.keys(localObj).forEach(key => {
                window.localStorage.setItem(key, localObj[key]);
            });
            const sessionObj = JSON.parse(ss);
            Object.keys(sessionObj).forEach(key => {
                window.sessionStorage.setItem(key, sessionObj[key]);
            });
        } catch(e) { console.error(e); }
    """, local_storage_str, session_storage_str)
    driver.refresh()
    time.sleep(5)
    return True

def wait_for_table(driver, timeout=TABLE_WAIT_TIMEOUT):
    for attempt in range(3):
        try:
            WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "table.block-trades-table"))
            )
            time.sleep(2)
            return True
        except TimeoutException:
            driver.refresh()
    return False

# ========== ПАРСИНГ ДАТЫ ==========
def parse_trade_date(driver):
    try:
        spans = driver.find_elements(By.CSS_SELECTOR, "span.button-text")
        for span in spans:
            text = span.text.strip()
            match = re.search(r'\d{1,2}\s+\w{3}\s+\d{4}', text)
            if match:
                date_obj = datetime.strptime(match.group(), "%d %b %Y")
                return date_obj.strftime("%Y-%m-%d")
    except:
        pass
    try:
        current_url = driver.current_url
        m = re.search(r'tradeDate=(\d{4}-\d{2}-\d{2})', current_url)
        if m:
            return m.group(1)
    except:
        pass
    return ""

# ========== КОНВЕРТАЦИЯ ВРЕМЕНИ CT -> UTC+5 ==========
def convert_time_to_utc5(time_str, trade_date_str):
    if not trade_date_str:
        return time_str
    try:
        dt_naive = datetime.strptime(time_str, "%I:%M:%S %p")
        trade_date = datetime.strptime(trade_date_str, "%Y-%m-%d").date()
        ct_time = CT_TZ.localize(datetime.combine(trade_date, dt_naive.time()))
        target_time = ct_time.astimezone(TARGET_TZ)
        return target_time.strftime("%H:%M:%S")
    except:
        return time_str

# ========== ГЕНЕРАЦИЯ УНИКАЛЬНОГО TRADE ID ==========
def generate_trade_id(trade_date, time_utc5, trade_type, legs):
    sorted_legs = sorted(
        [(lg.get("symbol",""), lg.get("strike",""), lg.get("position","")) for lg in legs]
    )
    raw = f"{trade_date}|{time_utc5}|{trade_type}|{sorted_legs}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]

# ========== ПАРСИНГ ТАБЛИЦЫ ==========
def parse_all_gold_options(driver):
    trade_date = parse_trade_date(driver)
    records = []

    try:
        table = driver.find_element(By.CSS_SELECTOR, "table.block-trades-table")
    except NoSuchElementException:
        print("❌ Таблица не найдена")
        return records

    rows = table.find_elements(By.TAG_NAME, "tr")
    print(f"Найдено строк в таблице: {len(rows)}")
    current_time = None
    current_type = None
    current_group_legs = []
    trades = []

    for row in rows:
        cells = [c.text.strip() for c in row.find_elements(By.TAG_NAME, "td")]
        if not cells:
            continue

        first_cell = cells[0] if cells else ""
        if re.match(r'^\d{1,2}:\d{2}:\d{2}\s*[AP]M$', first_cell):
            if current_group_legs:
                trades.append({
                    "time": current_time,
                    "type": current_type,
                    "legs": current_group_legs
                })
            current_time = convert_time_to_utc5(first_cell, trade_date)
            current_type = cells[1] if len(cells) > 1 else ""
            current_group_legs = []
            leg = parse_leg_row(cells, is_first_row_of_group=True)
            if leg:
                current_group_legs.append(leg)
        else:
            leg = parse_leg_row(cells, is_first_row_of_group=False)
            if leg:
                current_group_legs.append(leg)

    if current_group_legs:
        trades.append({
            "time": current_time,
            "type": current_type,
            "legs": current_group_legs
        })

    print(f"Групп сделок до фильтрации: {len(trades)}")
    today = date.today()
    for trade in trades:
        if not trade["legs"]:
            continue
        # Проверяем, не истекли ли опционы в сделке
        skip = False
        for leg in trade["legs"]:
            sym = leg.get("symbol", "")
            expiry_str = EXPIRY_DATES.get(sym)
            if expiry_str:
                try:
                    expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                    if expiry_date < today:
                        skip = True
                        print(f"⛔ Пропущена сделка с {sym} (экспирация {expiry_str})")
                        break
                except:
                    pass
        if skip:
            continue

        trade_id = generate_trade_id(trade_date, trade["time"], trade["type"], trade["legs"])
        for leg in trade["legs"]:
            breakeven = 0.0
            if leg["option_type"] in ("call", "put"):
                try:
                    strike_val = float(leg["strike"][1:])
                    premium = float(leg["price"])
                    if leg["option_type"] == "call":
                        breakeven = round(strike_val + premium, 2)
                    else:
                        breakeven = round(strike_val - premium, 2)
                except:
                    breakeven = 0.0

            sym = leg.get("symbol", "")
            expiry_date_str = EXPIRY_DATES.get(sym, "")

            records.append({
                "trade_id": trade_id,
                "trade_date": trade_date,
                "time_utc5": trade["time"],
                "type": trade["type"],
                "product_name": leg.get("product_name", ""),
                "symbol": sym,
                "option_type": leg.get("option_type", ""),
                "strike": leg.get("strike", ""),
                "position": leg.get("position", ""),
                "price": leg.get("price", 0.0),
                "quantity": leg.get("quantity", 1),
                "breakeven": breakeven,
                "expiry_date": expiry_date_str,
            })
    print(f"Записей после фильтрации: {len(records)}")
    return records

def parse_leg_row(cells, is_first_row_of_group):
    try:
        if is_first_row_of_group:
            if len(cells) < 9:
                return None
            product = cells[2]
            symbol = cells[3]
            qty_str = cells[5]
            strike_str = cells[6]
            b_s = cells[7].capitalize()
            price_str = cells[8]
        else:
            if len(cells) < 6:
                return None
            product = cells[0]
            symbol = cells[1]
            if len(cells) >= 7 and cells[2].strip() != "":
                qty_str = cells[3]
                strike_str = cells[4]
                b_s = cells[5].capitalize()
                price_str = cells[6]
            else:
                qty_str = cells[2]
                strike_str = cells[3]
                b_s = cells[4].capitalize()
                price_str = cells[5]

        quantity = int(qty_str) if qty_str.isdigit() else 1
        price = float(price_str) if re.match(r'^-?\d+(\.\d+)?$', price_str) else 0.0

        product_lower = product.lower()
        if "futures" in product_lower:
            option_type = "futures"
            strike = ""
        else:
            option_type = ""
            strike = strike_str
            if strike_str and (strike_str[0] in ('C', 'P')):
                option_type = "call" if strike_str[0] == 'C' else "put"
            else:
                option_type = "futures"
                strike = ""

        return {
            "product_name": product,
            "symbol": symbol,
            "option_type": option_type,
            "strike": strike,
            "position": b_s,
            "price": price,
            "quantity": quantity
        }
    except (ValueError, IndexError):
        return None

# ========== ГЛАВНАЯ ФУНКЦИЯ ==========
def main():
    print("Запуск скрейпера CME Block Trades...")
    print("Дата для запроса:", URL.split("tradeDate=")[1].split("&")[0])
    driver = create_driver()
    try:
        driver.get(URL)
        time.sleep(5)
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
            )
            btn.click()
        except:
            pass

        if load_session(driver):
            print("Сессия загружена")
        else:
            print("Файл сессии отсутствует, продолжаем без него...")

        if not wait_for_table(driver):
            print("❌ Таблица не загрузилась (возможно, нет данных за выбранную дату)")
            new_records = []
        else:
            new_records = parse_all_gold_options(driver)

        # ========== 1. Резервное копирование ==========
        if os.path.exists(CSV_FILE):
            backup_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"data_backup_{backup_timestamp}.csv"
            shutil.copy2(CSV_FILE, backup_name)
            print(f"✅ Резервная копия сохранена: {backup_name}")

        # ========== 2. Дозапись в CSV ==========
        cols_order = [
            "trade_id", "trade_date", "time_utc5", "type", "product_name",
            "symbol", "option_type", "strike", "position", "quantity",
            "breakeven", "price", "expiry_date"
        ]
        if os.path.exists(CSV_FILE):
            df_old = pd.read_csv(CSV_FILE)
            for col in cols_order:
                if col not in df_old.columns:
                    df_old[col] = ""
            df_old = df_old[cols_order]
        else:
            df_old = pd.DataFrame(columns=cols_order)

        if new_records:
            df_new = pd.DataFrame(new_records)[cols_order]
            df_combined = pd.concat([df_old, df_new], ignore_index=True)
            df_combined.drop_duplicates(subset=["trade_id", "symbol", "position"], inplace=True)
            print(f"✅ Добавлено {len(df_new)} записей")
        else:
            df_combined = df_old
            print("ℹ️ Нет новых записей")

        # ========== 3. Фильтрация просроченных сделок ==========
        today = date.today()

        def trade_is_active(group):
            for expiry_str in group['expiry_date']:
                if pd.isna(expiry_str) or str(expiry_str).strip() == '':
                    return True
                try:
                    exp_date = datetime.strptime(str(expiry_str).strip(), "%Y-%m-%d").date()
                    if exp_date >= today:
                        return True
                except ValueError:
                    return True
            return False

        active_mask = df_combined.groupby('trade_id')['expiry_date'].transform(trade_is_active)
        expired_count = df_combined[~active_mask]['trade_id'].nunique()
        df_combined = df_combined[active_mask]

        if expired_count > 0:
            print(f"⛔ Удалено {expired_count} полностью просроченных сделок (все ноги истекли).")

        df_combined.sort_values(by=["time_utc5"], ascending=False, inplace=True)
        print(f"📊 Итого записей после фильтрации: {len(df_combined)}")
        df_combined.to_csv(CSV_FILE, index=False)
        print("Файл сохранён:", CSV_FILE)

    finally:
        driver.quit()

if __name__ == "__main__":
    main()
