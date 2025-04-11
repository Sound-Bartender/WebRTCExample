import asyncio
import websockets
import numpy as np
import sounddevice as sd
import logging
import threading
import queue
import time
import struct
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput # 스트리밍 위한 커스텀 Output 필요 (이전 코드와 동일 가정)

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 설정 변경 ---
RPI_IP = '0.0.0.0'
PORT = 5556
VIDEO_WIDTH = 640
VIDEO_HEIGHT = 360
VIDEO_FRAMERATE = 25 # 25 FPS로 변경
AUDIO_SAMPLERATE = 16000 # 16000 Hz 확인
AUDIO_CHANNELS = 1
AUDIO_BLOCKSIZE = 640 # 콜백 빈도 및 청크 크기 결정 (16000 / 1024 ~= 15.6회/초 콜백)
AUDIO_DTYPE = 'int16'

# --- 데이터 타입 플래그 ---
TYPE_VIDEO = 0x01
TYPE_AUDIO = 0x02
TYPE_PROCESSED_AUDIO = 0x03
TYPE_CONFIG_FRAME = 0x06 # SPS/PPS 포함된 첫 프레임 데이터용 타입 추가

# --- 스레드 간 통신 큐 ---
# 큐 크기는 네트워크 상태 및 처리 속도에 따라 조절 필요
video_queue = queue.Queue(maxsize=int(VIDEO_FRAMERATE * 1.5)) # 약 1.5초 분량 버퍼
audio_queue = queue.Queue(maxsize=int((AUDIO_SAMPLERATE / AUDIO_BLOCKSIZE) * 1.5)) # 약 1.5초 분량 버퍼
processed_audio_queue = queue.Queue(maxsize=30)

# --- SPS/PPS 준비 상태 알림용 Event ---
headers_ready_event = asyncio.Event()
first_config_frame_data = None # 첫 프레임 데이터 저장용

# --- 종료 플래그 ---
stop_event = threading.Event()

# --- WebSocket 클라이언트 관리 ---
connected_clients = set()

# --- Picamera2 H.264 스트리밍을 위한 커스텀 Output ---
# (이전 코드와 동일하다고 가정 - H.264 프레임을 video_queue에 넣음)
class WebSocketVideoOutput(FileOutput):
    def __init__(self, q):
        super().__init__('-')
        self.queue = q
        self.headers_captured = False # 헤더 캡처 완료 플래그
        self.last_warning_time = 0
        # self.buffer = bytearray() # H.264 NAL 처리 시 필요할 수 있음

    def outputframe(self, frame, keyframe=True, timestamp=None, packet=None, audio=False):
        current_time = time.monotonic()

        if frame:
            # logging.debug(f"Video frame received: {len(frame)} bytes")
            if self.queue.full(): # 꽉 찬 경우 큐를 비움
                # queue.get_nowait()

                self.queue.get()  # 또는 queue.get_nowait()와 동일
                if current_time - self.last_warning_time > 5.0:
                    logging.warning(f"Video queue full, dropping frame. {current_time}")
                    self.last_warning_time = current_time

            if not self.headers_captured:
                # 첫 번째 유효한 프레임 데이터를 설정 데이터로 간주하고 저장
                logging.info(f"Captured first frame data (config frame), size: {len(frame)}")
                first_config_frame_data = frame # 데이터 저장
                self.headers_captured = True  # 캡처 완료 표시
                headers_ready_event.set()     # 이벤트 설정하여 send_data에 알림
                # 첫 프레임은 일반 video_queue에 넣지 않을 수 있음 (선택)
            else:
                self.queue.put((frame, current_time), block=False)
        else:
            logging.warning(f'video frame is not present')

# --- 비디오 캡처 스레드 ---
def video_capture_thread():
    picam2 = Picamera2()
    try:
        video_config = picam2.create_video_configuration(
            main={"size": (VIDEO_WIDTH, VIDEO_HEIGHT), "format": "RGB888"},
            controls={"FrameRate": VIDEO_FRAMERATE} # 프레임레이트 설정 적용
        )
        picam2.configure(video_config)
        # 비트레이트는 네트워크 대역폭에 맞춰 조절 필요
        encoder = H264Encoder(bitrate=1500000, repeat=True, iperiod=int(VIDEO_FRAMERATE/2)) # GOP 조절
        output = WebSocketVideoOutput(video_queue)

        picam2.start_recording(encoder, output)
        logging.info(f"Video capture started at {VIDEO_FRAMERATE} FPS.")

        stop_event.wait() # 종료 신호 대기

    except Exception as e:
        logging.error(f"Video capture error: {e}")
    finally:
        if picam2.is_open:
            try:
                picam2.stop_recording()
            except Exception as e:
                logging.error(f"Error stopping recording: {e}")
            picam2.close()
        logging.info("Video capture stopped.")

