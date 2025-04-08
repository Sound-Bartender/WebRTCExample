import asyncio
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRecorder

pcs = set()  # 연결된 PeerConnection 보관 (단일 클라이언트만 필요해도 예시로 세트 사용)


async def offer(request):
    # 클라이언트에서 전달된 SDP Offer 정보 추출
    params = await request.json()
    session = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    # PeerConnection 생성
    pc = RTCPeerConnection()
    pcs.add(pc)

    # MediaRecorder를 이용해 오디오+비디오 동시 녹화
    recorder = MediaRecorder(
        'output.mp4',
        format='mp4',
        options={
            # 오디오 코덱 지정
            'c:a': 'aac',
            # 채널/샘플레이트 재설정 필터
            'sample_rate': '16000',
            'channels': '1',
            # 필요한 경우 비디오 코덱 등 추가 가능
            # 'video_codec': 'libx264',
            # 'audio_bitrate': '64k',
        }
    )

    @pc.on("track")
    def on_track(track):
        print(f"서버: Track 수신 - kind={track.kind} {track.id}")
        recorder.addTrack(track)

    # 연결 상태 모니터링 (연결 종료 시 녹화 중지)
    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        print(f"서버: Connection state = {pc.connectionState}")
        if pc.connectionState in ["closed", "failed"]:
            await recorder.stop()
            pcs.discard(pc)

    # Offer 설정
    await pc.setRemoteDescription(session)

    # Answer 생성 + 설정
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    # 녹화 시작
    await recorder.start()

    # 클라이언트에게 Answer 반환
    return web.json_response({
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type
    })


if __name__ == "__main__":
    app = web.Application()
    app.router.add_post("/offer", offer)
    web.run_app(app, port=5555)