import subprocess

def send_video():
    # 수신(서버) 측 IP와 포트
    # SERVER_IP = "172.30.1.16"   # 예시: 서버 컴퓨터의 내부망 IP
    SERVER_IP = "192.168.50.236"   # 예시: 서버 컴퓨터의 내부망 IP
    SERVER_PORT = 5002

    width = 640
    height = 480
    fps = 15
    bitrate = 0.25 * 1000 * 1000
    
    # # raspivid(또는 libcamera-vid) 명령어를 실행하여 H.264로 인코딩 후 stdout으로 내보냄
    # raspivid_cmd = [
    #     "raspivid",
    #     "-t", "0",               # 무기한 실행
    #     "-o", "-",               # 표준 출력(stdout)으로 H.264 바이트스트림
    #     "-w", f"{width}",             # 해상도 예시
    #     "-h", f"{height}",
    #     "-fps", f"{fps}",            # 프레임레이트
    #     "-pf", "high"            # 프로파일 설정 (고화질)
    #     "-g 10"
    #     # libcamera-vid 사용 시:
    #     # "libcamera-vid", "--inline", "-t", "0", "--width", "640", "--height", "480",
    #     # "--framerate", "30", "--codec", "h264", "-o", "-"
    # ]

    raspivid_cmd = [
        "libcamera-vid",
        "--inline",           # SPS/PPS를 스트림 내부에 삽입
        "-t", "0",
        "--width", f"{width}",
        "--height", f"{height}",
        "--framerate", f"{fps}",
        "--intra", "1",
        "--bitrate", f"{bitrate}"
        "--codec", "h264",
        "-o", "-",
    ]
    
    # FFmpeg로 piped input(표준입력: pipe:0)을 받아
    # 재인코딩 없이 copy(-vcodec copy)로 mpegts 컨테이너에 담아
    # UDP://SERVER_IP:SERVER_PORT 로 전송
    ffmpeg_cmd = [
        "ffmpeg",
        "-re",                   # 실시간 프레임레이트로 읽기
        "-i", "pipe:0",          # raspivid 결과물을 표준입력 통해 받아옴
        "-vcodec", "copy",       # 재인코딩 없이 H.264 bitstream 그대로 복사
        "-an",                   # 오디오 없음
        "-f", "mpegts",          # TS 컨테이너로 전송
        f"udp://{SERVER_IP}:{SERVER_PORT}"
    ]
    
    # Popen으로 두 프로세스를 파이프로 연결
    raspivid_proc = subprocess.Popen(raspivid_cmd, stdout=subprocess.PIPE)
    ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdin=raspivid_proc.stdout)

    try:
        # FFmpeg 프로세스가 종료될 때까지 대기
        ffmpeg_proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        # Ctrl+C 등으로 종료 시 프로세스 정리
        ffmpeg_proc.terminate()
        raspivid_proc.terminate()

if __name__ == "__main__":
    send_video()