# --- 오디오 캡처 스레드 ---
def audio_capture_thread():
    last_warning_time = 0
    def audio_callback(indata, frames, time_info, status):
        nonlocal last_warning_time
        current_time = time.monotonic()
        if status:
            # 경고 메시지가 너무 자주 뜨는 것을 방지 (예: 5초에 한 번)
            if current_time - last_warning_time > 5.0:
                logging.warning(f"Audio status: {status}")
                last_warning_time = current_time
            # logging.debug(f"Video frame received: {len(frame)} bytes")
        if audio_queue.full(): # 꽉 찬 경우 큐를 비움
            # queue.get_nowait()
            audio_queue.get()  # 또는 queue.get_nowait()와 동일
            if current_time - last_warning_time > 5.0:
                logging.warning(f"Audio queue full, dropping frame. {current_time}")
                last_warning_time = current_time

        audio_data = indata.tobytes()
        audio_queue.put((audio_data, current_time), block=False) # 튜플로 저장
        # try:
        #     audio_data = indata.tobytes()
        #     # logging.debug(f"Audio chunk captured: {len(audio_data)} bytes")
        #     # audio_queue.put(audio_data, block=False)
        #     audio_queue.put((audio_data, current_time), block=False) # 튜플로 저장
        # except queue.Full:
        #     if current_time - last_warning_time > 5.0:
        #          logging.warning(f"Audio queue full, dropping chunk. {current_time}")
        #          last_warning_time = current_time
        #     pass
        # except Exception as e:
        #     logging.error(f"Error in audio callback: {e}")

    try:
        with sd.InputStream(samplerate=AUDIO_SAMPLERATE, # 16000 Hz 설정
                            blocksize=AUDIO_BLOCKSIZE,
                            channels=AUDIO_CHANNELS,
                            dtype=AUDIO_DTYPE,
                            callback=audio_callback):
            logging.info(f"Audio capture started at {AUDIO_SAMPLERATE} Hz.")
            stop_event.wait() # 종료 신호 대기
    except sd.PortAudioError as e:
         logging.error(f"PortAudio error during audio capture: {e}")
         logging.error("Please ensure audio input device is available and configured.")
    except Exception as e:
        logging.error(f"Audio capture error: {e}")
    finally:
        logging.info("Audio capture stopped.")

# --- 처리된 오디오 재생 스레드 ---
# (이전 코드와 동일 - 필요 시 수정)
def audio_playback_thread():
    try:
        with sd.OutputStream(samplerate=AUDIO_SAMPLERATE,
                             blocksize=AUDIO_BLOCKSIZE,
                             channels=AUDIO_CHANNELS,
                             dtype=AUDIO_DTYPE) as stream:
            logging.info("Audio playback started.")
            while not stop_event.is_set():
                try:
                    processed_data = processed_audio_queue.get(block=True, timeout=0.5)
                    processed_array = np.frombuffer(processed_data, dtype=AUDIO_DTYPE)
                    stream.write(processed_array)
                    # logging.debug(f"Playing processed audio: {len(processed_data)} bytes")
                except queue.Empty:
                    continue
                except Exception as e:
                    logging.error(f"Audio playback error: {e}")
                    time.sleep(0.1)
    except sd.PortAudioError as e:
         logging.error(f"PortAudio error during audio playback: {e}")
         logging.error("Please ensure audio output device is available.")
    except Exception as e:
        logging.error(f"Failed to open audio output stream: {e}")
    finally:
        logging.info("Audio playback stopped.")

