import cv2
import numpy as np
import asyncio
import json
import argparse
import pyaudio
import os
import time
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceServer
from aiortc.contrib.media import MediaPlayer, MediaRecorder, MediaRelay
from aiortc.mediastreams import AudioStreamTrack, VideoStreamTrack
from av import VideoFrame, AudioFrame
import logging
from fractions import Fraction
from picamera2 import Picamera2


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rpi-webrtc")

# 설정 변수
FRAME_WIDTH = 540
FRAME_HEIGHT = 360
FPS = 25
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
CHUNK_SIZE = 640  # 0.04초 단위 (16000 * 0.2)
FRAMES_PER_CHUNK = 1  # 0.04초 단위 (25fps * 0.2)

# 오디오 재생 및 녹음 객체
audio = pyaudio.PyAudio()


# 비디오 스트림 트랙 클래스 정의
class CameraVideoStreamTrack(VideoStreamTrack):
    def __init__(self):
        super().__init__()
        # self.camera = cv2.VideoCapture(0)
        # self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
        # self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        # self.camera.set(cv2.CAP_PROP_FPS, FPS)
        self.picam2 = Picamera2()
        # 540x360, RGB888 포맷 설정
        config = self.picam2.create_preview_configuration(
            main={
                "size": (FRAME_WIDTH, FRAME_HEIGHT),
                "format": "RGB888"
            }
        )
        self.picam2.configure(config)
        self.picam2.set_controls({"FrameRate": float(FPS)})
        self.frame_count = 0
        self.relay = MediaRelay()
        self.last_frame_time = time.time()

        self.current_time = None
        self.picam2.start()

    async def recv(self):
        if self.current_time is None:
            self.current_time = time.time()

        frame = self.picam2.capture_array()  # shape: (height, width, 3)

        # VideoFrame 생성
        video_frame = VideoFrame.from_ndarray(frame, format="rgb24")
        video_frame.pts = self.frame_count
        video_frame.time_base = Fraction(1, FPS)
        self.frame_count += 1

        to_sleep = (1 / FPS) - (time.time() - self.current_time)
        if to_sleep > 0:
            await asyncio.sleep(to_sleep)

        self.current_time += 1 / FPS
        # print("recv() 호출됨", self.current_time, self.frame_count, to_sleep)  # 디버그용

        return video_frame


# 오디오 스트림 트랙 클래스 정의
class MicrophoneAudioStreamTrack(AudioStreamTrack):
    def __init__(self):
        super().__init__()
        self.sample_rate = AUDIO_SAMPLE_RATE
        self.sample_width = 2  # 16-bit audio
        self.channels = AUDIO_CHANNELS
        self.pts = 0

        # 마이크 설정
        self.microphone = None

    async def recv(self):
        if self.microphone is None:
            self.microphone = audio.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=CHUNK_SIZE
            )
        # 0.2초 단위로 오디오 데이터 읽기 (16000 * 0.2 = 3200 샘플)
        raw_samples = self.microphone.read(CHUNK_SIZE, exception_on_overflow=False)

        # 바이트를 numpy 배열로 변환
        samples = np.frombuffer(raw_samples, dtype=np.int16)
        # print(samples.shape)
        # AudioFrame 생성
        frame = AudioFrame.from_ndarray(
            samples[None, :],
            format="s16",
            layout="mono" if self.channels == 1 else "stereo"
        )
        frame.pts = self.pts
        frame.sample_rate = self.sample_rate
        frame.time_base = Fraction(1, FPS)

        self.pts += CHUNK_SIZE
        return frame


# 오디오 출력 클래스 정의
class AudioOutputTrack:
    def __init__(self):
        self.audio_player = audio.open(
            format=pyaudio.paInt16,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_SAMPLE_RATE,
            output=True,
            frames_per_buffer=CHUNK_SIZE
        )

    def process_audio(self, frame):
        # 프레임을 numpy 배열로 변환
        samples = frame.to_ndarray()
        # 16비트 정수로 변환
        samples = samples.astype(np.int16)
        # 오디오 재생
        self.audio_player.write(samples.tobytes())


# WebRTC 연결 관리
pcs = set()
relay = MediaRelay()
audio_output = AudioOutputTrack()


async def index(request):
    content = open(os.path.join(os.path.dirname(__file__), "index.html"), "r").read()
    return web.Response(content_type="text/html", text=content)


