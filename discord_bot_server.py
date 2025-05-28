# 파일명: discord_bot_server.py (개선된 버전)

import discord
from discord.ext import commands
from apscheduler.schedulers.background import BackgroundScheduler
import asyncio
import logging
import threading
from datetime import datetime

# login_crawler.py에서 필요한 함수들을 임포트
from login_crawler import get_bus_schedule, close_webdriver

# key.py 파일에서 설정값 불러오기
from key import DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Discord 봇 설정
intents = discord.Intents.default()
intents.message_content = True # 메시지 내용을 읽을 권한 (개발자 포털에서도 활성화해야 함)
intents.guilds = True # 봇이 길드(서버) 정보를 캐시하도록 허용
intents.members = True # 봇이 멤버 정보를 캐시하도록 허용 (선택 사항이지만 도움이 될 수 있음)

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None) # 내장 help 명령어 비활성화
scheduler = BackgroundScheduler()

# --- 전역 상태 관리 변수 ---
current_bus_schedules = []        # 현재 크롤링된 버스 노선 정보
last_update_time = None           # current_bus_schedules가 마지막으로 갱신된 시간
monitored_bus_ids = set()         # 모니터링할 버스 번호들 (사용자 입력, 여러 개 가능)
last_monitored_seats = {}         # 마지막으로 모니터링한 버스의 좌석 정보 {bus_id: current_seats}

# 스레드 동기화를 위한 락
data_lock = threading.Lock() # 데이터 접근을 위한 락 (크롤링 결과 및 모니터링 목록)
webdriver_lock = threading.Lock() # WebDriver 인스턴스 접근을 위한 락 (login_crawler.py와 공유)


# --- 디스코드 메시지 전송 함수 ---
async def send_discord_message(channel_id, text_content):
    """
    디스코드 채널에 메시지를 보냅니다.
    """
    try:
        # 봇이 준비되지 않았으면 대기
        if not bot.is_ready():
            logging.warning("봇이 아직 준비되지 않았습니다. 3초 대기 후 재시도...")
            await asyncio.sleep(3)
            if not bot.is_ready():
                logging.error("봇이 준비되지 않아 메시지를 보낼 수 없습니다.")
                return

        channel = bot.get_channel(channel_id)
        if channel:
            logging.info(f"디스코드 메시지 전송 시도 (채널: {channel.name}, 내용: {text_content[:50]}...)")
            await channel.send(text_content)
            logging.info("디스코드 메시지 전송 성공!")
        else:
            # 채널을 직접 fetch 시도
            try:
                channel = await bot.fetch_channel(channel_id)
                if channel:
                    logging.info(f"fetch로 채널 찾음. 메시지 전송 시도 (채널: {channel.name})")
                    await channel.send(text_content)
                    logging.info("디스코드 메시지 전송 성공!")
                else:
                    logging.error(f"채널 ID ({channel_id})를 fetch할 수 없습니다.")
            except Exception as fetch_error:
                logging.error(f"채널 fetch 중 오류: {fetch_error}")
                logging.error(f"지정된 채널 ID ({channel_id})를 찾을 수 없습니다. 봇이 해당 채널에 접근 권한이 있는지 확인하세요.")
    except Exception as e:
        logging.error(f"메시지 전송 중 오류 발생: {e}", exc_info=True)


# --- 버스 스케줄 초기 로드 및 갱신 함수 (단 한 번의 크롤링으로 모든 데이터 가져옴) ---
def update_bus_schedules():
    global current_bus_schedules, last_update_time
    logging.info("버스 스케줄 데이터 갱신 시작...")
    with webdriver_lock: # WebDriver 접근 시 락 사용
        try:
            new_schedules = get_bus_schedule() # login_crawler에서 모든 버스 정보 가져옴
            with data_lock: # 데이터 갱신 시 락 사용
                current_bus_schedules = new_schedules
                last_update_time = datetime.now()
            logging.info(f"버스 스케줄 데이터 갱신 완료. ({len(current_bus_schedules)}개 노선)")
            return True # 성공
        except Exception as e:
            logging.error(f"버스 스케줄 데이터 갱신 중 오류 발생: {e}", exc_info=True)
            # 오류 발생 시 WebDriver 닫기
            try:
                close_webdriver()
            except Exception as ce:
                logging.error(f"WebDriver 닫기 중 오류 발생: {ce}")
            return False # 실패


