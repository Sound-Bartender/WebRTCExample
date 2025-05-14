import socket
import threading
import time
import struct
import logging
import pyaudio
import cv2

# --- 설정 ---
HOST = '0.0.0.0'  # 모든 인터페이스에서 연결 허용
PORT = 9999
VIDEO_WIDTH = 640
VIDEO_HEIGHT = 480
# FPS = 25.0
FPS = 25.0

# 오디오 설정 (안드로이드와 일치해야 할 수 있음)
AUDIO_CHUNK = 640  # 좀 더 큰 청크 사용 시도
AUDIO_FORMAT = pyaudio.paInt16
AUDIO_CHANNELS = 1
AUDIO_RATE = 16000  # AI 모델이 요구하는 샘플링 레이트로 설정하는 것이 좋음

# 데이터 타입 정의 (안드로이드와 일치해야 함)
TYPE_VIDEO = 0
TYPE_AUDIO = 1
TYPE_ENHANCED_AUDIO = 2  # 폰 -> 파이

# 로깅 설정
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 글로벌 변수 ---
client_socket = None
client_address = None
stop_event = threading.Event()
picam2 = None
audio_stream_in = None
audio_stream_out = None
p = None


# --- 데이터 전송 함수 ---
def send_data(sock, data_type, timestamp, payload):
    """데이터 타입, 타임스탬프, 페이로드를 패킹하여 소켓으로 전송"""
    if not sock:
        return False
    try:
        payload_len = len(payload)
        # '!B q I' : Network byte order, unsigned char (1), long long (8), unsigned int (4)
        header = struct.pack('!B q I', data_type, timestamp, payload_len)
        sock.sendall(header + payload)
        # logging.debug(f"Sent: Type={data_type}, TS={timestamp}, Len={payload_len}")
        return True
    except (socket.error, BrokenPipeError, ConnectionResetError) as e:
        logging.error(f"데이터 전송 오류: {e}")
        stop_event.set()  # 오류 발생 시 모든 스레드 중지 신호
        return False
    except Exception as e:
        logging.error(f"예상치 못한 전송 오류: {e}")
        stop_event.set()
        return False


def resize_and_crop(frame, target_width=360, target_height=240):
    original_height, original_width = frame.shape[:2]
    target_ratio = target_width / target_height
    original_ratio = original_width / original_height

    # Step 1: 비율 유지하며 리사이즈 (크게 맞춰놓기)
    if original_ratio > target_ratio:
        # 원본이 더 가로로 넓음 -> 세로 기준 맞추고 가로는 나중에 자름
        new_height = target_height
        new_width = int(original_width * (target_height / original_height))
    else:
        # 원본이 더 세로로 높음 -> 가로 기준 맞추고 세로는 나중에 자름
        new_width = target_width
        new_height = int(original_height * (target_width / original_width))

    resized = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_AREA)

    # Step 2: 중앙 crop
    x_start = (new_width - target_width) // 2
    y_start = (new_height - target_height) // 2
    cropped = resized[y_start:y_start + target_height, x_start:x_start + target_width]

    return cropped

