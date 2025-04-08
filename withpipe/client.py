

import cv2
import mediapipe as mp
from queue import Queue
import sounddevice as sd


class FaceMeshDetector:
    def __init__(self, static_image_mode=False, max_num_faces=5, min_detection_con=0.5, min_tracking_con=0.5):
        self.mpFaceMesh = mp.solutions.face_mesh
        self.faceMesh = self.mpFaceMesh.FaceMesh(
            static_image_mode=static_image_mode,
            max_num_faces=max_num_faces,
            min_detection_confidence=min_detection_con,
            min_tracking_confidence=min_tracking_con
        )

        self.count = 0
        self.last_range = None
        self.last_crop = None

        # 입술 관련 랜드마크 인덱스 (Mediapipe Face Mesh 기준)
        self.MOUTH_LANDMARKS = [0, 267, 269, 270, 409, 306, 375, 321, 405, 314,
                                17, 84, 181, 91, 146, 61, 185, 40, 39, 37]

    def findMouthROI(self, img):
        # img = cv2.resize(img, (640, 480))
        h, w, _ = img.shape

        self.count += 1
        if self.last_range is not None and self.count % 8 != 0:
            x1, y1, x2, y2 = self.last_range
            self.last_crop = cv2.cvtColor(img[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
            return self.last_crop

        imgRGB = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        results = self.faceMesh.process(imgRGB)

        if results.multi_face_landmarks:
            # (a) 화면의 중앙점
            center_x, center_y = w // 2, h // 2
            min_dist = 9999999.0
            best_face = None

            # (b) 여러 얼굴 중 '화면 중심'과 가장 가까운 얼굴 찾기
            for faceLms in results.multi_face_landmarks:
                min_x, max_x = 1.0, 0.0
                min_y, max_y = 1.0, 0.0

                # 이 얼굴 전체 landmark의 min/max
                for lm in faceLms.landmark:
                    if lm.x < min_x: min_x = lm.x
                    if lm.x > max_x: max_x = lm.x
                    if lm.y < min_y: min_y = lm.y
                    if lm.y > max_y: max_y = lm.y

                # 얼굴 바운딩박스의 중심
                box_center_x = int(((min_x + max_x) / 2) * w)
                box_center_y = int(((min_y + max_y) / 2) * h)

                dist = (box_center_x - center_x) * (box_center_x - center_x) + (box_center_y - center_y)*(box_center_y - center_y)
                if dist < min_dist:
                    min_dist = dist
                    best_face = faceLms

            # (2) best_face에서 입술 좌표만 추출 → 바운딩박스 중심 구하기
            if best_face is not None:
                min_x, max_x = w, 0
                min_y, max_y = h, 0
                for idx in self.MOUTH_LANDMARKS:
                    px = int(best_face.landmark[idx].x * w)
                    py = int(best_face.landmark[idx].y * h)

                    if px < min_x: min_x = px
                    if px > max_x: max_x = px
                    if py < min_y: min_y = py
                    if py > max_y: max_y = py

                # 입술 바운딩박스 중심
                cx = (min_x + max_x) // 2
                cy = (min_y + max_y) // 2

                # (3) 크기를 원본에서 crop
                half_size = 56

                # 좌표 범위가 이미지 밖으로 나가지 않도록 보정
                x1 = max(0, cx - half_size)
                y1 = max(0, cy - half_size)
                x2 = min(w, cx + half_size)
                y2 = min(h, cy + half_size)

                # crop한 입술 영역
                mouth_crop = img[y1:y2, x1:x2]

                # Grayscale 변환
                if mouth_crop is not None and mouth_crop.size > 0:
                    self.last_range = [x1, y1, x2, y2]
                    self.last_crop = cv2.cvtColor(mouth_crop, cv2.COLOR_BGR2GRAY)

        return self.last_crop


class Client:
    def __init__(self, ip, port, sample_rate=16000, block_size=3200):
        self.ip = ip
        self.port = port
        self.sample_rate = sample_rate
        self.block_size = block_size
        # 오디오 블록(640샘플)들을 쌓아둘 큐
        self.audio_queue = Queue()
        self.roi_detector = FaceMeshDetector()

    def audio_callback(self, indata, frames, time_info, status):
        # 블록사이즈(blocksize)=640으로 설정해 두면 frames=640이 됨
        # 받은 오디오를 큐에 넣는다. 꼭 copy() 해서 넣는 것이 안전
        self.audio_queue.put(indata[:, 0].copy())
        # print(f'audio queue size: {len(self.audio_queue.queue)}')

    def start(self):
        # 카메라 열기
        cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FPS, 25)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 540)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)

        with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype='float32',
                blocksize=self.block_size,
                callback=self.audio_callback
        ):
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break

                mouth_roi = self.roi_detector.findMouthROI(frame)

                if self.audio_queue.qsize() > 0:
                    input_audio = self.audio_queue.get()

                    # print('extracted audio and video #')

                    if mouth_roi is not None:
                        input_video = mouth_roi[None, None, None]
                        cv2.imshow("Mouth ROI", mouth_roi)
                    else:
                        cv2.imshow("Mouth ROI", frame)

                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

            cap.release()
            cv2.destroyAllWindows()


if __name__ == '__main__':
    client = Client('192.168.x.x', 5555, sample_rate=16000, block_size=640)
    client.start()