# --- 모니터링 중인 모든 버스 좌석 모니터링 함수 (주기적으로 실행될 메인 잡) ---
def monitor_all_monitored_buses_job():
    """
    스케줄러에 의해 주기적으로 실행될 모니터링 작업 함수.
    모니터링 대상인 모든 버스에 대해 좌석 현황을 확인하고 알림을 보냅니다.
    """
    global last_monitored_seats, current_bus_schedules, monitored_bus_ids

    # 1. 최신 버스 스케줄 데이터 갱신 (단 한 번의 크롤링)
    logging.info("모니터링을 위해 전체 버스 스케줄 데이터 갱신 시작...")
    if not update_bus_schedules():
        message = "버스 스케줄 갱신 중 오류가 발생하여 현재 모니터링을 정상적으로 수행할 수 없습니다."
        future = asyncio.run_coroutine_threadsafe(
            send_discord_message(DISCORD_CHANNEL_ID, message), 
            bot.loop
        )
        try:
            future.result(timeout=10)
        except Exception as send_error:
            logging.error(f"메시지 전송 실패: {send_error}")
        logging.error("전체 버스 스케줄 갱신 실패. 모니터링 작업 중단.")
        
        # 오류 시 모든 모니터링 중단 및 WebDriver 닫기
        with data_lock:
            monitored_bus_ids.clear()
            last_monitored_seats.clear()
        with webdriver_lock:
            close_webdriver()
        
        # 이 잡 자체를 제거하여 더 이상 실행되지 않도록 함
        if scheduler.get_job('main_bus_monitor_job'):
            scheduler.remove_job('main_bus_monitor_job')
            logging.info("메인 모니터링 잡 'main_bus_monitor_job' 제거 완료.")
        return

    # 2. 모니터링 대상 버스들에 대한 알림 로직 처리
    with data_lock: # current_bus_schedules 및 monitored_bus_ids, last_monitored_seats 접근 시 락 사용
        buses_to_remove = set() # 모니터링을 중단할 버스 ID 목록
        for bus_id_to_monitor in list(monitored_bus_ids): # Set을 iterate하면서 remove하면 오류 발생 가능 -> list로 변환 후 사용
            monitored_bus_info = next((bus for bus in current_bus_schedules if bus['id'] == bus_id_to_monitor), None)

            if monitored_bus_info:
                current_seats = monitored_bus_info['current_seats']
                total_seats = monitored_bus_info['total_seats']
                prev_seats = last_monitored_seats.get(bus_id_to_monitor)

                # 첫 실행 알림 (만석 상태 확인)
                if prev_seats is None:
                    if current_seats == total_seats:
                        initial_message = f"✅ ID '{bus_id_to_monitor}'번 노선이 현재 만석({current_seats}/{total_seats})입니다!\n" \
                                          f"노선: {monitored_bus_info['bus_route_detail']}"
                        future = asyncio.run_coroutine_threadsafe(
                            send_discord_message(DISCORD_CHANNEL_ID, initial_message), 
                            bot.loop
                        )
                        try:
                            future.result(timeout=10)
                            logging.info(f"ID '{bus_id_to_monitor}' 첫 모니터링 알림 전송 (만석): {current_seats}/{total_seats}")
                        except Exception as send_error:
                            logging.error(f"첫 모니터링 알림 전송 실패: {send_error}")
                        # 만석이면 계속 모니터링
                        last_monitored_seats[bus_id_to_monitor] = current_seats
                    else:
                        initial_message = f"ID '{bus_id_to_monitor}'번 노선은 현재 만석이 아닙니다. " \
                                          f"현재 좌석: {current_seats}/{total_seats}\n" \
                                          f"만석({total_seats}/{total_seats})이 되면 알림을 보내드릴게요."
                        future = asyncio.run_coroutine_threadsafe(
                            send_discord_message(DISCORD_CHANNEL_ID, initial_message), 
                            bot.loop
                        )
                        try:
                            future.result(timeout=10)
                            logging.info(f"ID '{bus_id_to_monitor}' 첫 모니터링 알림 전송 (만석 아님): {current_seats}/{total_seats}")
                        except Exception as send_error:
                            logging.error(f"첫 모니터링 알림 전송 실패: {send_error}")
                        buses_to_remove.add(bus_id_to_monitor) # 만석이 아니므로 모니터링 중단 요청
                
                # 만석 (total_seats/total_seats)일 때 알림 로직
                elif current_seats == total_seats:
                    if prev_seats is not None and prev_seats != total_seats: # 만석 상태가 새로 감지되었을 때
                        message = f"✅ ID '{bus_id_to_monitor}'번 노선이 만석({current_seats}/{total_seats})이 되었습니다!\n" \
                                  f"노선: {monitored_bus_info['bus_route_detail']}\n" \
                                  f"예약 페이지: <https://kit.kumoh.ac.kr/jsp/administration/bus/bus_reservation.jsp>"
                        future = asyncio.run_coroutine_threadsafe(
                            send_discord_message(DISCORD_CHANNEL_ID, message), 
                            bot.loop
                        )
                        try:
                            future.result(timeout=10)
                            logging.info(f"ID '{bus_id_to_monitor}' 만석 알림 전송: {current_seats}/{total_seats}")
                        except Exception as send_error:
                            logging.error(f"만석 알림 전송 실패: {send_error}")
                    else:
                        logging.info(f"ID '{bus_id_to_monitor}' 계속 만석 유지 중: {current_seats}/{total_seats}")
                    last_monitored_seats[bus_id_to_monitor] = current_seats # 현재 좌석 상태 저장
                else: # 만석이 아닐 때 (prev_seats == total_seats 였을 경우)
                    if prev_seats == total_seats: 
                        message = f"🚌 ID '{bus_id_to_monitor}'번 노선이 만석이 아니게 되었습니다. " \
                                  f"현재 좌석: {current_seats}/{total_seats}\n" \
                                  f"노선: {monitored_bus_info['bus_route_detail']}"
                        future = asyncio.run_coroutine_threadsafe(
                            send_discord_message(DISCORD_CHANNEL_ID, message), 
                            bot.loop
                        )
                        try:
                            future.result(timeout=10)
                            logging.info(f"ID '{bus_id_to_monitor}' 만석 아님으로 변경 알림 전송: {current_seats}/{total_seats}")
                        except Exception as send_error:
                            logging.error(f"만석 아님 알림 전송 실패: {send_error}")
                    else:
                        logging.info(f"ID '{bus_id_to_monitor}' 계속 만석 아님 유지 중: {current_seats}/{total_seats}. 추가 알림 없음.")
                    buses_to_remove.add(bus_id_to_monitor) # 만석이 아니므로 모니터링 중단 요청
            else:
                # 버스 정보를 찾을 수 없는 경우
                message = f"ID '{bus_id_to_monitor}' 노선을 찾을 수 없습니다. 모니터링을 중단합니다."
                future = asyncio.run_coroutine_threadsafe(
                    send_discord_message(DISCORD_CHANNEL_ID, message), 
                    bot.loop
                )
                try:
                    future.result(timeout=10)
                except Exception as send_error:
                    logging.error(f"노선 없음 알림 전송 실패: {send_error}")
                logging.warning(f"ID '{bus_id_to_monitor}' 노선을 찾을 수 없음. 모니터링 중단.")
                buses_to_remove.add(bus_id_to_monitor) # 해당 버스 ID 제거 요청
        
        # 모니터링 중단 요청된 버스들을 실제로 제거
        for bus_id in buses_to_remove:
            monitored_bus_ids.discard(bus_id)
            if bus_id in last_monitored_seats:
                del last_monitored_seats[bus_id]
            logging.info(f"ID '{bus_id}' 모니터링 중단 처리 완료.")

    # 3. 모든 모니터링이 중단되면 WebDriver 닫기
    if not monitored_bus_ids:
        logging.info("모니터링 중인 버스가 없어 WebDriver를 닫습니다.")
        with webdriver_lock:
            close_webdriver()
        # 모든 모니터링이 끝나면 메인 잡도 제거
        if scheduler.get_job('main_bus_monitor_job'):
            scheduler.remove_job('main_bus_monitor_job')
            logging.info("메인 모니터링 잡 'main_bus_monitor_job' 제거 완료 (모든 버스 중단).")


