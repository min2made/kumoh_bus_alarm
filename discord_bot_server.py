# 파일명: discord_bot_server.py

import discord
from discord.ext import commands
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import asyncio
import logging
import threading
from datetime import datetime
import os # os 모듈 임포트

# login_crawler.py에서 필요한 함수들을 임포트
from login_crawler import get_bus_schedule, close_webdriver

# key.py 파일 대신 환경 변수에서 설정값 불러오기
# Render.com 배포 시 이 환경 변수들을 설정해야 합니다.
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
# DISCORD_CHANNEL_ID는 int로 변환해야 합니다.
DISCORD_CHANNEL_ID = int(os.environ.get('DISCORD_CHANNEL_ID')) if os.environ.get('DISCORD_CHANNEL_ID') else None

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Discord 봇 설정
intents = discord.Intents.default()
intents.message_content = True # 메시지 내용을 읽을 권한 (개발자 포털에서도 활성화해야 함)
intents.guilds = True # 봇이 길드(서버) 정보를 캐시하도록 허용
intents.members = True # 봇이 멤버 정보를 캐시하도록 허용 (선택 사항이지만 도움이 될 수 있음)

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None) # 내장 help 명령어 비활성화
scheduler = BackgroundScheduler(timezone='Asia/Seoul')

# --- 전역 상태 관리 변수 ---
current_bus_schedules = []        # 현재 크롤링된 버스 노선 정보
last_update_time = None           # current_bus_schedules가 마지막으로 갱신된 시간
monitored_bus_ids = set()         # 모니터링 중인 버스 ID (예: 'BUS123')
data_lock = threading.Lock()      # 전역 변수 접근 시 스레드 안전을 위한 락

# --- 버스 모니터링 및 Discord 알림 함수 ---
async def monitor_all_monitored_buses_job():
    """모니터링 중인 버스 정보를 크롤링하고 Discord에 알리는 비동기 작업."""
    logging.info("버스 모니터링 작업을 시작합니다.")
    global current_bus_schedules, last_update_time

    try:
        # 1. 버스 스케줄 크롤링 및 갱신
        # login_crawler.py의 get_bus_schedule 함수 호출
        new_schedules = get_bus_schedule()
        with data_lock:
            current_bus_schedules = new_schedules
            last_update_time = datetime.now()
        logging.info(f"버스 스케줄을 성공적으로 갱신했습니다. 총 {len(current_bus_schedules)}개 노선.")

        # 2. 모니터링 중인 버스 정보 Discord 채널에 전송
        if monitored_bus_ids:
            if DISCORD_CHANNEL_ID is None:
                logging.error("DISCORD_CHANNEL_ID가 설정되지 않았습니다. 알림을 보낼 수 없습니다.")
                return

            channel = bot.get_channel(DISCORD_CHANNEL_ID)
            if channel:
                msg_header = "🚌 **버스 도착 정보 업데이트** 🚌\n"
                messages_to_send = []
                current_msg_part = msg_header

                # 모니터링 중인 버스 ID들을 순회하며 정보 구성
                # set을 list로 변환하여 순회 (락 내부에서 monitored_bus_ids 변경 방지)
                for bus_id in sorted(list(monitored_bus_ids)):
                    bus_info = next((b for b in current_bus_schedules if b['id'] == bus_id), None)
                    
                    line_part = ""
                    if bus_info:
                        try:
                            bus_number = bus_info.get('bus_number', 'N/A')
                            bus_type = bus_info.get('bus_type', 'N/A')
                            vehicle = bus_info.get('bus_vehicle', 'N/A')
                            remaining_seats = bus_info.get('remaining_seats', 'N/A')
                            total_seats = bus_info.get('total_seats', 'N/A')
                            arrival_time = bus_info.get('arrival_time', 'N/A')

                            line_part = (f"- **{bus_number}** ({bus_type}, {vehicle}): "
                                         f"{arrival_time} 도착 예정, 잔여 좌석: {remaining_seats}/{total_seats}\n")
                        except KeyError as ke:
                            logging.warning(f"버스 정보 {bus_id}에서 키 오류: {ke} - 정보: {bus_info}")
                            line_part = f"- ID: {bus_id} (정보 불완전 또는 오류 발생)\n"
                    else:
                        line_part = f"- ID: {bus_id} (정보를 찾을 수 없음, `!load`로 갱신 필요)\n"

                    # Discord 메시지 길이 제한(2000자) 고려하여 메시지 분할
                    if len(current_msg_part) + len(line_part) > 1900: # 여유롭게 1900자로 설정
                        messages_to_send.append(current_msg_part)
                        current_msg_part = "" # 다음 메시지 파트 시작

                    current_msg_part += line_part

                if current_msg_part and current_msg_part != msg_header: # 내용이 있으면 마지막 메시지 파트 추가
                    messages_to_send.append(current_msg_part)
                elif current_msg_part == msg_header and not monitored_bus_ids:
                    # 모니터링 중인 버스가 없으면 빈 메시지 전송 방지
                    logging.info("모니터링 중인 버스 노선이 없어 Discord 알림을 보내지 않습니다.")
                    return

                # Discord 메시지 전송
                for msg_part in messages_to_send:
                    await channel.send(msg_part)
                logging.info("버스 도착 정보 Discord 채널에 성공적으로 전송.")
            else:
                logging.error(f"지정된 채널 ID {DISCORD_CHANNEL_ID}를 찾을 수 없습니다. 봇이 올바른 서버에 추가되었는지 확인하세요.")
        else:
            logging.info("모니터링 중인 버스 노선이 없어 Discord 알림을 보내지 않습니다.")

    except Exception as e:
        logging.error(f"버스 모니터링 중 오류 발생: {e}", exc_info=True)
        # 오류 발생 시 WebDriver를 닫아서 다음 실행을 위해 깨끗한 상태 유지
        close_webdriver()
        
        if DISCORD_CHANNEL_ID is not None:
            channel = bot.get_channel(DISCORD_CHANNEL_ID)
            if channel:
                await channel.send(f"⚠️ **오류 발생**: 버스 정보를 가져오는 중 문제가 발생했습니다. 관리자에게 문의하세요.")
        else:
             logging.error("DISCORD_CHANNEL_ID가 설정되지 않아 오류 알림을 Discord로 보낼 수 없습니다.")

