import asyncio
import json
import wave
import websockets

from aiortc import RTCIceCandidate, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import AudioStreamTrack
from av import AudioFrame


class AudioFileSaverTrack(AudioStreamTrack):
    kind = "audio"

    def __init__(self, sample_rate=16000, channels=1, filename="recorded.wav"):
        super().__init__()
        self.sample_rate = sample_rate
        self.channels = channels
        self.filename = filename
        self.wav_file = wave.open(self.filename, 'wb')
        self.wav_file.setnchannels(self.channels)
        self.wav_file.setsampwidth(2)  # int16 => 2 byte
        self.wav_file.setframerate(self.sample_rate)

    async def recv(self):
        # 수신된 오디오 프레임을 상위 레벨에서 받아 파일로만 저장할 것이므로
        # 이 Track 자체에서 새 프레임을 만들 일은 거의 없음
        # 필요하다면 pass나 None 리턴이 가능하지만,
        # aiortc 내부 로직 때문에 반환이 필요할 수 있음
        frame = AudioFrame(samples=1024)
        return frame

    def write_frame(self, frame: AudioFrame):
        # AudioFrame -> raw PCM (int16) 추출
        # AudioFrame.planes[0]가 PCM 데이터
        # 또는 to_ndarray()를 통해 numpy로 꺼낼 수도 있음
        pcm_data = frame.planes[0].to_bytes()
        self.wav_file.writeframes(pcm_data)

    def stop(self):
        # WAV 파일 닫기
        if self.wav_file:
            self.wav_file.close()
        super().stop()


async def handler(websocket, path):
    # 간단 시그널링 예시
    pc = RTCPeerConnection()

    # 오디오 저장용 트랙 생성
    saver_track = AudioFileSaverTrack(sample_rate=16000, channels=1, filename="recorded.wav")

    @pc.on("track")
    def on_track(track):
        print(f"서버: 클라이언트로부터 {track.kind} 트랙 수신")
        if track.kind == "audio":
            # 계속 프레임을 받아서 saver_track에 쓰기
            @track.on("frame")
            def on_frame(frame):
                saver_track.write_frame(frame)

    # offer 수신 -> answer 생성 -> 전송
    message = await websocket.recv()
    msg = json.loads(message)

    if msg["type"] == "offer":
        offer = RTCSessionDescription(sdp=msg["sdp"], type=msg["type"])
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        await websocket.send(json.dumps({
            "type": pc.localDescription.type,
            "sdp": pc.localDescription.sdp
        }))

    print("서버: 연결 준비 완료. 오디오를 기다립니다...")

    # 연결이 유지되는 동안 대기
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        print("서버: 종료.")
        await pc.close()


async def main():
    async with websockets.serve(handler, "0.0.0.0", 5555):
        print("서버: WebSocket 시그널링 서버가 5555 포트에서 대기 중...")
        await asyncio.Future()  # 종료될 때까지 계속


if __name__ == "__main__":
    # 간단 실행: python server.py
    # 그리고 다른 터미널에서 python client.py
    asyncio.run(main())