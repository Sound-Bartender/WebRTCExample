import numpy as np
import pyaudio
from scipy.signal import correlate, resample

CHUNK = 640  # 0.04초 (25fps)
RATE = 16000  # 샘플링 주파수
CHANNELS = 1
FORMAT = pyaudio.paInt16
MAX_SHIFT = int(RATE * 0.01)  # ±10ms 보정 범위 (160샘플)
DRIFT_WINDOW = 100  # drift 평균 계산용 chunk 수 (~4초)
DRIFT_THRESHOLD = 0.5  # 평균 delay가 이 이상이면 drift 보정

drift_log = []


# === 송신 함수 ===
def send_beamformed(chunk):
    print(f'sending {len(chunk)} bytes')


# === 시간차 추정 ===
def estimate_delay(sig1, sig2, max_shift):
    corr = correlate(sig1, sig2, mode='full')
    center = len(corr) // 2
    lag = np.argmax(corr[center - max_shift: center + max_shift]) - max_shift
    return lag


# === 리샘플링 보정 ===
def resample_with_drift(sig, drift_ratio):
    new_len = int(len(sig) * (1 + drift_ratio))
    resampled = resample(sig, new_len)
    # 길이를 다시 맞춤 (짧으면 pad, 길면 자름)
    if len(resampled) < len(sig):
        resampled = np.pad(resampled, (0, len(sig) - len(resampled)), mode='constant')
    else:
        resampled = resampled[:len(sig)]
    return resampled.astype(np.int16)


# === 빔포밍 (Drift 보정 포함) ===
def beamform_with_drift(sig1, sig2):
    delay = estimate_delay(sig1, sig2, MAX_SHIFT)
    drift_log.append(delay)
    if len(drift_log) > DRIFT_WINDOW:
        drift_log.pop(0)

    drift_avg = np.mean(drift_log)
    drift_ratio = drift_avg / len(sig1)

    # drift 크면 보정 수행
    if abs(drift_avg) >= DRIFT_THRESHOLD:
        sig2 = resample_with_drift(sig2, drift_ratio)

    aligned2 = np.roll(sig2, -delay)
    return ((sig1 + aligned2) / 2).astype(np.int16)


# === USB 마이크 인덱스 찾기 ===
def find_input_devices(p):
    usb_devices = []
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if 'USB' in info['name'] and info['maxInputChannels'] >= 1:
            usb_devices.append(i)
    return usb_devices[:2]


# === 메인 루프 ===
def main(callback_audio):
    p = pyaudio.PyAudio()
    dev_idxs = find_input_devices(p)
    if len(dev_idxs) < 2:
        print("❌ USB 마이크 2개 필요")
        return

    stream1 = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True,
                     input_device_index=dev_idxs[0], frames_per_buffer=CHUNK)
    stream2 = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True,
                     input_device_index=dev_idxs[1], frames_per_buffer=CHUNK)

    print("🎙️  빔포밍 + 드리프트 보정 시작")

    try:
        while True:
            data1 = np.frombuffer(stream1.read(CHUNK, exception_on_overflow=False), dtype=np.int16)
            data2 = np.frombuffer(stream2.read(CHUNK, exception_on_overflow=False), dtype=np.int16)

            output = beamform_with_drift(data1, data2)
            callback_audio(output.tobytes())

    except KeyboardInterrupt:
        print("🛑 종료됨")

    finally:
        stream1.stop_stream()
        stream1.close()
        stream2.stop_stream()
        stream2.close()
        p.terminate()


if __name__ == '__main__':
    main(send_beamformed)
