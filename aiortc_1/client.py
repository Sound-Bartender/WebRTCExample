import asyncio
import time, fractions
import numpy as np
import pyaudio
import aiohttp

from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
from av import AudioFrame, VideoFrame


# 오디오+비디오 전송 간 25fps(40ms) 맞추기 위한 동기 도우미
class SyncTimer:
    def __init__(self, frame_rate=25):
        self.start_time = time.time()
        self.frame_interval = 1 / frame_rate

    def get_wait_time(self):
        """지금 시점에서 다음 프레임까지 대기해야 할 시간(초)을 계산."""
        elapsed = time.time() - self.start_time
        frames_passed = int(elapsed / self.frame_interval)
        next_frame_time = (frames_passed + 1) * self.frame_interval
        return max(0.0, self.start_time + next_frame_time - time.time())


class AudioStreamTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self, sync: SyncTimer):
        super().__init__()  # MediaStreamTrack 초기화
        self.sync = sync
        self.chunk = 640         # 16kHz에서 40ms면 640샘플
        self.sample_rate = 16000

        # PyAudio 초기화 (16kHz, 모노)
        self.pa = pyaudio.PyAudio()
        self.stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=self.chunk
        )

    async def recv(self):
        # 프레임 동기: 25fps에 맞춰 40ms 간격
        await asyncio.sleep(self.sync.get_wait_time())

        # 오디오 캡처
        audio_data = self.stream.read(self.chunk, exception_on_overflow=False)

        # aiortc용 AudioFrame 생성
        frame = AudioFrame(format="s16", layout="mono", samples=self.chunk)
        frame.planes[0].update(audio_data)
        # 타임스탬프 설정(샘플레이트 기준)
        frame.sample_rate = self.sample_rate
        frame.time_base = fractions.Fraction(1, self.sample_rate)
        frame.pts = int(time.time() * self.sample_rate)

        return frame


class DummyVideoStreamTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, sync: SyncTimer):
        super().__init__()  # MediaStreamTrack 초기화
        self.sync = sync
        self.width = 112
        self.height = 112

    async def recv(self):
        # 프레임 동기: 25fps에 맞춰 40ms 간격
        await asyncio.sleep(self.sync.get_wait_time())

        # 임의의 더미 프레임 생성
        arr = (np.random.rand(self.height, self.width, 3) * 255).astype(np.uint8)
        frame = VideoFrame.from_ndarray(arr, format="rgb24")

        # aiortc용 타임스탬프 설정
        frame.pts, frame.time_base = self.next_timestamp()
        return frame


async def run_client():
    # 클라이언트 PeerConnection 생성
    pc = RTCPeerConnection()

    # 오디오·비디오 동시에 25fps로 동기 전송
    sync_timer = SyncTimer(frame_rate=25)
    pc.addTrack(AudioStreamTrack(sync_timer))
    pc.addTrack(DummyVideoStreamTrack(sync_timer))

    # Offer 생성
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    # 서버에 Offer 전송
    async with aiohttp.ClientSession() as session:
        async with session.post("http://localhost:5555/offer", json={
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type
        }) as resp:
            answer_data = await resp.json()

    # 서버의 Answer 반영
    answer = RTCSessionDescription(
        sdp=answer_data["sdp"],
        type=answer_data["type"]
    )
    await pc.setRemoteDescription(answer)

    # 무기한 대기
    await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(run_client())