# --- 정기 업데이트 함수 (모니터링 중인 버스가 없을 때만 전체 스케줄 갱신) ---
def scheduled_hourly_update():
    global monitored_bus_ids
    with data_lock: # monitored_bus_ids 접근 시 락 사용
        if not monitored_bus_ids: # 모니터링 중인 버스가 없을 때만 실행
            logging.info("모니터링 중인 버스가 없어 1시간 주기 전체 버스 스케줄 갱신을 실행합니다.")
            
            if not update_bus_schedules():
                logging.error("1시간 주기 버스 스케줄 갱신 실패.")
            else:
                future = asyncio.run_coroutine_threadsafe(
                    send_discord_message(DISCORD_CHANNEL_ID, "⏰ 정기 업데이트: 버스 노선 정보가 갱신되었습니다. `!list`로 확인하세요."),
                    bot.loop
                )
                try:
                    future.result(timeout=10)
                except Exception as send_error:
                    logging.error(f"정기 업데이트 알림 전송 실패: {send_error}")
        else:
            logging.info("모니터링 중인 버스가 있어 1시간 주기 전체 버스 스케줄 갱신을 건너뜁니다 (메인 모니터링 잡이 이미 갱신).")


# --- 디스코드 봇 이벤트 핸들러 ---
@bot.event
async def on_ready():
    logging.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    logging.info(f'설정된 채널 ID: {DISCORD_CHANNEL_ID}')
    
    # 채널 접근 가능 여부 확인
    try:
        channel = bot.get_channel(DISCORD_CHANNEL_ID)
        if channel:
            logging.info(f'채널 접근 성공: {channel.name} (ID: {channel.id})')
            await channel.send("🤖 금오공대 통학버스 알리미가 시작되었습니다! `!load` 명령어로 시작하세요.")
        else:
            # fetch로 재시도
            try:
                channel = await bot.fetch_channel(DISCORD_CHANNEL_ID)
                logging.info(f'채널 fetch 성공: {channel.name} (ID: {channel.id})')
                await channel.send("🤖 금오공대 통학버스 알리미가 시작되었습니다! `!load` 명령어로 시작하세요.")
            except Exception as fetch_error:
                logging.error(f'채널 접근 실패 (ID: {DISCORD_CHANNEL_ID}): {fetch_error}')
                logging.error('key.py 파일의 DISCORD_CHANNEL_ID가 올바른지 확인하세요.')
    except Exception as e:
        logging.error(f'채널 접근 중 오류: {e}')

    logging.info(f'봇이 연결되었습니다! 디스코드에서 `!help` 명령어를 사용해보세요.')
    
    if not scheduler.running:
        scheduler.start()
        logging.info("APScheduler 시작됨.")
        # 1시간 주기 전체 버스 스케줄 갱신 작업 추가 (모니터링 중인 버스가 없을 때만 동작)
        scheduler.add_job(scheduled_hourly_update, 'interval', hours=1, id='hourly_full_update')
        logging.info("1시간 주기 전체 버스 스케줄 갱신 작업이 추가되었습니다.")


