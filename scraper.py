# scraper.py
import os, time, hashlib, pickle, re
from datetime import datetime
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
URL = (
    "https://www.cmegroup.com/clearing/operations-and-deliveries/"
    "accepted-trade-types/block-data.html"
    "#tradeDate=2025-08-22&subGroups=35&exchanges=COMEX&foi=O"
)
TABLE_WAIT_TIMEOUT = 45
SESSION_FILE = "cme_session.pkl"
CSV_FILE = "data.csv"

# Часовые пояса
CT_TZ = pytz.timezone("US/Central")
TARGET_TZ = pytz.timezone("Asia/Almaty")  # UTC+5

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
        print("Таблица не найдена")
        return records

    rows = table.find_elements(By.TAG_NAME, "tr")
    current_time = None
    current_type = None
    current_group_legs = []
    trades = []

    for row in rows:
        cells = [c.text.strip() for c in row.find_elements(By.TAG_NAME, "td")]
        if not cells:
            continue

        # Первая ячейка содержит время?
        time_match = re.match(r'^\d{1,2}:\d{2}:\d{2}\s*[AP]M$', cells[0])
        if time_match:
            if current_group_legs:
                trades.append({
                    "time": current_time,
                    "type": current_type,
                    "legs": current_group_legs
                })
            current_time = convert_time_to_utc5(cells[0], trade_date)
            current_type = cells[1] if len(cells) > 1 else ""
            current_group_legs = []
            leg = parse_leg_row(cells, is_header=True)
            if leg:
                current_group_legs.append(leg)
        else:
            # Строка без времени – продолжение группы
            leg = parse_leg_row(cells, is_header=False)
            if leg:
                current_group_legs.append(leg)

    if current_group_legs:
        trades.append({
            "time": current_time,
            "type": current_type,
            "legs": current_group_legs
        })

    for trade in trades:
        if not trade["legs"]:
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

            records.append({
                "trade_id": trade_id,
                "trade_date": trade_date,
                "time_utc5": trade["time"],
                "type": trade["type"],
                "product_name": leg.get("product_name", ""),
                "symbol": leg.get("symbol", ""),
                "option_type": leg.get("option_type", ""),
                "strike": leg.get("strike", ""),
                "position": leg.get("position", ""),
                "price": leg.get("price", 0.0),
                "quantity": leg.get("quantity", 1),
                "breakeven": breakeven
            })
    return records

def parse_leg_row(cells, is_header):
    try:
        if is_header:
            # Ячеек всегда 9: time, type, product, sym, net, qty, strike, bs, price
            if len(cells) < 9:
                return None
            product = cells[2]
            symbol = cells[3]
            qty_str = cells[5]          # qty после net-price
            strike_str = cells[6]
            b_s = cells[7].capitalize()
            price_str = cells[8]
        else:
            # Может быть 6 ячеек (без net-price) или 7 (с net-price)
            if len(cells) < 6:
                return None
            product = cells[0]
            symbol = cells[1]
            # Определяем, содержит ли cells[2] число (net-price) или это qty
            possible_net = cells[2].strip()
            if possible_net and re.match(r'^-?\d+(\.\d+)?$', possible_net):
                # Есть net-price: cells[2]=net, cells[3]=qty, cells[4]=strike, cells[5]=bs, cells[6]=price
                if len(cells) < 7:
                    return None
                qty_str = cells[3]
                strike_str = cells[4]
                b_s = cells[5].capitalize()
                price_str = cells[6]
            else:
                # Нет net-price: cells[2]=qty, cells[3]=strike, cells[4]=bs, cells[5]=price
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
            print("Таблица не найдена")
            return

        records = parse_all_gold_options(driver)
        if records:
            df = pd.DataFrame(records)
            cols_order = [
                "trade_id", "trade_date", "time_utc5", "type", "product_name",
                "symbol", "option_type", "strike", "position", "quantity",
                "breakeven", "price"
            ]
            df = df[cols_order]
            df.drop_duplicates(subset=["trade_id", "symbol", "position"], inplace=True)
            df.sort_values(by=["time_utc5"], ascending=False, inplace=True)
            df.to_csv(CSV_FILE, index=False)
            print(f"Файл перезаписан, сохранено {len(df)} записей")
        else:
            print("Нет данных для сохранения")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
