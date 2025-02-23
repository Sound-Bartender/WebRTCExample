unset PYTHONPATH
pip install --upgrade pip setuptools

pip install requests aiortc av numpy opencv-python aiohttp

# 라즈베리파이가 아니면 오류남
sudo apt update
sudo apt install -y portaudio19-dev
sudo apt install -y libcamera-dev libcamera-apps
sudo apt install python3-picamera2 --no-install-recommends

pip install picamera2 pyaudio

# copy
cp -r /usr/lib/python3/dist-packages/libcamera* venv/lib/python3.11/site-packages/
cp -r /usr/lib/python3/dist-packages/pykms venv/lib/python3.11/site-packages/