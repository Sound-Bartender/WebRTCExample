
import asyncio
import json
import logging
import cv2

from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaBlackhole

logging.basicConfig(level=logging.INFO)

pcs = set()  # 활성화된 PeerConnection들을 저장


async def index(request):
    """ 단순 테스트용 GET """
    return web.Response(text="WebRTC Server is running.")


async def offer(request):
    """
    클라이언트(라즈베리 파이)에서 Offer SDP를 JSON 형태로 POST하면,
    서버에서 Answer SDP를 생성해 반환한다.
    """
    params = await request.json()
    offer_sdp = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        print("PC connection state:", pc.connectionState)
        if pc.connectionState == "failed":
            await pc.close()
            pcs.discard(pc)

    @pc.on("track")
    def on_track(track):
        print(f"Received {track.kind} track")

        if track.kind == "video":
            # 비디오 트랙이 들어오면 OpenCV 창에서 보여준다.
            # 별도의 코루틴 실행
            asyncio.ensure_future(show_video(track))
        elif track.kind == "audio":
            # 여기서는 오디오를 재생하지 않고 그냥 버림(MediaBlackhole) 처리
            media_sink = MediaBlackhole()
            media_sink.addTrack(track)

    # 클라이언트 Offer 처리
    await pc.setRemoteDescription(offer_sdp)

    # 서버가 Answer 생성
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    # Answer를 JSON으로 응답
    return web.Response(
        content_type="application/json",
        text=json.dumps({
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type
        }),
    )


async def show_video(track):
    """
    들어온 비디오 프레임을 계속해서 받아 OpenCV로 실시간 표시하는 코루틴.
    """
    while True:
        try:
            frame = await track.recv()
        except:
            break

        # aiortc의 VideoFrame -> numpy 배열 (BGR)
        img = frame.to_ndarray(format="bgr24")

        # OpenCV 창에 표시
        cv2.imshow("Received Video", img)
        # 키 입력 대기 (GUI 이벤트 처리)
        # 'q' 누르면 종료
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    track.stop()
    cv2.destroyAllWindows()
    print("Video display finished.")


async def on_shutdown(app):
    # 서버 종료 시 모든 PeerConnection 닫기
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()


def main():
    app = web.Application()
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)

    web.run_app(app, host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()