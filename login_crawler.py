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
import os # os 모듈 임포트

# key.py 파일 대신 환경 변수에서 설정값 불러오기
# Render.com 배포 시 이 환경 변수들을 설정해야 합니다.
YOUR_ID = os.environ.get('YOUR_ID')
YOUR_PASSWORD = os.environ.get('YOUR_PASSWORD')
# CHROMEDRIVER_PATH와 CHROME_BINARY_LOCATION은 Dockerfile에서 설정한 경로와 일치해야 합니다.
CHROMEDRIVER_PATH = os.environ.get('CHROMEDRIVER_PATH', '/usr/local/bin/chromedriver') # Dockerfile의 기본 경로
CHROME_BINARY_LOCATION = os.environ.get('CHROME_BINARY_LOCATION', '/usr/bin/google-chrome') # Dockerfile의 기본 경로

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- WebDriver 인스턴스 관리 (전역적으로, 그러나 스레드 안전하게) ---
_webdriver_local = threading.local()
_webdriver_lock = threading.Lock() # WebDriver 생성/접근 시 사용될 스레드 잠금

def get_webdriver():
    """
    스레드 로컬에 WebDriver 인스턴스가 없으면 새로 생성하여 반환합니다.
    WebDriver는 스레드별로 독립적으로 관리됩니다.
    """
    with _webdriver_lock: # WebDriver 생성 및 접근 시 잠금
        if not hasattr(_webdriver_local, "driver") or _webdriver_local.driver is None:
            logging.info("WebDriver 인스턴스 생성 중...")
            chrome_options = Options()
            chrome_options.add_argument("--headless")         # Headless 모드 (GUI 없이 백그라운드 실행)
            chrome_options.add_argument("--no-sandbox")       # 샌드박스 비활성화 (Docker 환경 필수)
            chrome_options.add_argument("--disable-dev-shm-usage") # /dev/shm 사용 비활성화 (Docker 환경 필수)
            chrome_options.add_argument("--disable-gpu")      # GPU 사용 비활성화
            chrome_options.add_argument("--window-size=1920,1080") # 창 크기 설정
            chrome_options.add_argument("--remote-debugging-port=9222") # 원격 디버깅 포트 (디버깅용)
            
            # Chrome 바이너리 경로 명시 (Dockerfile에서 설치한 경로)
            chrome_options.binary_location = CHROME_BINARY_LOCATION
            
            try:
                # ChromeDriver 서비스 설정
                service = Service(executable_path=CHROMEDRIVER_PATH)
                _webdriver_local.driver = webdriver.Chrome(service=service, options=chrome_options)
                logging.info(f"WebDriver 인스턴스 성공적으로 생성됨.")
                logging.info(f"  Chrome 바이너리 위치: {CHROME_BINARY_LOCATION}")
                logging.info(f"  ChromeDriver 경로: {CHROMEDRIVER_PATH}")
            except Exception as e:
                logging.error(f"WebDriver 생성 중 오류 발생: {e}", exc_info=True)
                _webdriver_local.driver = None # 오류 시 드라이버를 None으로 설정
                raise # 예외 다시 발생시켜 호출자에게 알림
        return _webdriver_local.driver

def close_webdriver():
    """
    스레드 로컬의 WebDriver 인스턴스가 존재하면 닫고 제거합니다.
    오류 발생 시, 또는 특정 작업을 마친 후 WebDriver 리소스를 해제할 때 사용합니다.
    """
    with _webdriver_lock: # WebDriver 종료 시에도 잠금
        if hasattr(_webdriver_local, "driver") and _webdriver_local.driver is not None:
            try:
                _webdriver_local.driver.quit()
                logging.info("WebDriver 인스턴스 종료됨.")
            except Exception as e:
                logging.error(f"WebDriver 종료 중 오류 발생: {e}", exc_info=True)
            finally:
                _webdriver_local.driver = None # 참조 제거

def perform_login(driver):
    """금오공대 로그인 페이지에 로그인합니다."""
    logging.info("로그인 절차 시작...")
    try:
        driver.get("https://kumoh.ac.kr/bus/index.do")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "login-menu"))
        )

        login_menu = driver.find_element(By.ID, "login-menu")
        login_menu.click()

        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "id"))
        )

        id_input = driver.find_element(By.ID, "id")
        password_input = driver.find_element(By.ID, "password")
        login_button = driver.find_element(By.ID, "login")

        if not YOUR_ID or not YOUR_PASSWORD:
            logging.error("로그인 ID 또는 비밀번호 환경 변수가 설정되지 않았습니다.")
            raise ValueError("로그인 ID 또는 비밀번호가 필요합니다.")

        id_input.send_keys(YOUR_ID)
        password_input.send_keys(YOUR_PASSWORD)
        login_button.click()

        WebDriverWait(driver, 10).until(
            EC.url_changes("https://kumoh.ac.kr/bus/index.do") # URL 변경을 기다림
        )
        
        # 로그인 성공 여부 확인 (예: 특정 요소의 존재 여부)
        # 예시: 'logout-menu' 또는 로그인 후 나타나는 특정 요소
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "logout-menu")) # 로그아웃 버튼이 생겼는지 확인
        )
        logging.info("로그인 성공!")

    except Exception as e:
        logging.error(f"로그인 중 오류 발생: {e}", exc_info=True)
        # 로그인 실패 시 WebDriver를 닫아서 다음 시도를 위해 초기화
        close_webdriver()
        raise # 예외 다시 발생