# --- 디스코드 봇 명령어 ---
@bot.command(name='load', help='버스 조회 프로그램을 실행하고 초기 노선 정보를 로드합니다. (최초 1회 실행 권장)')
async def load_buses(ctx):
    await ctx.send("버스 조회 프로그램을 실행합니다. 잠시 기다려주세요...")

    def run_initial_crawl_thread_discord():
        logging.info("초기 크롤링 스레드 시작...")
        if update_bus_schedules(): # 여기서 한 번만 전체 크롤링
            asyncio.run_coroutine_threadsafe(
                ctx.send(f"로그인 및 초기 버스 노선 조회에 성공했습니다. ({len(current_bus_schedules)}개 노선 로드)\n`!list`를 입력하여 노선 리스트를 확인하세요."),
                bot.loop
            )
        else:
            asyncio.run_coroutine_threadsafe(
                ctx.send("로그인 및 초기 버스 노선 조회에 실패했거나, 노선 정보가 없습니다."),
                bot.loop
            )
        logging.info("초기 크롤링 스레드 완료.")

    threading.Thread(target=run_initial_crawl_thread_discord, daemon=True).start()


@bot.command(name='list', help='현재 로드된 버스 노선 리스트를 표시합니다.')
async def list_buses(ctx):
    # 가장 최신 데이터를 보여주기 위해 !list 명령 시에도 한 번 갱신 시도
    await ctx.send("버스 노선 정보를 갱신 중입니다. 잠시만 기다려주세요...")
    update_success = False
    def run_update_in_thread():
        nonlocal update_success
        update_success = update_bus_schedules() # 여기서 한 번만 전체 크롤링
    
    thread = threading.Thread(target=run_update_in_thread, daemon=True)
    thread.start()
    thread.join(timeout=30) # 갱신이 완료될 때까지 최대 30초 대기

    if not update_success:
        await ctx.send("버스 노선 정보 갱신에 실패했거나 시간이 초과되었습니다. 현재 캐시된 정보를 표시합니다.")
        if not current_bus_schedules:
            await ctx.send("현재 로드된 버스 노선 정보가 없습니다. `!load`를 입력하여 먼저 프로그램을 실행해주세요.")
            return

    with data_lock: # current_bus_schedules 접근 시 락 사용
        if current_bus_schedules:
            header = "🚌 현재 버스 노선 리스트:\n"
            if last_update_time:
                header += f"최종 갱신: {last_update_time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            else:
                header += "\n"

            bus_list_parts = []
            current_part = header

            for bus in current_bus_schedules:
                bus_info = (
                    f"[{bus['id']}] {bus['bus_type']} - {bus['bus_number']} ({bus['bus_vehicle']})\n"
                    f"  지역: {bus['bus_region']}\n"
                    f"  노선: {bus['bus_route_detail']}\n"
                    f"  좌석: {bus['current_seats']}/{bus['total_seats']}\n"
                    f"--------------------\n"
                )

                if len(current_part) + len(bus_info) > 1990:
                    bus_list_parts.append(current_part)
                    current_part = "🚌 버스 노선 리스트 (계속):\n"
                    if last_update_time:
                        current_part += f"최종 갱신: {last_update_time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    else:
                        current_part += "\n"

                current_part += bus_info

            if current_part.strip() != header.strip():
                bus_list_parts.append(current_part)
            elif not bus_list_parts and current_bus_schedules:
                bus_list_parts.append(current_part)

            for part in bus_list_parts:
                await ctx.send(part)
                await asyncio.sleep(0.5)

        else:
            await ctx.send("현재 로드된 버스 노선 정보가 없습니다. `!load`를 입력하여 먼저 프로그램을 실행해주세요.")


