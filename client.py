import asyncio
import json
import logging
import av
import time
import cv2

from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
from aiortc.contrib.media import MediaRelay
from fractions import Fraction  # Python 내장 모듈

from picamera2 import Picamera2

# try:
#     picam_available = True
# except ImportError:
#     picam_available = False

logging.basicConfig(level=logging.INFO)

class PiCameraTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, camera, fps=30):
        super().__init__()  # 꼭 호출
        self.camera = camera
        self.fps = fps
        self.start_time = time.time()
        self.frame_count = 0

    async def recv(self):
        # picamera2에서 프레임(numpy array) 추출
        frame_np = self.camera.capture_array()
        # aiortc가 쓰는 av.VideoFrame으로 변환
        frame_np = frame_np[:, :, 1:4]  # X(패딩) 채널 제거하고 BGR만 유지
        # frame_np = cv2.cvtColor(frame_np, cv2.COLOR_XBGR2BGR)
        video_frame = av.VideoFrame.from_ndarray(frame_np, format='bgr24')

        # 타임스탬프 관련 설정
        self.frame_count += 1
        video_frame.pts = self.frame_count
        video_frame.time_base = Fraction(1, self.fps)

        # 원하는 FPS에 맞추어 sleep
        await asyncio.sleep(1 / self.fps)
        print('video: ', frame_np.shape, self.frame_count)
        return video_frame

async def run_client(server_url):
    # WebRTC PC 생성
    pc = RTCPeerConnection()

    @pc.on("iceconnectionstatechange")
    def on_iceconnectionstatechange():
        print(f"ICE connection state: {pc.iceConnectionState}")
        if pc.iceConnectionState == "failed":
            asyncio.run_coroutine_threadsafe(pc.close(), asyncio.get_event_loop())

    # if not picam_available:
    #     raise RuntimeError("picamera2 라이브러리가 설치되지 않았습니다.")

    camera = Picamera2()
    # 해상도/포맷 등 원하는 설정
    camera.configure(camera.create_video_configuration(main={"size": (640, 480)}))  # XBGR8888
    # picam2.configure(picam2.create_preview_configuration(main={
    #     "size": (820, 616), "format": 'XRGB8888'
    # }))
    # 노출 모드를 자동으로 설정
    # picam2.set_controls({"AeMode": controls.AeModeEnum.Normal})

    # 셔터 스피드를 10000(1/10000초)으로 고정
    camera.set_controls({"ExposureTime": 10000})

    # 노출 보정을 1스톱 올림 (1스톱 = 2배 밝기)
    camera.set_controls({"ExposureValue": 2})
    camera.start()

    # 직접 구현한 PiCameraTrack 생성
    camera_track = PiCameraTrack(camera, fps=20)

    # 카메라 트랙을 WebRTC에 추가
    pc.addTrack(camera_track)

    # Offer 생성 및 설정
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    # 서버로 Offer 전송
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server_url}/offer",
            json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
        ) as resp:
            answer_json = await resp.json()

    # Answer 설정
    answer = RTCSessionDescription(sdp=answer_json["sdp"], type=answer_json["type"])
    await pc.setRemoteDescription(answer)

    print("WebRTC 연결 완료. Picamera2 스트리밍 전송 중... (Ctrl+C로 종료)")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await pc.close()
        camera.stop()

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://192.168.50.236:5002")
    args = parser.parse_args()

    asyncio.run(run_client(args.server))

if __name__ == "__main__":
    main()