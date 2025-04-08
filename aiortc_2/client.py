import asyncio
import json
import pyaudio
import websockets

from aiortc import RTCIceCandidate, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.signaling import BYE
from aiortc.mediastreams import AudioStreamTrack
from av import AudioFrame
import numpy as np


# PyAudio에서 16kHz PCM 데이터를 읽어오는 Track 정의
class PyAudioTrack(AudioStreamTrack):
    kind = "audio"

    def __init__(self, rate=16000, channels=1, chunk=1024):
        super().__init__()
        self.rate = rate
        self.channels = channels
        self.chunk = chunk

        self.p = pyaudio.PyAudio()
        self.stream = self.p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.rate,
            input=True,
            frames_per_buffer=self.chunk,
        )

    async def recv(self):
        # PyAudio에서 PCM 데이터 읽어오기
        data = self.stream.read(self.chunk, exception_on_overflow=False)

        # (chunk x 채널) x int16 => numpy array로 변환
        audio_array = np.frombuffer(data, dtype=np.int16)

        # aiortc용 AudioFrame 생성
        frame = AudioFrame.from_ndarray(audio_array.reshape(-1, self.channels), format="s16", layout="mono")
        frame.sample_rate = self.rate
        return frame

    def stop(self):
        super().stop()
        if self.stream is not None:
            self.stream.stop_stream()
            self.stream.close()
        if self.p is not None:
            self.p.terminate()


async def send_offer_and_media(ws_uri: str):
    # 서버와 WebSocket 연결
    async with websockets.connect(ws_uri) as ws:
        # RTCPeerConnection 생성
        pc = RTCPeerConnection()

        # 오디오 트랙 추가
        local_audio_track = PyAudioTrack(rate=16000, channels=1)
        pc.addTrack(local_audio_track)

        # offer 생성 및 전송
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        await ws.send(json.dumps({
            "type": "offer",
            "sdp": pc.localDescription.sdp
        }))

        # 서버로부터 answer 수신
        message = await ws.recv()
        msg = json.loads(message)
        if msg["type"] == "answer":
            answer = RTCSessionDescription(sdp=msg["sdp"], type=msg["type"])
            await pc.setRemoteDescription(answer)

        # 연결이 유지되는 동안 대기
        print("클라이언트: 오디오 전송 중... Ctrl+C로 종료.")
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            print("클라이언트: 종료.")
            await pc.close()


if __name__ == "__main__":
    # 간단 실행: python client.py
    # 예: asyncio.run(send_offer_and_media("ws://localhost:8765"))
    asyncio.run(send_offer_and_media("ws://localhost:5555"))