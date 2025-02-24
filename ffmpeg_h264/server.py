import subprocess
import cv2
import numpy as np

def receive_video():
    # UDP로 들어올 포트 설정 (0.0.0.0는 모든 NIC에서 수신)
    LISTEN_IP = "0.0.0.0"
    LISTEN_PORT = 5002
    
    # 송신 측과 동일 해상도라고 가정 (640x480)
    width = 640
    height = 480
    
    # FFmpeg로 mpegts(H.264) 스트림을 받아 rawvideo(bgr24)로 디코딩하여
    # 표준출력(pipe:1)으로 내보냄
    ffmpeg_cmd = [
        "ffmpeg",
        "-i", f"udp://{LISTEN_IP}:{LISTEN_PORT}",  # 입력 소스
        "-f", "rawvideo",                          # 디코딩한 RAW 영상
        "-pix_fmt", "bgr24",                       # OpenCV가 바로 읽기 쉬운 BGR 포맷
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-probesize", "32",
        "-analyzeduration", "0",
        "pipe:1",                                  # 파이프로 stdout
    ]
    
    ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, bufsize=10**7)
    
    try:
        while True:
            # 한 프레임 크기: width * height * 3(BGR24)
            frame_size = width * height * 3
            raw_frame = ffmpeg_proc.stdout.read(frame_size)
            
            if not raw_frame:
                print("수신 종료 또는 오류 발생")
                break
            
            # numpy 배열로 변환 후 OpenCV 이미지로 해석
            frame = np.frombuffer(raw_frame, np.uint8).reshape((height, width, 3))
            cv2.imshow("Received Video", frame)
            
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    
    except KeyboardInterrupt:
        pass
    finally:
        ffmpeg_proc.terminate()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    receive_video()