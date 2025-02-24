import subprocess

def main():
    # ffplay를 이용해 UDP 포트 1234에서 스트림 수신 및 실시간 재생
    cmd = [
        "ffplay",
        "-fflags", "nobuffer",
        "-i", "udp://0.0.0.0:5003"
    ]
    print("실시간 스트림 재생 시작...")
    subprocess.run(cmd)

if __name__ == "__main__":
    main()