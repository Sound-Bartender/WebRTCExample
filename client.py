
import sys
import asyncio
import json
import logging
import requests
import av
import numpy as np
import time

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaStreamTrack
from picamera2 import Picamera2
from av import VideoFrame

# 오디오 캡처용
import pyaudio

logging.basicConfig(level=logging.INFO)

# --- 비디오 트랙 (picamera2) ---
class PiCameraVideoTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, camera):
        super().__init__()  # MediaStreamTrack 초기화
        self.camera = camera
        self.width = 640
        self.height = 480

        # 카메라 설정 (still_configuration, video_configuration 등 상황에 따라 사용)
        camera_config = self.camera.create_still_configuration(
            main={"size": (self.width, self.height)}
        )
        self.camera.configure(camera_config)
        self.camera.start()

        self.start_time = time.time()

    async def recv(self):
        # AIORTC가 주기적으로 recv()를 호출 -> 다음 프레임 반환
        frame_array = self.camera.capture_array()  # numpy(BGR)

        video_frame = VideoFrame.from_ndarray(frame_array, format="bgr24")

        # pts, time_base 설정(대략적인 타임스탬프)
        elapsed = time.time() - self.start_time
        video_frame.pts = int(elapsed * 1e6)
        video_frame.time_base = av.Rational(1, 1_000_000)

        return video_frame


# --- 오디오 트랙 (PyAudio) ---
class MicrophoneAudioTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self, rate=16000, channels=1):
        super().__init__()
        self.rate = rate
        self.channels = channels

        self.p = pyaudio.PyAudio()
        self.stream = self.p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.rate,
            input=True,
            frames_per_buffer=1024
        )

    async def recv(self):
        # 마이크에서 1024 샘플 읽기
        audio_data = self.stream.read(1024, exception_on_overflow=False)

        # aiortc에서 쓰는 AudioFrame으로 변환
        audio_frame = av.AudioFrame.from_ndarray(
            np.frombuffer(audio_data, dtype=np.int16),
            layout="mono" if self.channels == 1 else "stereo"
        )
        # 오디오 프레임도 pts/time_base 설정이 가능하지만 생략
        return audio_frame


async def run_client(server_ip):
    pc = RTCPeerConnection()

    # 비디오 트랙 추가 (picamera2)
    picam2 = Picamera2()
    video_track = PiCameraVideoTrack(picam2)
    pc.addTrack(video_track)

    # 오디오 트랙 추가
    audio_track = MicrophoneAudioTrack(rate=16000, channels=1)
    pc.addTrack(audio_track)

    # 클라이언트 -> 서버 Offer 생성
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    # 서버 /offer 로 Offer SDP 전송
    url = f"http://{server_ip}:8080/offer"
    headers = {"Content-Type": "application/json"}
    data = {
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type
    }
    resp = requests.post(url, headers=headers, data=json.dumps(data))
    if resp.status_code != 200:
        print("Failed to get Answer from server:", resp.text)
        return

    answer_json = resp.json()
    answer = RTCSessionDescription(sdp=answer_json["sdp"], type=answer_json["type"])
    await pc.setRemoteDescription(answer)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        print("Connection State:", pc.connectionState)
        if pc.connectionState == "failed" or pc.connectionState == "disconnected":
            await pc.close()

    print("WebRTC 연결이 설정되었습니다. (Ctrl+C로 종료)")

    # 연결 유지를 위해 무한 대기
    while True:
        await asyncio.sleep(1)


def main():
    if len(sys.argv) < 2:
        print(f"사용법: python {sys.argv[0]} <server_ip>")
        sys.exit(1)

    server_ip = sys.argv[1]
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(run_client(server_ip))
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == "__main__":
    main()