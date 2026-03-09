import asyncio
import struct

# slin = signed 16-bit linear PCM at 8 kHz
# Asterisk uses PT=10 for slin/8kHz in ExternalMedia (verified from live packet capture)
PAYLOAD_TYPE  = 10
SAMPLE_RATE   = 8_000    # Hz
FRAME_MS      = 20       # ms per RTP frame
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)   # 160 samples
FRAME_BYTES   = FRAME_SAMPLES * 2                     # 320 bytes (16-bit)
RTP_HEADER    = 12                                    # bytes

def _byteswap16(data: bytes) -> bytes:
    """Swap bytes of every 16-bit sample (convert between LE and BE)."""
    ba = bytearray(len(data))
    ba[0::2] = data[1::2]   # put MSB into even positions
    ba[1::2] = data[0::2]   # put LSB into odd positions
    return bytes(ba)


def decode_rtp(packet: bytes) -> bytes:
    """Strip RTP header and return PCM payload as little-endian (for STT)."""
    if len(packet) <= RTP_HEADER:
        return b""
    payload = packet[RTP_HEADER:]
    return _byteswap16(payload)   # RTP is big-endian; convert to LE for processing


def inspect_rtp(packet: bytes) -> str:
    """Return a short diagnostic string for the first packet."""
    if len(packet) < RTP_HEADER:
        return f"too short ({len(packet)} bytes)"
    pt = packet[1] & 0x7F
    seq = struct.unpack("!H", packet[2:4])[0]
    return f"PT={pt} seq={seq} payload={len(packet)-RTP_HEADER}b"


def encode_rtp(payload: bytes, seq: int, timestamp: int, ssrc: int = 12345) -> bytes:
    """Wrap raw PCM in an RTP header (V=2, PT=11/slin16)."""
    header = struct.pack(
        "!BBHII",
        0x80,           # V=2, P=0, X=0, CC=0
        PAYLOAD_TYPE,   # M=0, PT=11
        seq & 0xFFFF,
        timestamp & 0xFFFFFFFF,
        ssrc & 0xFFFFFFFF,
    )
    return header + payload


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, queue: asyncio.Queue) -> None:
        self._queue     = queue
        self._transport = None

    def connection_made(self, transport) -> None:
        self._transport = transport

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        print(f"[UDP] Packet from {addr}  len={len(data)}")
        self._queue.put_nowait((data, addr))

    def error_received(self, exc: Exception) -> None:
        print(f"[RTP] UDP error: {exc}")


class UdpAudioStream:
    """
    Bidirectional RTP/UDP bridge between Asterisk ExternalMedia and Python.

    Asterisk sends RTP to our UDP port → receive() returns decoded PCM.
    We send PCM via send() → encode_rtp() → sent back to Asterisk's port.
    The remote address is learned from the first packet received.
    """

    def __init__(self) -> None:
        self._queue       : asyncio.Queue      = asyncio.Queue()
        self._transport                        = None
        self._remote_addr : tuple | None       = None
        self._seq         : int                = 0
        self._timestamp   : int                = 0

    async def start(self, host: str = "0.0.0.0", port: int = 7000) -> None:
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _UdpProtocol(self._queue),
            local_addr=(host, port),
        )
        print(f"[RTP] UDP server listening on {host}:{port}")

    async def receive(self) -> bytes:
        """Wait for the next inbound RTP packet and return its PCM payload."""
        data, addr = await self._queue.get()
        if self._remote_addr != addr:
            self._remote_addr = addr
            print(f"[RTP] Asterisk RTP source: {addr}  {inspect_rtp(data)}")
        return decode_rtp(data)

    def send(self, payload: bytes) -> None:
        """Send one frame of raw PCM to Asterisk as an RTP packet."""
        if self._transport is None or self._remote_addr is None:
            return
        packet = encode_rtp(_byteswap16(payload), self._seq, self._timestamp)
        if self._seq == 0:
            print(f"[RTP] Sending audio to: {self._remote_addr}")
        self._transport.sendto(packet, self._remote_addr)

        self._seq       = (self._seq + 1) & 0xFFFF
        self._timestamp = (self._timestamp + FRAME_SAMPLES) & 0xFFFFFFFF

    def stop(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None
