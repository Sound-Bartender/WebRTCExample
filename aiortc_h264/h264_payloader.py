import math

MTU_SIZE = 1200  # 보통 Ethernet 환경에서 안전한 페이로드 크기(헤더 고려)

class H264Payloader:
    """
    aiortc/examples/h264/h264.py 에 있는 pay() 로직을 단순화/발췌한 클래스
    H.264 NAL 단위를 RTP 페이로드 여러 개로 쪼개줌(Fragmentation).
    """

    def __init__(self):
        pass

    def pay(self, nal, timestamp_increment=3000):
        """
        nal (bytes): 하나의 H.264 NAL(Unit)
        return: RTP에 실릴 payload 바이트들(list)
        """
        # 만약 NAL이 MTU_SIZE 이하라면, 조각 낼 필요가 없음
        if len(nal) <= MTU_SIZE:
            return [nal]

        # NAL이 너무 크면, 다수의 Fragment에 나누어 전송
        fragments = []
        offset = 1  # NAL header를 첫 바이트로 가정
        nal_header = nal[0]  # 첫 바이트에 NAL Type 정보
        forbidden_bit = nal_header & 0x80
        nri = nal_header & 0x60
        nal_type = nal_header & 0x1F

        # Fragmentation Unit Header
        #  -> FU Indicator: [ forbidden_bit | NRI | 28(=FU-A) ]
        fu_indicator = (forbidden_bit | nri | 28)

        while offset < len(nal):
            size = min(MTU_SIZE - 2, len(nal) - offset)  # 2바이트(FU-A header) 확보
            start_bit = 0x80 if offset == 1 else 0x00    # 첫 Fragment
            end_bit = 0x40 if (offset + size) >= len(nal) else 0x00
            fu_header = (start_bit | end_bit | nal_type)

            fragment = bytes([fu_indicator, fu_header]) + nal[offset:offset+size]
            fragments.append(fragment)
            offset += size

        return fragments