@bot.command(name='monitor', help='만석 알림을 받을 버스 번호(ID)를 설정합니다. 여러 버스를 모니터링할 수 있습니다. 예: `!monitor 5` 또는 `!monitor 5 12`')
async def monitor_bus(ctx, *bus_ids: str): # 여러 인자를 받을 수 있도록 변경
    global monitored_bus_ids

    if not bus_ids:
        await ctx.send("모니터링할 버스 ID를 입력해주세요. 예: `!monitor 5` 또는 `!monitor 5 12`")
        return

    if not current_bus_schedules:
        await ctx.send("버스 노선 정보가 로드되지 않았습니다. 먼저 `!load` 명령어를 실행해주세요.")
        return

    added_count = 0
    not_found_ids = []

    with data_lock: # monitored_bus_ids 접근 시 락 사용
        for bus_id in bus_ids:
            found_bus = next((bus for bus in current_bus_schedules if bus['id'] == bus_id), None)

            if found_bus:
                if bus_id not in monitored_bus_ids:
                    monitored_bus_ids.add(bus_id)
                    added_count += 1
                    # 첫 알림을 위해 last_monitored_seats 초기화
                    last_monitored_seats[bus_id] = None # 초기 상태를 None으로 설정하여 첫 크롤링 시 알림 트리거
                    await ctx.send(f"ID '{bus_id}'번 노선 만석 알림 모니터링 목록에 추가했습니다. 첫 좌석 현황을 확인 중...")
                else:
                    await ctx.send(f"ID '{bus_id}'번 노선은 이미 모니터링 중입니다.")
            else:
                not_found_ids.append(bus_id)
    
    if not_found_ids:
        await ctx.send(f"입력하신 ID {', '.join(not_found_ids)}는 존재하지 않습니다. `!list`를 입력하여 노선 리스트를 확인해주세요.")
    
    if added_count > 0:
        await ctx.send(f"총 {added_count}개의 버스 노선 모니터링을 시작했습니다.")
        # 메인 모니터링 잡이 없으면 추가
        if not scheduler.get_job('main_bus_monitor_job'):
            scheduler.add_job(monitor_all_monitored_buses_job, 'interval', minutes=1, id='main_bus_monitor_job')
            logging.info("메인 모니터링 잡 'main_bus_monitor_job' 시작됨.")
            # 잡이 추가된 직후 바로 한 번 실행하여 초기 상태 확인
            def run_initial_monitor_thread():
                logging.info("메인 모니터링 잡 초기 실행 스레드 시작...")
                monitor_all_monitored_buses_job()
                logging.info("메인 모니터링 잡 초기 실행 스레드 완료.")
            threading.Thread(target=run_initial_monitor_thread, daemon=True).start()