def get_bus_schedule():
    """
    금오공대 셔틀버스 정보를 크롤링하여 반환합니다.
    WebDriver 인스턴스가 없으면 새로 생성하고 로그인합니다.
    """
    driver = get_webdriver()
    bus_routes_data = []

    try:
        # 드라이버가 로그인된 상태인지 확인 (옵션: URL 확인 등)
        # 이미 로그인되어 있지 않다면 로그인 수행
        if "login" in driver.current_url or "sso" in driver.current_url or "index.do" not in driver.current_url:
            perform_login(driver)
            time.sleep(2) # 로그인 후 페이지 로딩 대기

        # 버스 스케줄 페이지로 이동 (이미 로그인된 상태라면 다시 이동할 필요 없을 수 있지만, 안전을 위해)
        driver.get("https://kumoh.ac.kr/bus/index.do")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "bus-schedule-list"))
        )

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        bus_schedule_list = soup.find('ul', class_='bus-schedule-list')

        if not bus_schedule_list:
            logging.error("버스 스케줄 리스트를 찾을 수 없습니다. HTML 구조 변경 가능성.")
            raise ValueError("버스 스케줄 리스트를 찾을 수 없습니다.")

        rows = bus_schedule_list.find_all('li')

        if not rows:
            logging.warning("버스 스케줄 데이터가 없습니다. 페이지 내용 확인 필요.")
            return []

        for i, row in enumerate(rows):
            cols_divs = row.find_all('div', recursive=False) # 직접적인 자식 div만 찾기
            
            # 예상하는 <div> 개수는 6개입니다. (ID, 버스번호, 타입, 차량, 잔여좌석, 도착예정)
            # 0: id, 1: bus_number, 2: bus_type, 3: bus_vehicle, 4: remaining_seats, 5: arrival_time
            if len(cols_divs) >= 6:
                try:
                    bus_id = cols_divs[0].get_text(strip=True)
                    bus_number = cols_divs[1].get_text(strip=True)
                    bus_type = cols_divs[2].get_text(strip=True)
                    bus_vehicle = cols_divs[3].get_text(strip=True)
                    
                    # '잔여 좌석' 부분에서 총 좌석수 추출
                    seats_text = cols_divs[4].get_text(strip=True)
                    remaining_seats = seats_text.split('/')[0].strip() if '/' in seats_text else seats_text
                    total_seats = seats_text.split('/')[1].strip() if '/' in seats_text else 'N/A'

                    arrival_time = cols_divs[5].get_text(strip=True)

                    bus_routes_data.append({
                        'id': bus_id,
                        'bus_number': bus_number,
                        'bus_type': bus_type,
                        'bus_vehicle': bus_vehicle,
                        'remaining_seats': remaining_seats,
                        'total_seats': total_seats,
                        'arrival_time': arrival_time
                    })
                except Exception as ex:
                    logging.error(f"컬럼 데이터 추출 중 오류 (행 {i+1}): {ex} - 행 내용: {row}", exc_info=True)
                    continue
            else:
                logging.warning(f"불완전한 행 감지 (컬럼 수 부족, 행 {i+1}): {len(cols_divs)}개 - 행 내용: {row}")

    except Exception as e:
        logging.error(f"버스 스케줄 크롤링 중 치명적인 오류 발생: {e}", exc_info=True)
        close_webdriver() # 오류 발생 시 WebDriver 종료
        raise # 예외 다시 발생시켜 호출자에게 알림

    return bus_routes_data

if __name__ == '__main__':
    # 이 부분은 로컬에서 login_crawler.py를 단독으로 테스트할 때 사용됩니다.
    # Render.com 배포 시에는 이 부분이 직접 실행되지 않습니다.
    print("login_crawler.py 단독 실행 (테스트 모드)")
    
    # 환경 변수가 설정되어 있지 않다면 더미 값 사용 (로컬 테스트용)
    if not os.environ.get('YOUR_ID'):
        os.environ['YOUR_ID'] = 'your_test_id'
        os.environ['YOUR_PASSWORD'] = 'your_test_password'
    if not os.environ.get('CHROMEDRIVER_PATH'):
        # 로컬 환경에 맞는 ChromeDriver 경로 설정 (예시)
        # MacOS: '/usr/local/bin/chromedriver'
        # Windows: 'C:\\path\\to\\chromedriver.exe'
        os.environ['CHROMEDRIVER_PATH'] = '/usr/local/bin/chromedriver' 
    if not os.environ.get('CHROME_BINARY_LOCATION'):
        # 로컬 환경에 맞는 Chrome 바이너리 경로 설정 (예시)
        # MacOS: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
        # Windows: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
        os.environ['CHROME_BINARY_LOCATION'] = '/usr/bin/google-chrome' # 리눅스 기본값

    try:
        # WebDriver를 직접 생성하지 않고 get_webdriver()를 통해 인스턴스 가져오기
        driver = get_webdriver() 
        
        bus_data = get_bus_schedule()
        print("\n--- 크롤링된 버스 노선 정보 (단독 실행) ---")
        if bus_data:
            for route in bus_data:
                print(f"[{route['id']}] {route['bus_type']} - {route['bus_number']} ({route['bus_vehicle']})")
                print(f"  도착 예정: {route['arrival_time']}, 잔여 좌석: {route['remaining_seats']}/{route['total_seats']}")
        else:
            print("크롤링된 버스 정보가 없습니다.")
    except Exception as e:
        print(f"테스트 중 오류 발생: {e}")
    finally:
        close_webdriver()
        print("WebDriver 종료.")