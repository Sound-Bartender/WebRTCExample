import asyncio
import json
import logging

from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRecorder

logging.basicConfig(level=logging.INFO)
pcs = set()  # PeerConnection 관리를 위한 집합

async def offer(request):
    """
    클라이언트로부터 Offer (SDP, type)를 받아 처리 후 Answer 반환
    """
    params = await request.json()
    offer_sdp = params["sdp"]
    offer_type = params["type"]

    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        print("Connection state is %s" % pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            pcs.discard(pc)

    @pc.on("track")
    async def on_track(track):
        print(f"Track kind={track.kind} is received")
        if track.kind == "video":
            # 수신된 영상을 mp4로 파일에 저장(내부적으로 ffmpeg 사용)
            recorder = MediaRecorder("received_video.mp4")
            recorder.addTrack(track)
            await recorder.start()

            @track.on("ended")
            async def on_ended():
                print("Video track ended")
                await recorder.stop()

    # 클라이언트 Offer 설정
    offer_obj = RTCSessionDescription(sdp=offer_sdp, type=offer_type)
    await pc.setRemoteDescription(offer_obj)

    # 서버 쪽 Answer 생성
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    # Answer SDP를 JSON 형태로 반환
    return web.Response(
        content_type="application/json",
        text=json.dumps({
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type
        }),
    )


async def on_shutdown(app):
    # 서버 종료 시 모든 PC 종료
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()


def main():
    app = web.Application()
    app.on_shutdown.append(on_shutdown)
    # /offer 라우팅
    app.router.add_post("/offer", offer)

    # 포트 5002로 서버 실행
    web.run_app(app, host="0.0.0.0", port=5002)


if __name__ == "__main__":
    main()