# --- Discord 봇 이벤트 핸들러 ---
@bot.event
async def on_ready():
    """봇이 Discord에 로그인되었을 때 실행됩니다."""
    logging.info(f'Logged in as {bot.user.name} ({bot.user.id})')
    print(f'Logged in as {bot.user.name} ({bot.user.id})')

    # APScheduler 시작
    if not scheduler.running:
        scheduler.start()
        logging.info("Scheduler started.")

    # 봇 시작 시 APScheduler에 메인 버스 모니터링 잡을 추가합니다.
    # 기존 job이 없으면 추가, 있으면 무시 (replace_existing=False가 기본값)
    if not scheduler.get_job('main_bus_monitor_job'):
        # CronTrigger를 사용하여 평일(월-금), 오전 9시부터 다음 날 새벽 2시까지 (즉, 새벽 3시 ~ 오전 8시 중단)
        # 매 1분마다 실행되도록 스케줄 설정
        scheduler.add_job(monitor_all_monitored_buses_job, 'cron',
                          day_of_week='mon-fri', # 월요일(0)부터 금요일(4)까지
                          hour='9-2',            # 오전 9시(9)부터 다음 날 새벽 2시(2)까지
                          minute='*/1',          # 매 1분마다
                          id='main_bus_monitor_job')
        logging.info("Main bus monitor job scheduled for weekdays (9 AM - 2 AM KST).")
    else:
        logging.info("Main bus monitor job already exists and is running.")
    
    # 봇이 시작될 때 미리 한 번 버스 정보를 로드합니다.
    # 이는 사용자가 !monitor 명령어를 사용하기 전에 버스 정보가 없어서
    # "정보를 찾을 수 없음" 메시지가 뜨는 것을 방지하기 위함입니다.
    try:
        logging.info("Initial bus data load on bot start...")
        temp_schedules = get_bus_schedule()
        with data_lock:
            global current_bus_schedules, last_update_time
            current_bus_schedules = temp_schedules
            last_update_time = datetime.now()
        logging.info(f"Initial bus data load complete. Loaded {len(current_bus_schedules)} schedules.")
    except Exception as e:
        logging.error(f"Initial bus data load failed: {e}", exc_info=True)
        close_webdriver() # 실패 시 웹드라이버 닫기


