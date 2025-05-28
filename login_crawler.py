# 파일명: login_crawler.py

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
import time
import logging
import threading

# key.py 파일에서 설정값 불러오기
from key import CHROMEDRIVER_PATH, YOUR_ID, YOUR_PASSWORD

logging.basicConfig(level=logging.INFO)

# --- WebDriver 인스턴스 관리 (전역적으로, 그러나 스레드 안전하게) ---
_webdriver_local = threading.local() 
_webdriver_lock = threading.Lock() # <-- 추가: WebDriver 접근을 위한 스레드 잠금

def get_webdriver():
    """스레드 로컬에 WebDriver 인스턴스가 없으면 새로 생성하여 반환."""
    with _webdriver_lock: # <-- 추가: WebDriver 생성 및 접근 시 잠금
        if not hasattr(_webdriver_local, "driver") or _webdriver_local.driver is None:
            chrome_options = Options()
            chrome_options.add_argument("--headless")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            service = Service(executable_path=CHROMEDRIVER_PATH)
            _webdriver_local.driver = webdriver.Chrome(service=service, options=chrome_options)
            print("새로운 WebDriver 인스턴스 생성 및 초기화.")
        return _webdriver_local.driver

def close_webdriver(): # 이 함수가 반드시 존재해야 합니다.
    """스레드 로컬의 WebDriver 인스턴스를 닫음."""
    with _webdriver_lock: # <-- 추가: WebDriver 종료 시 잠금
        if hasattr(_webdriver_local, "driver") and _webdriver_local.driver is not None:
            try:
                _webdriver_local.driver.quit()
            except Exception as e:
                print(f"WebDriver 종료 중 오류 발생: {e}")
            finally:
                _webdriver_local.driver = None
                print("WebDriver 인스턴스 닫음.")

def get_bus_schedule():
    """
    버스 노선 정보를 크롤링하여 리스트로 반환합니다.
    WebDriver 인스턴스는 내부적으로 관리합니다.
    """
    driver = get_webdriver()

    try:
        current_url = driver.current_url
        if "bus_reservation.jsp" not in current_url:
            print("드라이버가 올바른 페이지에 있지 않음. 초기화 및 로그인 시도.")
            driver.get("https://kit.kumoh.ac.kr/jsp/administration/bus/bus_reservation.jsp")
            
            WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.NAME, "iframeA")))
            driver.switch_to.frame(driver.find_element(By.NAME, "iframeA"))
            print("iframe으로 컨텍스트 전환 완료.")

            WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.ID, "user_id")))
            driver.find_element(By.ID, "user_id").send_keys(YOUR_ID)
            driver.find_element(By.ID, "user_password").send_keys(YOUR_PASSWORD)
            print("아이디/비밀번호 입력 완료.")

            WebDriverWait(driver, 20).until(lambda d: d.execute_script("return typeof doLogin === 'function';"))
            driver.execute_script("doLogin()")
            print("로그인 버튼 클릭 완료.")
            time.sleep(3)
        else:
            try:
                driver.switch_to.default_content()
                WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.NAME, "iframeA")))
                driver.switch_to.frame(driver.find_element(By.NAME, "iframeA"))
                print("기존 드라이버를 사용하여 iframe으로 컨텍스트 재전환.")
            except Exception as e_switch:
                print(f"iframe 재전환 실패 (이미 iframe에 있거나, 구조 변경): {e_switch}")
                close_webdriver()
                return get_bus_schedule()

        search_button = WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.XPATH, "//div[@class='cl-text' and text()='조회']")))
        search_button.click()
        print("조회 버튼 클릭 완료.")
        time.sleep(5)

        page_source = driver.page_source
        soup = BeautifulSoup(page_source, 'html.parser')
        bus_routes_data = []

        all_rows = soup.find_all('div', class_=lambda x: x and 'cl-grid-row' in x)
        data_rows = all_rows[2:]

        if not data_rows:
            print("실제 데이터 행을 찾을 수 없습니다.")
            return []

        def get_text_from_cell(cell_div):
            cl_text_element = cell_div.find(class_='cl-text')
            if cl_text_element:
                if cl_text_element.name == 'input':
                    return cl_text_element.get('value', '').strip()
                else:
                    return cl_text_element.text.strip()
            return ""

        for i, row in enumerate(data_rows):
            cols_divs = row.find_all('div', class_=lambda x: x and 'cl-grid-cell' in x)
            if len(cols_divs) >= 7:
                try:
                    bus_id = get_text_from_cell(cols_divs[0])
                    bus_type = get_text_from_cell(cols_divs[1])
                    bus_number = get_text_from_cell(cols_divs[2])
                    bus_vehicle = get_text_from_cell(cols_divs[3])
                    bus_region = get_text_from_cell(cols_divs[4])
                    bus_route_detail = get_text_from_cell(cols_divs[5])
                    seats_info = get_text_from_cell(cols_divs[6])

                    current_seats, total_seats = 0, 0
                    if '/' in seats_info:
                        try:
                            current_seats, total_seats = map(int, seats_info.split('/'))
                        except ValueError:
                            pass

                    bus_routes_data.append({
                        "id": bus_id,
                        "bus_type": bus_type,
                        "bus_number": bus_number,
                        "bus_vehicle": bus_vehicle,
                        "bus_region": bus_region,
                        "bus_route_detail": bus_route_detail,
                        "current_seats": current_seats,
                        "total_seats": total_seats
                    })
                except Exception as ex:
                    logging.error(f"컬럼 데이터 추출 중 오류 (행 {i+1}, HTML 인덱스 {i+3}): {ex} - 행 내용: {row}", exc_info=True)
                    continue
            else:
                logging.warning(f"불완전한 행 감지 (컬럼 수 부족, 행 {i+1}, HTML 인덱스 {i+3}): {len(cols_divs)}개 - 행 내용: {row}")

    except Exception as e:
        logging.error(f"버스 스케줄 크롤링 중 치명적인 오류 발생: {e}", exc_info=True)
        close_webdriver()
        raise

    return bus_routes_data

if __name__ == '__main__':
    print("login_crawler.py 단독 실행 (테스트 모드)")
    try:
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        
        test_driver_options = Options()
        _webdriver_local.driver = webdriver.Chrome(service=Service(executable_path=CHROMEDRIVER_PATH), options=test_driver_options)

        bus_data = get_bus_schedule()
        print("\n--- 크롤링된 버스 노선 정보 (단독 실행) ---")
        if bus_data:
            for route in bus_data:
                print(f"[{route['id']}] {route['bus_type']} - {route['bus_number']} ({route['bus_vehicle']})")
                print(f"  지역: {route['bus_region']}")
                print(f"  노선: {route['bus_route_detail']}")
                print(f"  잔여 좌석: {route['current_seats']}/{route['total_seats']}")
                print("-" * 30)
        else:
            print("추출된 버스 노선 정보가 없습니다.")
        print("-------------------------------")
    finally:
        close_webdriver()
        print("스크립트 실행 완료. 브라우저가 닫혔습니다.")