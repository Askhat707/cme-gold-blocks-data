import os, time, hashlib, pickle
from datetime import datetime, timezone
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

URL = "https://www.cmegroup.com/clearing/operations-and-deliveries/accepted-trade-types/block-data.html#tradeDate=2025-08-22&subGroups=35&exchanges=COMEX&foi=O"
TABLE_WAIT_TIMEOUT = 45
SESSION_FILE = "cme_session.pkl"   # файл должен лежать в корне репозитория
CSV_FILE = "data.csv"

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
    opts.add_argument("--disable-features=TranslateUI,BlinkGenPropertyTrees")
    opts.add_argument("--disable-application-cache")
    opts.add_argument("--disable-client-side-phishing-detection")
    opts.add_argument("--disable-default-apps")
    opts.add_argument("--disable-hang-monitor")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--disable-prompt-on-repost")
    opts.add_argument("--disable-logging")
    opts.add_argument("--log-level=3")
    opts.add_argument("--silent")
    opts.add_argument("--disk-cache-size=1")
    opts.add_argument("--media-cache-size=1")
    opts.add_argument("--js-flags=--max-old-space-size=256")
    opts.add_argument("--disable-dev-tools")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
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

def convert_time_to_utc5(ts):
    try:
        t = datetime.strptime(ts, "%I:%M:%S %p")
        est = pytz.timezone("US/Eastern")
        utc5 = pytz.timezone("Asia/Almaty")
        now_est = datetime.now(est).date()
        est_time = est.localize(datetime.combine(now_est, t.time()))
        return est_time.astimezone(utc5).strftime("%H:%M:%S")
    except:
        return ts

def parse_all_gold_options(driver):
    records = []
    try:
        trade_date_elem = driver.find_element(By.CSS_SELECTOR, "span.button-text")
        trade_date = trade_date_elem.text
    except:
        trade_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        table = driver.find_element(By.CSS_SELECTOR, "table.block-trades-table")
    except NoSuchElementException:
        return records
    tbody_elements = table.find_elements(By.TAG_NAME, "tbody")
    current_time = None
    current_type = None
    for tbody in tbody_elements:
        rows = tbody.find_elements(By.TAG_NAME, "tr")
        for row in rows:
            cells = [c.text.strip() for c in row.find_elements(By.TAG_NAME, "td")]
            if cells and ":" in cells[0] and ("AM" in cells[0] or "PM" in cells[0]):
                current_time = convert_time_to_utc5(cells[0])
            for txt in ["Spread", "Strip", "Option"]:
                if txt in cells:
                    current_type = txt
                    break
            for idx, cell in enumerate(cells):
                if cell.startswith("OG") and "Futures" not in cells[max(0, idx-1)]:
                    product = cells[idx-1] if idx>0 else ""
                    if "Option" not in product:
                        continue
                    try:
                        symbol = cell
                        quantity = cells[idx+2] if idx+2 < len(cells) else "1"
                        strike_idx = idx+3
                        while strike_idx < len(cells) and not cells[strike_idx].startswith(('C','P')):
                            strike_idx += 1
                        if strike_idx+2 >= len(cells):
                            continue
                        strike = cells[strike_idx]
                        pos = cells[strike_idx+1].lower()
                        premium = cells[strike_idx+2]
                        if not (strike.startswith('C') or strike.startswith('P')):
                            continue
                        opt = "call" if strike.startswith('C') else "put"
                        strike_val = float(strike[1:])
                        premium_val = float(premium)
                        be = round(strike_val + premium_val if opt == "call" else strike_val - premium_val, 2)
                        records.append({
                            "trade_date": trade_date,
                            "time_utc5": current_time or "",
                            "type": current_type or "",
                            "symbol": symbol,
                            "strike": strike,
                            "option_type": opt,
                            "position": pos,
                            "premium": premium_val,
                            "quantity": int(quantity) if quantity.isdigit() else 1,
                            "breakeven": be,
                            "scraped_at": datetime.now(timezone.utc).isoformat()
                        })
                    except (ValueError, IndexError):
                        continue
    return records

def main():
    print("Starting scrape...")
    driver = create_driver()
    try:
        driver.get(URL)
        time.sleep(5)
        # accept cookies if present
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
            )
            btn.click()
        except:
            pass
        if load_session(driver):
            print("Session loaded")
        else:
            print("No session file, continuing...")
        if not wait_for_table(driver):
            print("Table not found")
            return
        records = parse_all_gold_options(driver)
        if records:
            df_new = pd.DataFrame(records)
            if os.path.exists(CSV_FILE):
                df_existing = pd.read_csv(CSV_FILE)
                # дедупликация по уникальным полям
                key_cols = ['trade_date','time_utc5','symbol','strike','position','premium']
                df_merged = pd.concat([df_existing, df_new], ignore_index=True)
                df_merged.drop_duplicates(subset=key_cols, keep='last', inplace=True)
                df_merged.to_csv(CSV_FILE, index=False)
            else:
                df_new.to_csv(CSV_FILE, index=False)
            print(f"Saved {len(records)} records, total {len(df_merged) if 'df_merged' in locals() else len(df_new)}")
        else:
            print("No records scraped")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