@bot.event
async def on_command_error(ctx, error):
    """명령어 실행 중 오류 발생 시 처리."""
    if isinstance(error, commands.CommandNotFound):
        await ctx.send(f"알 수 없는 명령어입니다. `!help`를 입력하여 사용 가능한 명령어를 확인하세요.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"명령어 인수가 부족합니다. 올바른 사용법: `{ctx.command.signature}`")
    else:
        logging.error(f"명령어 실행 중 오류 발생: {error}", exc_info=True)
        await ctx.send(f"명령어 실행 중 오류가 발생했습니다: `{error}`")

# --- Discord 봇 명령어 ---
@bot.command(name='help', help='사용 가능한 명령어 목록을 보여줍니다.')
async def show_help(ctx):
    help_text = """
    **🚌 버스 알리미 봇 명령어 🚌**
    
    `!help` - 이 도움말 메시지를 보여줍니다.
    `!load` - 금오공대 셔틀버스 정보를 수동으로 새로고침합니다. (새로운 버스 노선 확인용)
    `!list` - 현재 로드된 모든 버스 노선 정보를 보여줍니다.
    `!monitor [버스ID]` - 해당 버스 ID를 모니터링 목록에 추가합니다. (예: `!monitor K1`)
    `!unmonitor [버스ID]` - 해당 버스 ID를 모니터링 목록에서 제거합니다. (예: `!unmonitor K1`)
    `!monitors` - 현재 모니터링 중인 버스 목록을 보여줍니다.
    `!status` - 봇의 현재 상태와 설정 정보를 확인합니다.
    
    **💡 참고:**
    - 버스 정보 업데이트는 **평일(월~금) 오전 9시부터 다음 날 새벽 2시까지** 매 1분마다 자동으로 진행됩니다.
    - `버스ID`는 `!list` 명령어로 확인할 수 있는 `ID` 값을 사용합니다. (예: K1, K2, K3, N1, N2, N3 등)
    """
    await ctx.send(help_text)

@bot.command(name='load', help='버스 정보를 수동으로 새로고침합니다.')
async def load_bus_info(ctx):
    """금오공대 셔틀버스 정보를 수동으로 새로고침하고 Discord에 알립니다."""
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        await ctx.send(f"이 명령어는 <#{DISCORD_CHANNEL_ID}> 채널에서만 사용 가능합니다.")
        return

    await ctx.send("🚌 버스 정보를 새로고침 중입니다... 잠시만 기다려주세요.")
    try:
        new_schedules = get_bus_schedule()
        with data_lock:
            global current_bus_schedules, last_update_time
            current_bus_schedules = new_schedules
            last_update_time = datetime.now()
        await ctx.send(f"✅ 버스 정보를 성공적으로 새로고침했습니다. 총 {len(current_bus_schedules)}개 노선.")
        logging.info(f"수동으로 버스 정보를 새로고침했습니다. 총 {len(current_bus_schedules)}개 노선.")
    except Exception as e:
        logging.error(f"버스 정보 새로고침 중 오류 발생: {e}", exc_info=True)
        close_webdriver() # 실패 시 웹드라이버 닫기
        await ctx.send(f"❌ 버스 정보를 새로고침하는 데 실패했습니다. 오류: {e}")

@bot.command(name='list', help='현재 로드된 모든 버스 노선 정보를 보여줍니다.')
async def list_all_buses(ctx):
    """현재 로드된 모든 버스 노선 정보를 Discord에 전송합니다."""
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        await ctx.send(f"이 명령어는 <#{DISCORD_CHANNEL_ID}> 채널에서만 사용 가능합니다.")
        return

    with data_lock:
        if not current_bus_schedules:
            await ctx.send("현재 로드된 버스 노선 정보가 없습니다. `!load` 명령어로 새로고침해주세요.")
            return

        msg_header = "🚌 **현재 로드된 버스 노선 목록** 🚌\n"
        messages_to_send = []
        current_msg_part = msg_header

        for bus_info in current_bus_schedules:
            try:
                bus_id = bus_info.get('id', 'N/A')
                bus_number = bus_info.get('bus_number', 'N/A')
                bus_type = bus_info.get('bus_type', 'N/A')
                vehicle = bus_info.get('bus_vehicle', 'N/A')
                arrival_time = bus_info.get('arrival_time', 'N/A')
                remaining_seats = bus_info.get('remaining_seats', 'N/A')
                total_seats = bus_info.get('total_seats', 'N/A')

                line_part = (f"ID: `{bus_id}` | 노선: **{bus_number}** ({bus_type}, {vehicle})\n"
                             f"  > 도착 예정: {arrival_time}, 잔여 좌석: {remaining_seats}/{total_seats}\n")
                
                if len(current_msg_part) + len(line_part) > 1900:
                    messages_to_send.append(current_msg_part)
                    current_msg_part = ""
                current_msg_part += line_part

            except KeyError as ke:
                logging.warning(f"버스 정보 리스팅 중 키 오류: {ke} - 정보: {bus_info}")
                line_part = f"ID: `{bus_info.get('id', 'N/A')}` (정보 불완전 또는 오류 발생)\n"
                if len(current_msg_part) + len(line_part) > 1900:
                    messages_to_send.append(current_msg_part)
                    current_msg_part = ""
                current_msg_part += line_part

        if current_msg_part:
            messages_to_send.append(current_msg_part)
        
        for msg_part in messages_to_send:
            await ctx.send(msg_part)

@bot.command(name='monitor', help='버스 ID를 모니터링 목록에 추가합니다. `!monitor [버스ID]`')
async def add_monitor(ctx, bus_id: str):
    """모니터링 목록에 버스 ID를 추가합니다."""
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        await ctx.send(f"이 명령어는 <#{DISCORD_CHANNEL_ID}> 채널에서만 사용 가능합니다.")
        return

    bus_id = bus_id.upper() # 대소문자 구분 없이 처리

    with data_lock:
        if bus_id in monitored_bus_ids:
            await ctx.send(f"버스 ID `{bus_id}`는 이미 모니터링 중입니다.")
            return

        monitored_bus_ids.add(bus_id)
        # 현재 로드된 스케줄에서 버스 정보 확인 (사용자 편의를 위해 버스 이름 표시)
        bus_info = next((b for b in current_bus_schedules if b['id'] == bus_id), None)
        bus_name_display = f"{bus_info['bus_number']} ({bus_info['bus_type']})" if bus_info else bus_id

        await ctx.send(f"버스 ID `{bus_name_display}`를 모니터링 목록에 추가했습니다. "
                       f"자동 업데이트는 평일 오전 9시부터 다음 날 새벽 2시까지 매 1분마다 진행됩니다.")

@bot.command(name='unmonitor', help='모니터링 목록에서 버스 ID를 제거합니다. `!unmonitor [버스ID]`')
async def remove_monitor(ctx, bus_id: str):
    """모니터링 목록에서 버스 ID를 제거합니다."""
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        await ctx.send(f"이 명령어는 <#{DISCORD_CHANNEL_ID}> 채널에서만 사용 가능합니다.")
        return

    bus_id = bus_id.upper() # 대소문자 구분 없이 처리

    with data_lock:
        if bus_id not in monitored_bus_ids:
            await ctx.send(f"버스 ID `{bus_id}`는 모니터링 중이 아닙니다.")
            return

        monitored_bus_ids.remove(bus_id)
        await ctx.send(f"버스 ID `{bus_id}`를 모니터링 목록에서 제거했습니다.")

@bot.command(name='monitors', help='현재 모니터링 중인 버스 목록을 보여줍니다.')
async def list_monitors(ctx):
    """현재 모니터링 중인 버스 목록을 Discord에 전송합니다."""
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        await ctx.send(f"이 명령어는 <#{DISCORD_CHANNEL_ID}> 채널에서만 사용 가능합니다.")
        return

    with data_lock:
        if monitored_bus_ids:
            # 버스 ID를 오름차순으로 정렬하여 표시
            sorted_monitors = sorted(list(monitored_bus_ids))
            msg = "👀 **현재 모니터링 중인 버스 목록** 👀\n"
            
            messages_to_send = []
            current_msg_part = msg

            for bus_id in sorted_monitors:
                bus_info = next((b for b in current_bus_schedules if b['id'] == bus_id), None)
                bus_name_display = f"{bus_info['bus_number']} ({bus_info['bus_type']})" if bus_info else bus_id
                
                line_part = f"- `{bus_id}` ({bus_name_display})\n"

                if len(current_msg_part) + len(line_part) > 1900:
                    messages_to_send.append(current_msg_part)
                    current_msg_part = ""
                current_msg_part += line_part

            if current_msg_part:
                messages_to_send.append(current_msg_part)
            
            for msg_part in messages_to_send:
                await ctx.send(msg_part)
        else:
            await ctx.send("현재 모니터링 중인 버스 노선이 없습니다. `!monitor [버스ID]` 명령어로 모니터링을 시작하세요.")


@bot.command(name='status', help='현재 봇 상태와 채널 정보를 확인합니다.')
async def bot_status(ctx):
    """봇의 현재 상태 정보를 Discord에 전송합니다."""
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        await ctx.send(f"이 명령어는 <#{DISCORD_CHANNEL_ID}> 채널에서만 사용 가능합니다.")
        return

    with data_lock: # 전역 변수 접근 시 락 사용
        status_msg = f"🤖 **봇 상태 정보**\n"
        status_msg += f"• 봇 이름: {bot.user.name}\n"
        status_msg += f"• 봇 ID: {bot.user.id}\n"
        status_msg += f"• 설정된 채널 ID: {DISCORD_CHANNEL_ID}\n"
        status_msg += f"• 현재 채널 ID: {ctx.channel.id}\n"
        status_msg += f"• 로드된 버스 노선: {len(current_bus_schedules)}개\n"
        status_msg += f"• 모니터링 중인 버스: {', '.join(sorted(list(monitored_bus_ids))) if monitored_bus_ids else '없음'}\n"
        if last_update_time:
            status_msg += f"• 마지막 업데이트: {last_update_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        else:
            status_msg += f"• 마지막 업데이트: 없음\n"
        
        # 스케줄러 잡 상태 정보 추가
        job = scheduler.get_job('main_bus_monitor_job')
        if job:
            status_msg += f"• 모니터링 스케줄: 평일 (월~금) 오전 9시 ~ 다음날 새벽 2시\n"
            status_msg += f"• 다음 실행 예정: {job.next_run_time.strftime('%Y-%m-%d %H:%M:%S')} (KST)\n"
        else:
            status_msg += f"• 모니터링 스케줄: 설정되지 않음 (봇 시작 시 자동 설정)\n"
        
        await ctx.send(status_msg)

# --- 봇 실행 ---
if __name__ == '__main__':
    if not DISCORD_BOT_TOKEN:
        logging.error("DISCORD_BOT_TOKEN 환경 변수가 설정되지 않았습니다. 봇을 실행할 수 없습니다.")
    elif DISCORD_CHANNEL_ID is None:
        logging.error("DISCORD_CHANNEL_ID 환경 변수가 설정되지 않았습니다. 봇을 실행할 수 없습니다.")
    else:
        try:
            bot.run(DISCORD_BOT_TOKEN)
        except discord.errors.LoginFailure as e:
            logging.error(f"Discord 봇 토큰이 유효하지 않습니다: {e}")
        except Exception as e:
            logging.error(f"봇 실행 중 예상치 못한 오류 발생: {e}", exc_info=True)