# --- WebSocket 핸들러 ---
# (이전 코드와 거의 동일, send_data / receive_data 호출)
async def handler(websocket):
    global connected_clients
    if websocket in connected_clients: # 중복 연결 방지 (필요 시)
        logging.warning(f"Client {websocket.remote_address} already connected. Ignoring new connection.")
        return

    connected_clients.add(websocket)
    logging.info(f"Client connected: {websocket.remote_address}")

    # 데이터 전송 및 수신을 위한 비동기 작업 생성
    send_task = asyncio.create_task(send_data(websocket))
    receive_task = asyncio.create_task(receive_data(websocket))

    try:
        done, pending = await asyncio.wait(
            [send_task, receive_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except Exception as e:
        logging.error(f"Handler error for {websocket.remote_address}: {e}")
    finally:
        if websocket in connected_clients:
             connected_clients.remove(websocket)
        logging.info(f"Client disconnected: {websocket.remote_address}")
        # 작업이 정상적으로 취소되었는지 확인 (디버깅 시)
        # if send_task and not send_task.done(): send_task.cancel()
        # if receive_task and not receive_task.done(): receive_task.cancel()


# --- 데이터 전송 로직 (가변 슬립 적용) ---
async def send_data(websocket):
    """Websocket을 통해 비디오 및 오디오 데이터를 클라이언트로 전송합니다.
    목표 인터벌(1초)을 유지하기 위해 가변 슬립 시간을 사용합니다."""
    global first_config_frame_data

    target_interval = 1 / VIDEO_FRAMERATE  # 목표 전송 주기 (초)
    # 전송할 데이터 타입과 큐 매핑
    data_queues = {
        TYPE_VIDEO: video_queue,
        TYPE_AUDIO: audio_queue,
    }
    # 데이터 타입별 설명 (로깅용)
    data_desc = {
        TYPE_VIDEO: "video",
        TYPE_AUDIO: "audio",
    }

    last_cycle_start_time = time.monotonic()

    # 1. SPS/PPS 포함된 첫 프레임 데이터가 준비될 때까지 대기
    logging.info("Waiting for config frame data (SPS/PPS)...")
    await headers_ready_event.wait()
    logging.info("Config frame data ready.")

    if first_config_frame_data:
        # 2. Config Frame 전송
        timestamp = 0.0 # 설정 데이터는 타임스탬프 무관
        header = struct.pack('>BdI', TYPE_CONFIG_FRAME, timestamp, len(first_config_frame_data))
        await websocket.send(header + first_config_frame_data)
        logging.info(f"Sent config frame ({len(first_config_frame_data)} bytes) to client.")
        # first_config_frame_data = None # 한 번 보낸 후 비워도 됨

    else:
        logging.error("Config frame data was not captured!")
        # 오류 처리 필요
    # while websocket.open:
    while True: # 무한 루프 시작
        current_cycle_start_time = time.monotonic()
        next_cycle_target_time = last_cycle_start_time + target_interval
        frames_sent_this_cycle = 0
        audio_chunks_sent_this_cycle = 0

        # --- 1초 동안 가능한 많은 데이터 전송 ---
        # 목표 시간까지 또는 큐가 빌 때까지 계속 시도
        # while time.monotonic() < next_cycle_target_time:
        processed_something = False
        for data_type, data_q in data_queues.items():
            try:
                # 큐에서 데이터 가져오기 (non-blocking)
                # data = data_q.get_nowait()
                data, timestamp = data_q.get_nowait() # 튜플 언패킹
                # 메시지 헤더 생성: [Type(1)][Timestamp(8)][Length(4)]
                # 'd'는 double(8바이트), '>'는 big-endian
                header = struct.pack('>BdI', data_type, timestamp, len(data))

                # 데이터 전송
                await websocket.send(header + data)

                # 통계 업데이트
                if data_type == TYPE_VIDEO: frames_sent_this_cycle += 1
                elif data_type == TYPE_AUDIO: audio_chunks_sent_this_cycle += 1
                processed_something = True
                # logging.debug(f"Sent {data_desc[data_type]} chunk: {len(data)} bytes")

            except queue.Empty:
                # 해당 타입의 큐가 비어있으면 다음 타입으로 넘어감
                continue
            except websockets.exceptions.ConnectionClosed:
                logging.warning("Connection closed during send.")
                return # 핸들러에서 처리하므로 함수 종료
            except Exception as e:
                logging.error(f"Error sending {data_desc[data_type]} data: {e}")
                # 연결 오류 시 함수 종료 또는 재시도 로직 필요
                # await asyncio.sleep(0.1) # 잠시 후 재시도? 또는 return
                return # 에러 시 함수 종료

        # 이번 루프에서 아무 데이터도 처리하지 못했고, 아직 목표 시간 전이면 잠깐 sleep
        if not processed_something and time.monotonic() < next_cycle_target_time:
            await asyncio.sleep(0.001) # CPU 사용량 줄이기 위한 짧은 대기

        # --- 1초 주기 맞추기 위한 가변 슬립 ---
        cycle_end_time = time.monotonic()
        cycle_duration = cycle_end_time - last_cycle_start_time
        sleep_duration = target_interval - cycle_duration

        logging.info(f"Send cycle took {cycle_duration:.4f}s. "
                      f"Sent: {frames_sent_this_cycle} video, {audio_chunks_sent_this_cycle} audio. "
                      f"Calculated sleep: {sleep_duration:.4f}s")

        if sleep_duration > 0:
            await asyncio.sleep(sleep_duration)
            last_cycle_start_time = next_cycle_target_time # 정확히 1초 뒤 시작하도록
        else:
            # 목표 시간(1초)보다 오래 걸린 경우
            logging.warning(f"Send cycle exceeded target interval by {-sleep_duration:.4f}s.")
            # 즉시 다음 사이클 시작 (밀린 시간 보상 시도)
            last_cycle_start_time = time.monotonic() # 현재 시간 기준으로 다음 1초 시작

# --- 데이터 수신 로직 (처리된 오디오) ---
# (이전 코드와 동일 - 필요 시 수정)
async def receive_data(websocket):
    # while websocket.open:
    while True:
        try:
            message = await websocket.recv()
            if isinstance(message, bytes):
                if len(message) > 5:
                    header = message[:5]
                    payload = message[5:]
                    msg_type, msg_len = struct.unpack('>BI', header)

                    if msg_type == TYPE_PROCESSED_AUDIO and len(payload) == msg_len:
                         # logging.debug(f"Received processed audio: {msg_len} bytes")
                         try:
                            processed_audio_queue.put(payload, block=False)
                         except queue.Full:
                             logging.warning("Processed audio queue full, dropping chunk.")
                             pass
                    else:
                         logging.warning(f"Received unexpected msg type({msg_type}) or length (exp:{msg_len}, got:{len(payload)})")
                else:
                    logging.warning(f"Received short binary message: {len(message)} bytes")

        except websockets.exceptions.ConnectionClosed:
            logging.info("Connection closed by client while receiving.")
            break
        except Exception as e:
            logging.error(f"Error receiving data: {e}")
            break

# --- 메인 실행 ---
async def main():
    # 백그라운드 스레드 시작
    video_thread = threading.Thread(target=video_capture_thread, daemon=True)
    audio_capture_thread_instance = threading.Thread(target=audio_capture_thread, daemon=True)
    audio_playback_thread_instance = threading.Thread(target=audio_playback_thread, daemon=True)

    video_thread.start()
    # 카메라 및 오디오 장치 초기화 시간 확보
    await asyncio.sleep(2)
    audio_capture_thread_instance.start()
    audio_playback_thread_instance.start()

    # WebSocket 서버 시작
    # IPv6 사용 시: websockets.serve(handler, "::", PORT)
    try:
        async with websockets.serve(handler, RPI_IP, PORT,
                                    # 메시지 크기 제한 증가 (1초 분량 데이터가 클 수 있음)
                                    # 기본값은 1MB, 필요 시 더 늘려야 함
                                    max_size=2*1024*1024, # 예: 2MB
                                    # 핑 간격 설정 (연결 유지 확인)
                                    ping_interval=20,
                                    ping_timeout=20):
            logging.info(f"WebSocket server started on ws://{RPI_IP}:{PORT}")
            await asyncio.Future() # 서버 무한 실행
    except OSError as e:
        logging.error(f"Failed to start WebSocket server: {e}")
        logging.error("Is the port already in use or permission denied?")
    except Exception as e:
         logging.error(f"An unexpected error occurred in main: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Ctrl+C pressed. Stopping server...")
    finally:
        stop_event.set() # 모든 스레드에 종료 신호 전달
        logging.info("Server shutdown sequence initiated.")
        # 여기서 스레드가 완전히 종료될 때까지 기다리는 로직 추가 가능 (join)
        # 하지만 daemon=True 이므로 메인 스레드 종료 시 자동 종료됨