# --- 비디오 스트리밍 스레드 ---
def video_stream_thread(sock):
    logging.info("비디오 스트리밍 스레드 시작")

    # 맥북 웹캠은 30fps 고정이라서 그냥 돌아가는 구나~ 정도만 확인하면 OK
    camera = cv2.VideoCapture(0)
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, VIDEO_WIDTH)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, VIDEO_HEIGHT)
    # camera.set(cv2.CAP_PROP_FPS, FPS)

    try:
        # picam2 = Picamera2()
        # # XRGB8888은 cv2에서 사용하기 좋고, MJPEG 인코딩 전에 필요합니다.
        # config = picam2.create_video_configuration(
        #     main={"size": (VIDEO_WIDTH, VIDEO_HEIGHT), "format": 'RGB888'},
        #     controls={"FrameRate": FPS}
        # )
        # picam2.configure(config)
        # picam2.start()
        time.sleep(1)  # 카메라 안정화 시간

        frame_interval = 1.0 / FPS
        start_time = time.monotonic()  # 루프 시작 시간 기록

        while not stop_event.is_set():
            ts = time.time_ns()  # 데이터 타임스탬프

            time1 = time.monotonic()
            # 프레임 캡처 (배열로)
            # frame_array = picam2.capture_array()  # RGB 형식
            ret, frame_array = camera.read()
            if not ret:
                print('?')
                break
            time2 = time.monotonic()

            frame_array = resize_and_crop(frame_array)
            time3 = time.monotonic()
            # print('shape:', frame_array.shape)

            # OpenCV를 사용하여 MJPEG(JPEG)으로 인코딩
            # cv2.imencode는 BGR 형식을 기대할 수 있으므로 변환 필요 (RGB->BGR)
            # frame_bgr = cv2.cvtColor(frame_array, cv2.COLOR_RGB2BGR) # capture_array가 RGB일 때
            # _, img_encoded = cv2.imencode('.jpg', frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90]) # 품질 90

            # capture_array()가 BGR을 반환하면 바로 사용
            # BGR 형식으로 가정하고 인코딩
            is_success, img_encoded = cv2.imencode('.jpg', frame_array, [int(cv2.IMWRITE_JPEG_QUALITY), 90])  # 품질 90

            time4 = time.monotonic()
            if is_success:
                frame_data = img_encoded.tobytes()
                if not send_data(sock, TYPE_VIDEO, ts, frame_data):
                    logging.warning('전송중 오류')
                    break  # 전송 실패 시 루프 종료
            else:
                logging.warning("JPEG 인코딩 실패")
            time5 = time.monotonic()
            # print(time2-time1, time3-time2, time4-time3, time5-time4, is_success)
            # FPS 유지 로직
            elapsed_time = time.monotonic() - start_time
            sleep_time = frame_interval - elapsed_time
            if sleep_time > 0:
                # time.sleep(sleep_time)
                # print('sleep time:', sleep_time)
                start_time += frame_interval
            else:
                current_time = time.monotonic()
                logging.warning(f'시간 재설정 {elapsed_time} {current_time} {-sleep_time} {current_time - start_time - frame_interval} 딜레이')
                start_time = current_time

            # else:
            #     logging.warning(f"프레임 처리 시간 초과: {elapsed_time:.4f}s")
    except Exception as e:
        logging.error(f"비디오 스트리밍 스레드 오류: {e}")
    finally:
        camera.release()
        # if picam2:
        #     picam2.stop()
        #     logging.info("카메라 정지됨.")
        logging.info("비디오 스트리밍 스레드 종료")


# --- 오디오 스트리밍 스레드 ---
def audio_stream_thread(sock):
    global p, audio_stream_in

    logging.info("오디오 스트리밍 스레드 시작")
    p = pyaudio.PyAudio()
    try:
        audio_stream_in = p.open(format=AUDIO_FORMAT,
                                 channels=AUDIO_CHANNELS,
                                 rate=AUDIO_RATE,
                                 input=True,
                                 frames_per_buffer=AUDIO_CHUNK)

        logging.info("오디오 입력 스트림 열림")

        while not stop_event.is_set() and audio_stream_in.is_active():
            ts = time.time_ns()  # 타임스탬프
            try:
                audio_data = audio_stream_in.read(AUDIO_CHUNK, exception_on_overflow=False)
                if not send_data(sock, TYPE_AUDIO, ts, audio_data):
                    break  # 전송 실패 시 루프 종료
            except IOError as e:
                logging.error(f"오디오 읽기 오류: {e}")
                time.sleep(0.1)  # 잠시 대기 후 재시도

    except Exception as e:
        logging.error(f"오디오 스트리밍 스레드 오류: {e}")
    finally:
        if audio_stream_in:
            try:
                audio_stream_in.stop_stream()
                audio_stream_in.close()
                logging.info("오디오 입력 스트림 닫힘")
            except Exception as e_close:
                logging.error(f"오디오 입력 스트림 닫기 오류: {e_close}")
        if p:
            p.terminate()
            logging.info("PyAudio 종료됨 (Audio Stream Thread)")
        logging.info("오디오 스트리밍 스레드 종료")


# --- 개선된 오디오 수신 및 재생 스레드 ---
def audio_receive_thread(sock):
    global p, audio_stream_out

    logging.info("오디오 수신 스레드 시작")
    p_recv = pyaudio.PyAudio()  # 별도 인스턴스 사용 시도
    audio_stream_out = None

    try:
        audio_stream_out = p_recv.open(
            format=AUDIO_FORMAT,  # 원본과 동일 포맷 가정
            channels=AUDIO_CHANNELS,
            rate=AUDIO_RATE,
            output=True,
            frames_per_buffer=AUDIO_CHUNK
        )
        logging.info("오디오 출력 스트림 열림")

        header_size = 13  # Type(1) + Timestamp(8) + Length(4)

        while not stop_event.is_set() and audio_stream_out.is_active():
            try:
                # 1. 헤더 수신
                header_bytes = sock.recv(header_size)
                if not header_bytes:
                    logging.warning("클라이언트 연결 끊김 (헤더 수신 중)")
                    stop_event.set()
                    break
                if len(header_bytes) < header_size:
                    logging.warning(f"헤더 데이터 부족: {len(header_bytes)}/{header_size} bytes 수신. 연결 문제 가능성.")
                    stop_event.set()  # 불완전한 데이터는 중지 유발
                    break

                data_type, timestamp, payload_len = struct.unpack('!B q I', header_bytes)
                # logging.debug(f"Recv Header: Type={data_type}, TS={timestamp}, Len={payload_len}")

                # 2. 페이로드 수신
                if payload_len > 0:
                    payload_bytes = b''
                    remaining = payload_len
                    while remaining > 0 and not stop_event.is_set():
                        chunk = sock.recv(min(remaining, 4096))  # 최대 4KB씩 읽기
                        if not chunk:
                            logging.warning("클라이언트 연결 끊김 (페이로드 수신 중)")
                            stop_event.set()
                            break
                        payload_bytes += chunk
                        remaining -= len(chunk)

                    if stop_event.is_set():  # 중간에 중지 신호가 오면 종료
                        break

                    if len(payload_bytes) != payload_len:
                        logging.warning(f"페이로드 데이터 불일치: {len(payload_bytes)}/{payload_len} bytes 수신.")
                        # 이 경우 데이터를 버리고 다음 헤더를 기다리거나 연결을 끊을 수 있음
                        continue  # 일단 다음 데이터 시도

                    # 3. 데이터 처리 (개선된 오디오만 처리)
                    if data_type == TYPE_ENHANCED_AUDIO:
                        logging.debug(f"Enhanced audio received: {payload_len} bytes")
                        try:
                            audio_stream_out.write(payload_bytes)
                        except IOError as e_write:
                            logging.error(f"오디오 쓰기 오류: {e_write}")
                            # 스트림이 닫혔을 수 있음, 재시도 로직 추가 가능
                            time.sleep(0.1)
                    else:
                        logging.warning(f"예상치 않은 데이터 타입 수신: {data_type}")

            except (socket.error, ConnectionResetError, BrokenPipeError) as e:
                logging.error(f"수신 소켓 오류: {e}")
                stop_event.set()
                break
            except struct.error as e:
                logging.error(f"데이터 언패킹 오류: {e}. 헤더 사이즈({header_size}) 또는 데이터 손상 확인 필요.")
                stop_event.set()
                break
            except Exception as e:
                logging.error(f"오디오 수신 스레드 루프 오류: {e}")
                stop_event.set()
                break

    except Exception as e:
        logging.error(f"오디오 수신 스레드 초기화/종료 오류: {e}")
    finally:
        if audio_stream_out:
            try:
                if audio_stream_out.is_active():
                    audio_stream_out.stop_stream()
                audio_stream_out.close()
                logging.info("오디오 출력 스트림 닫힘")
            except Exception as e_close:
                logging.error(f"오디오 출력 스트림 닫기 오류: {e_close}")
        if p_recv:
            p_recv.terminate()
            logging.info("PyAudio 종료됨 (Audio Receive Thread)")
        logging.info("오디오 수신 스레드 종료")


# --- 메인 서버 로직 ---
def main():
    global client_socket, client_address, stop_event

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # 주소 재사용 옵션
    server_socket.bind((HOST, PORT))
    server_socket.listen(1)
    logging.info(f"서버 시작됨. 클라이언트 연결 대기 중 ({HOST}:{PORT})...")
    logging.info(f'width: {VIDEO_WIDTH} height: {VIDEO_HEIGHT}')

    try:
        while not stop_event.is_set():
            try:
                # 클라이언트 연결 수락 (타임아웃 설정)
                server_socket.settimeout(1.0)  # 1초마다 stop_event 확인
                client_socket, client_address = server_socket.accept()
                server_socket.settimeout(None)  # 연결 후 타임아웃 해제
                logging.info(f"클라이언트 연결됨: {client_address}")

                # 스레드 초기화 및 시작
                threads = []
                video_thread = threading.Thread(target=video_stream_thread, args=(client_socket,))
                threads.append(video_thread)
                audio_send_thread = threading.Thread(target=audio_stream_thread, args=(client_socket,))
                audio_recv_thread = threading.Thread(target=audio_receive_thread, args=(client_socket,))
                threads.append(audio_send_thread)
                threads.append(audio_recv_thread)

                if not threads:
                    logging.error("사용 가능한 기능(비디오/오디오)이 없어 스레드를 시작할 수 없습니다.")
                    if client_socket: client_socket.close()
                    break  # 또는 return

                for t in threads:
                    t.start()

                # 모든 스레드가 종료될 때까지 대기
                for t in threads:
                    t.join()

                logging.info("모든 스레드 종료됨. 클라이언트 연결 해제.")

            except socket.timeout:
                # 타임아웃은 정상적인 상황 (stop_event 체크 위함)
                continue
            except KeyboardInterrupt:
                logging.info("Ctrl+C 감지. 서버 종료 중...")
                stop_event.set()
                break
            except Exception as e:
                logging.error(f"메인 루프 오류: {e}")
                stop_event.set()  # 오류 발생 시 종료
                break
            finally:
                # 클라이언트 소켓 정리
                if client_socket:
                    try:
                        client_socket.shutdown(socket.SHUT_RDWR)
                        client_socket.close()
                        logging.info("클라이언트 소켓 닫힘.")
                    except OSError as e:
                        logging.warning(f"클라이언트 소켓 닫기 오류 (이미 닫혔을 수 있음): {e}")
                    client_socket = None  # 리셋
                # logging.info("다음 클라이언트 연결 대기...")
                # stop_event가 설정되지 않았다면, 다시 루프를 돌며 연결을 기다림
                # stop_event가 설정되었다면, 바깥 루프도 종료됨

    finally:
        # 서버 소켓 정리
        if server_socket:
            server_socket.close()
            logging.info("서버 소켓 닫힘.")
        # 혹시 남아있을 수 있는 PyAudio 인스턴스 종료 (각 스레드에서 종료하지만 안전장치)
        # if p and p._streams: # Check if p was initialized and has streams
        #      p.terminate()
        #      logging.info("메인 스레드에서 PyAudio 최종 종료 시도.")

        logging.info("서버 프로그램 종료.")


if __name__ == "__main__":
    main()
