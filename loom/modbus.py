"""Minimal Modbus/TCP: a client (what Loom needs to read Factory I/O's
Modbus TCP/IP Server driver) and a small server (a stand-in for Factory
I/O in tests and on machines without it). No third-party dependency.

Function codes: 1 read coils, 2 read discrete inputs, 3 read holding
registers, 4 read input registers, 5 write single coil, 6 write single
register. Factory I/O maps sensors to discrete inputs / input registers
and actuators to coils / holding registers.
"""
from __future__ import annotations

import socket
import socketserver
import struct
import threading


class ModbusError(RuntimeError):
    pass


class ModbusClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 502, unit: int = 1, timeout: float = 2.0) -> None:
        self.host, self.port, self.unit, self.timeout = host, port, unit, timeout
        self._sock: socket.socket | None = None
        self._tid = 0
        self._lock = threading.Lock()

    def connect(self) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def close(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None

    def _req(self, fc: int, payload: bytes) -> bytes:
        if self._sock is None:
            self.connect()
        with self._lock:
            self._tid = (self._tid + 1) & 0xFFFF
            pdu = bytes([fc]) + payload
            hdr = struct.pack(">HHHB", self._tid, 0, len(pdu) + 1, self.unit)
            self._sock.sendall(hdr + pdu)
            head = self._recv(7)
            tid, _, length, _ = struct.unpack(">HHHB", head)
            body = self._recv(length - 1)
        if body[0] & 0x80:
            raise ModbusError(f"function {fc} failed: exception code {body[1]}")
        return body[1:]

    def _recv(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ModbusError("connection closed")
            buf += chunk
        return buf

    def _read_bits(self, fc: int, address: int, count: int) -> list[bool]:
        data = self._req(fc, struct.pack(">HH", address, count))
        nbytes, raw = data[0], data[1:]
        bits = []
        for b in raw[:nbytes]:
            bits += [bool(b >> k & 1) for k in range(8)]
        return bits[:count]

    def _read_regs(self, fc: int, address: int, count: int) -> list[int]:
        data = self._req(fc, struct.pack(">HH", address, count))
        nbytes = data[0]
        return list(struct.unpack(f">{nbytes // 2}H", data[1:1 + nbytes]))

    def read_coils(self, address: int, count: int) -> list[bool]:
        return self._read_bits(1, address, count)

    def read_discrete_inputs(self, address: int, count: int) -> list[bool]:
        return self._read_bits(2, address, count)

    def read_holding_registers(self, address: int, count: int) -> list[int]:
        return self._read_regs(3, address, count)

    def read_input_registers(self, address: int, count: int) -> list[int]:
        return self._read_regs(4, address, count)

    def write_coil(self, address: int, value: bool) -> None:
        self._req(5, struct.pack(">HH", address, 0xFF00 if value else 0x0000))

    def write_register(self, address: int, value: int) -> None:
        self._req(6, struct.pack(">HH", address, value & 0xFFFF))


class ModbusServer:
    """Tiny multi-client Modbus/TCP server holding four tables."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, size: int = 256) -> None:
        self.coils = [False] * size
        self.discrete = [False] * size
        self.holding = [0] * size
        self.input = [0] * size
        self.lock = threading.Lock()
        srv = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                while True:
                    head = _recv_exact(self.request, 7)
                    if head is None:
                        return
                    tid, pid, length, unit = struct.unpack(">HHHB", head)
                    body = _recv_exact(self.request, length - 1)
                    if body is None:
                        return
                    resp = srv._handle(body)
                    self.request.sendall(struct.pack(">HHHB", tid, pid, len(resp) + 1, unit) + resp)

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = Server((host, port), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> "ModbusServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _handle(self, pdu: bytes) -> bytes:
        fc = pdu[0]
        with self.lock:
            if fc in (1, 2):
                addr, count = struct.unpack(">HH", pdu[1:5])
                table = self.coils if fc == 1 else self.discrete
                bits = table[addr:addr + count]
                out = bytearray((count + 7) // 8)
                for k, b in enumerate(bits):
                    if b:
                        out[k // 8] |= 1 << (k % 8)
                return bytes([fc, len(out)]) + bytes(out)
            if fc in (3, 4):
                addr, count = struct.unpack(">HH", pdu[1:5])
                table = self.holding if fc == 3 else self.input
                regs = table[addr:addr + count]
                return bytes([fc, 2 * count]) + struct.pack(f">{count}H", *regs)
            if fc == 5:
                addr, val = struct.unpack(">HH", pdu[1:5])
                self.coils[addr] = val == 0xFF00
                return pdu
            if fc == 6:
                addr, val = struct.unpack(">HH", pdu[1:5])
                self.holding[addr] = val
                return pdu
        return bytes([fc | 0x80, 1])


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf
