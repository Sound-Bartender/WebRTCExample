import os
import subprocess
import threading
import time
import sys
from picamera2 import Picamera2
import pyaudio

# --- 설정 ---
SERVER_IP = "192.168.50.236"      # 서버의 고정 IP (환경에 맞게 수정)
UDP_PORT = 5003                  # 전송 포트
VIDEO_PIPE = "/tmp/video_pipe"   # 영상용 FIFO
AUDIO_PIPE = "/tmp/audio_pipe"   # 오디오용 FIFO

def create_fifo(pipe_path):
    """FIFO(이름있는 파이프)가 없으면 생성"""
    if not os.path.exists(pipe_path):
        os.mkfifo(pipe_path)
        print(f"Created FIFO: {pipe_path}")

def start_ffmpeg():
    """
    ffmpeg 프로세스 시작  
    - /tmp/video_pipe: H264 영상 입력  
    - /tmp/audio_pipe: PCM(s16le, 44100Hz, 모노) 오디오 입력  
    - MPEG‑TS 컨테이너로 패키징 후 UDP로 전송  
    """
    cmd = [
        "ffmpeg",
        "-thread_queue_size", "512",
        "-f", "h264",
        "-i", VIDEO_PIPE,
        "-thread_queue_size", "512",
        "-f", "s16le",
        "-ar", "44100",
        "-ac", "1",
        "-i", AUDIO_PIPE,
        "-c:v", "copy",    # 영상은 재인코딩 없이 복사
        "-c:a", "aac",     # 오디오는 AAC 인코딩
        "-f", "mpegts",    # MPEG-TS 컨테이너 (UDP 환경에서 안정적)
        f"udp://{SERVER_IP}:{UDP_PORT}?pkt_size=1316"
    ]
    print("ffmpeg 시작:", " ".join(cmd))
    return subprocess.Popen(cmd)

def video_capture():
    """picamera2로 영상 캡처 후 FIFO에 기록"""
    # FIFO를 쓰기 모드로 오픈 (블록 모드이므로 ffmpeg가 먼저 읽고 있어야 함)
    with open(VIDEO_PIPE, "wb") as video_fifo:
        picam2 = Picamera2()
        config = picam2.create_video_configuration()
        picam2.configure(config)
        # picamera2는 file-like 객체를 인자로 받아 H264 스트림을 출력할 수 있음
        picam2.start_recording(video_fifo, format="h264")
        print("영상 캡처 시작")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        picam2.stop_recording()
        print("영상 캡처 종료")

def audio_capture():
    """PyAudio로 마이크 입력 캡처 후 FIFO에 기록"""
    p = pyaudio.PyAudio()
    FORMAT = pyaudio.paInt16  # 16비트 PCM
    CHANNELS = 1              # 모노
    RATE = 44100              # 샘플링 주파수
    CHUNK = 1024              # 버퍼 크기

    stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE,
                    input=True, frames_per_buffer=CHUNK)
    # FIFO를 쓰기 모드로 오픈
    with open(AUDIO_PIPE, "wb") as audio_fifo:
        print("오디오 캡처 시작")
        try:
            while True:
                data = stream.read(CHUNK, exception_on_overflow=False)
                audio_fifo.write(data)
                audio_fifo.flush()
        except KeyboardInterrupt:
            pass
        stream.stop_stream()
        stream.close()
        p.terminate()
        print("오디오 캡처 종료")

def main():
    # FIFO 생성
    create_fifo(VIDEO_PIPE)
    create_fifo(AUDIO_PIPE)
    
    # ffmpeg 프로세스 시작
    ffmpeg_proc = start_ffmpeg()

    # 영상과 오디오 캡처 스레드 시작
    video_thread = threading.Thread(target=video_capture, daemon=True)
    audio_thread = threading.Thread(target=audio_capture, daemon=True)
    video_thread.start()
    audio_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("클라이언트 스트리밍 종료")
        ffmpeg_proc.terminate()
        ffmpeg_proc.wait()
        sys.exit(0)

if __name__ == "__main__":
    main()