async def javascript(request):
    content = open(os.path.join(os.path.dirname(__file__), "client.js"), "r").read()
    return web.Response(content_type="application/javascript", text=content)


async def offer(request):
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    print(offer.sdp)

    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info(f"Connection state is {pc.connectionState}")
        if pc.connectionState == "failed":
            await pc.close()
            pcs.discard(pc)

    # 오디오 트랙 수신 처리
    @pc.on("track")
    def on_track(track):
        logger.info(f"Track {track.kind} received")

        if track.kind == "audio":
            # 안드로이드에서 보낸 오디오를 라즈베리파이에서 재생
            @track.on("frame")
            def on_frame(frame):
                audio_output.process_audio(frame)

    # 라즈베리파이의 카메라와 마이크 트랙 추가
    pc.addTrack(CameraVideoStreamTrack())
    pc.addTrack(MicrophoneAudioStreamTrack())

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    print("Answer SDP:\n", pc.localDescription.sdp)

    return web.Response(
        content_type="application/json",
        text=json.dumps({
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type
        })
    )


async def on_shutdown(app):
    # 연결 종료 처리
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()

    # 오디오 리소스 해제
    audio.terminate()


# HTML 파일 생성
def create_html_file():
    html_content = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>라즈베리파이 WebRTC</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 0; padding: 20px; }
        video { max-width: 100%; background-color: #ddd; }
        button { padding: 8px 16px; margin: 5px; cursor: pointer; }
    </style>
</head>
<body>
    <h1>라즈베리파이 WebRTC 스트리밍</h1>
    <video id="video" autoplay playsinline></video>
    <div>
        <button id="start">시작</button>
        <button id="stop">중지</button>
    </div>
    <script src="client.js"></script>
</body>
</html>
    """

    with open("index.html", "w") as f:
        f.write(html_content)


# JavaScript 파일 생성
def create_js_file():
    js_content = """
let pc = null;
let videoElement = document.getElementById('video');
let startButton = document.getElementById('start');
let stopButton = document.getElementById('stop');

async function start() {
    try {
        // 오디오 스트림 생성
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });

        // 비디오 요소에 스트림 연결
        videoElement.srcObject = new MediaStream();

        // WebRTC 연결 생성
        pc = new RTCPeerConnection({
            iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
        });

        // 스트림의 오디오 트랙을 WebRTC 연결에 추가
        stream.getAudioTracks().forEach(track => {
            pc.addTrack(track, stream);
        });

        // 서버로부터 받은 비디오/오디오 트랙 처리
        pc.ontrack = function(evt) {
            if (evt.track.kind === 'video') {
                videoElement.srcObject.addTrack(evt.track);
            }
        };

        // ICE 상태 로깅
        pc.oniceconnectionstatechange = function() {
            console.log('ICE 상태:', pc.iceConnectionState);
        };

        // 오퍼 생성 및 서버에 전송
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);

        const response = await fetch('/offer', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                sdp: pc.localDescription.sdp,
                type: pc.localDescription.type
            })
        });

        const answer = await response.json();
        await pc.setRemoteDescription(answer);

        console.log('WebRTC 연결 완료');
    } catch (e) {
        console.error('WebRTC 연결 실패:', e);
    }
}

function stop() {
    if (pc) {
        pc.close();
        pc = null;
    }

    if (videoElement.srcObject) {
        videoElement.srcObject.getTracks().forEach(track => track.stop());
        videoElement.srcObject = null;
    }

    console.log('WebRTC 연결 종료');
}

startButton.addEventListener('click', start);
stopButton.addEventListener('click', stop);
    """

    with open("client.js", "w") as f:
        f.write(js_content)


if __name__ == "__main__":
    # 필요한 파일 생성
    create_html_file()
    create_js_file()

    # 웹 서버 설정
    app = web.Application()
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_get("/client.js", javascript)
    app.router.add_post("/offer", offer)

    # 서버 실행
    parser = argparse.ArgumentParser(description="라즈베리파이 WebRTC 스트리밍 서버")
    parser.add_argument("--host", default="0.0.0.0", help="호스트 IP")
    parser.add_argument("--port", type=int, default=8080, help="포트 번호")
    args = parser.parse_args()

    logger.info(f"서버 시작: http://{args.host}:{args.port}")
    web.run_app(app, host=args.host, port=args.port)