import asyncio
import time
import logging
from aiortc import RTCPeerConnection, RTCConfiguration, RTCIceServer, MediaStreamTrack
from aiortc.contrib.signaling import TcpSocketSignaling
from picamera2 import Picamera2

from fractions import Fraction
import struct

from h264_payloader import H264Payloader

# 간단한 Annex-B 구분자(0x00000001) 파서
def split_annexb_frames(bitstream: bytes):
    """
    H.264 Annex-B (0x00000001) 기준으로 NAL Unit들 추출.
    """
    units = []
    start = 0
    while True:
        idx = bitstream.find(b"\x00\x00\x00\x01", start+4)
        if idx == -1:
            # 마지막
            nal = bitstream[start:]
            if len(nal) > 0:
                units.append(nal)
            break
        nal = bitstream[start:idx]
        if len(nal) > 0:
            units.append(nal)
        start = idx
    return units

class H264VideoTrack(MediaStreamTrack):
    """
    Picamera2에서 H.264 바이트스트림을 읽어,
    aiortc가 기대하는 형식(RTP 페이로드)으로 보내는 '가짜' VideoTrack.

    실제 uncompressed VideoFrame을 반환하는 것이 아니라,
    payloader를 이용해 RTP Packet으로 쪼개 전달한다.
    """
    kind = "video"

    def __init__(self, camera: Picamera2, fps=30):
        super().__init__()
        self.camera = camera
        self.fps = fps
        self.h264_payloader = H264Payloader()

        self.sequence_number = 0
        self.timestamp = 0
        self.start_time = time.time()

    async def recv(self):
        """
        aiortc가 각 "프레임"을 요청할 때마다 호출됨.
        실제로는 H.264 NAL 들을 RTP 페이로드로 만들어
        내부적으로 RTP 전송하는 역할 수행.
        여기서는 "빈 VideoFrame"을 리턴하거나, 
        aiortc가 EncodedFrame으로 처리할 수 있도록 별도 trick을 쓸 수도 있음.
        """
        # 1) H.264 raw bitstream 읽기
        #    Picamera2 -> 하드웨어 인코딩된 H.264. 
        #    capture_buffer("main") 로 한 덩어리 가져온다고 가정
        data = self.camera.capture_buffer("main")
        if not data:
            # 데이터가 없으면 sleep 후 재시도
            await asyncio.sleep(1.0 / self.fps)
            return None

        # 2) Annex-B 기준으로 NAL들 추출
        nals = split_annexb_frames(data)

        # 3) 각 NAL을 payloader로 RTP용 Fragment로 분할
        payloads = []
        for nal in nals:
            fragments = self.h264_payloader.pay(nal)
            payloads.extend(fragments)

        # 4) aiortc는 실제로
        #    - RTP sender에서 payloads를 하나하나 RTP 패킷으로 씌워 전송
        #    - MediaStreamTrack.recv()가 "디코딩된 VideoFrame"을 return하기를 기대
        #
        #    그러나 "EncodedTrack" 형태로 송출하려면
        #    aiortc 내부적으로는 transform 같은 커스텀 로직이 필요.
        #
        # 공식 예제(h264.py)는
        # "H264Reader" -> "pay(nals)" -> "EncodedFrame" 을 "RtpSender._send_rtp()"에 전달
        #
        # 여기서는 간단히 "프레임 하나" 처리로 보고,
        # timestamp 증가, sequence_number는 payload 개수만큼 증가
        # -> 실제로는 aiortc가 packet들을 보낼 때 sequence_number를 관리.
        #    여기서는 '흉내'만 내는 상태.
        #
        # *이 예시는 "개념 시연용"임을 강조합니다.*

        # (부족한 부분) aiortc에 EncodedFrame을 전달하는 공식 API가 없어
        # MediaStreamTrack에서는 uncompressed VideoFrame을 반환해야 함.
        # ==> 다음처럼 "더미 VideoFrame"을 반환하고,
        #     내부적으로 pay() 결과를 RtpSender에게 직접 밀어넣는 구조를 만들어야 함.

        # 5) 타임스탬프 및 FPS 맞추기
        self.timestamp += int(90000 / self.fps)  # 90kHz 기반 timestamp
        await asyncio.sleep(1.0 / self.fps)

        # 6) 더미 VideoFrame 생성 (실제로는 내용 없는 frame)
        #    수신 측은 이 track을 "H.264 Track"으로 인식해 디코딩 시도
        import av
        fake_frame = av.VideoFrame.from_ndarray(
            b"",  # 빈 바이트
            format="gray",  # 아무거나
        )
        fake_frame.pts = self.timestamp
        fake_frame.time_base = Fraction(1, 90000)

        return fake_frame

async def run_client(server_ip="127.0.0.1", server_port=9999):
    # 시그널링
    signaling = TcpSocketSignaling(server_ip, server_port)
    await signaling.connect()

    # PeerConnection 생성
    ice_servers = [RTCIceServer("stun:stun.l.google.com:19302")]
    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=ice_servers))

    # Picamera2 설정 (H.264)
    camera = Picamera2()
    config = camera.create_video_configuration(
        main={
            "size": (640, 480),
            "format": "H264"
        }
    )
    camera.configure(config)
    camera.start()

    # H.264 Track 추가
    local_track = H264VideoTrack(camera, fps=15)
    pc.addTrack(local_track)

    # Offer 생성
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    print("Client: Send Offer")
    await signaling.send(pc.localDescription)

    # Server(Answer) 수신
    answer = await signaling.receive()
    print("Client: Got Answer")
    await pc.setRemoteDescription(answer)

    # 종료 안 하고 계속 실행 (테스트용 1시간)
    await asyncio.sleep(3600)
    print("Client: Terminated")

    await pc.close()
    camera.stop()

def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_client("서버_IP_or_localhost", 9999))

if __name__ == "__main__":
    main()