@bot.command(name='stop', help='버스 노선 모니터링을 중지합니다. 예: `!stop 5` (5번 버스 중지), `!stop all` (모든 버스 중지)')
async def stop_monitoring(ctx, bus_id_or_all: str = None):
    global monitored_bus_ids, last_monitored_seats

    if bus_id_or_all is None:
        await ctx.send("어떤 버스 모니터링을 중지할지 지정해주세요. 예: `!stop 5` (5번 버스 중지), `!stop all` (모든 버스 중지)")
        return

    stopped_count = 0
    with data_lock: # monitored_bus_ids, last_monitored_seats 접근 시 락 사용
        if bus_id_or_all.lower() == 'all':
            current_monitored = list(monitored_bus_ids) # Set을 iterate하면서 remove하면 오류 발생 가능 -> list로 변환 후 사용
            for bus_id in current_monitored:
                monitored_bus_ids.discard(bus_id)
                if bus_id in last_monitored_seats:
                    del last_monitored_seats[bus_id]
                stopped_count += 1
            
            if stopped_count > 0:
                await ctx.send(f"모든 ({stopped_count}개) 버스 노선 모니터링을 중지했습니다.")
            else:
                await ctx.send("현재 모니터링 중인 버스 노선이 없습니다.")
        else:
            bus_id = bus_id_or_all
            if bus_id in monitored_bus_ids:
                monitored_bus_ids.discard(bus_id)
                if bus_id in last_monitored_seats:
                    del last_monitored_seats[bus_id]
                stopped_count += 1
                await ctx.send(f"ID '{bus_id}'번 버스 노선 모니터링을 중지했습니다.")
            else:
                await ctx.send(f"ID '{bus_id}'번 버스 노선은 현재 모니터링 중이 아닙니다.")
    
    # 모든 모니터링이 중단되면 WebDriver도 닫고 메인 모니터링 잡도 중단
    if not monitored_bus_ids:
        logging.info("모든 모니터링이 중단되어 WebDriver를 닫고 메인 모니터링 잡을 중단합니다.")
        with webdriver_lock:
            close_webdriver()
        if scheduler.get_job('main_bus_monitor_job'):
            scheduler.remove_job('main_bus_monitor_job')
            logging.info("메인 모니터링 잡 'main_bus_monitor_job' 제거 완료.")


