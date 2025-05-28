# Dockerfile

# 최신 Python 3.9 Slim 이미지 사용
FROM python:3.9-slim-buster

# 필요한 시스템 패키지 설치
# wget, unzip: 파일 다운로드 및 압축 해제
# libglib2.0-0, libnss3, libfontconfig1, libxrender1, libxext6, libgconf-2-4, libffi-dev: Chrome/Chromium 의존성
# xvfb: Headless Chrome을 위한 가상 디스플레이 (필수는 아니지만 안정성을 높임)
RUN apt-get update && apt-get install -y \
    wget \
    unzip \
    libglib2.0-0 \
    libnss3 \
    libfontconfig1 \
    libxrender1 \
    libxext6 \
    libgconf-2-4 \
    libffi-dev \
    xvfb \
    fonts-liberation \ # 추가 폰트
    gconf-service \
    libappindicator1 \
    libasound2 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libexpat1 \
    libgdk-pixbuf2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxss1 \
    lsb-release \
    xdg-utils \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Google Chrome 다운로드 및 설치 (Render 환경에 맞춰 안정적인 버전 선택)
# 현재 버전 기준: Google Chrome Stable 최신 버전
# Chrome 바이너리 경로를 /usr/bin/google-chrome으로 설정
RUN wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && \
    dpkg -i /tmp/chrome.deb || apt-get -fy install && \
    rm /tmp/chrome.deb

# ChromeDriver 다운로드 및 설치 (설치된 Chrome 버전에 맞는 드라이버 사용)
# 최신 Chrome 버전에 맞는 ChromeDriver를 확인하여 URL과 버전 변경 필요
# 예: https://googlechromelabs.github.io/chrome-for-testing/
# 현재 Chrome Stable (125.x.x.x)에 맞는 ChromeDriver 125.0.6422.60 버전 예시
ENV CHROMEDRIVER_VERSION 125.0.6422.60
ENV CHROMEDRIVER_URL https://edgedl.me.gvt1.com/edgedl/chrome/chrome-for-testing/${CHROMEDRIVER_VERSION}/linux64/chromedriver-linux64.zip

RUN wget -q ${CHROMEDRIVER_URL} -O /tmp/chromedriver.zip && \
    unzip /tmp/chromedriver.zip -d /usr/local/bin/ && \
    mv /usr/local/bin/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver && \
    chmod +x /usr/local/bin/chromedriver && \
    rm -rf /tmp/chromedriver.zip /usr/local/bin/chromedriver-linux64

# 환경 변수 설정
ENV CHROMEDRIVER_PATH="/usr/local/bin/chromedriver"
ENV CHROME_BINARY_LOCATION="/usr/bin/google-chrome" # ChromeDriver에서 사용할 Chrome 바이너리 경로

# 작업 디렉토리 설정
WORKDIR /app

# 파이썬 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 파일 복사
COPY . .

# 봇 실행 명령어
# Gunicorn은 웹 서비스용이므로, 백그라운드 워커에는 직접 Python 스크립트를 실행합니다.
CMD ["python", "discord_bot_server.py"]