@bot.command(name='monitoring_list', help='현재 모니터링 중인 버스 노선 리스트를 표시합니다.')
async def monitoring_list(ctx):
    with data_lock: # monitored_bus_ids, current_bus_schedules 접근 시 락 사용
        if monitored_bus_ids:
            msg = "👀 **현재 모니터링 중인 버스 노선 ID:**\n"
            
            # 최신 버스 노선 정보로 current_bus_schedules를 갱신 (선택 사항이지만 최신 정보를 보여주는 것이 좋음)
            # 이 부분은 monitor_all_monitored_buses_job이 주기적으로 갱신하므로, 
            # 여기서는 캐시된 current_bus_schedules를 사용해도 무방
            # 만약 정말 즉각적인 최신 정보가 필요하면 여기서도 update_bus_schedules()를 호출할 수 있음.
            # 하지만 잦은 호출은 부하가 될 수 있으므로, 현재 캐시된 정보 사용을 우선 고려.
            # (현재 코드는 !list처럼 별도 스레드에서 업데이트를 시도하는 방식)

            for bus_id in sorted(list(monitored_bus_ids)):
                bus_info = next((bus for bus in current_bus_schedules if bus['id'] == bus_id), None)
                if bus_info:
                    msg += f"- ID: {bus_id}, 노선: {bus_info['bus_route_detail']}, 현재 좌석: {bus_info['current_seats']}/{bus_info['total_seats']}\n"
                else:
                    msg += f"- ID: {bus_id} (정보를 찾을 수 없음, `!load`로 갱신 필요)\n" 
            await ctx.send(msg)
        else:
            await ctx.send("현재 모니터링 중인 버스 노선이 없습니다. `!monitor [버스ID]` 명령어로 모니터링을 시작하세요.")


@bot.command(name='status', help='현재 봇 상태와 채널 정보를 확인합니다.')
async def bot_status(ctx):
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
        status_msg += f"• 메인 모니터링 잡 활성화: {'예' if scheduler.get_job('main_bus_monitor_job') else '아니오'}\n"
        
        await ctx.send(status_msg)

@bot.command(name='help', help='사용 가능한 모든 명령어를 표시합니다.')
async def show_help(ctx):
    help_text = "📚 **사용 가능한 명령어:**\n"
    for command in bot.commands:
        if not command.hidden: # 숨겨진 명령어는 제외
            help_text += f"• `!{command.name}`: {command.help}\n"
    await ctx.send(help_text)

# 봇 실행
if __name__ == '__main__':
    bot.run(DISCORD_BOT_TOKEN)