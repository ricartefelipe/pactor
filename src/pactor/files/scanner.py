"""Cliente mínimo do clamd (protocolo INSTREAM sobre TCP).

Sem dependências externas: o protocolo é simples e documentado no ClamAV.
Qualquer falha de comunicação vira :class:`ScannerUnavailableError`: um
arquivo nunca é considerado limpo sem resposta explícita ``OK`` do scanner.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from enum import StrEnum

_CHUNK = 64 * 1024
_MAX_RESPONSE = 4096


class ScanVerdict(StrEnum):
    CLEAN = "clean"
    INFECTED = "infected"


@dataclass(frozen=True, slots=True)
class ScanResult:
    verdict: ScanVerdict
    signature: str
    engine: str


class ScannerUnavailableError(RuntimeError):
    """Scanner inacessível, com erro ou resposta inesperada."""


class ClamdScanner:
    def __init__(self, host: str, port: int, *, timeout: float = 30.0) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout

    def _command(self, payload: bytes, *, stream: bytes | None = None) -> str:
        try:
            with socket.create_connection((self._host, self._port), timeout=self._timeout) as conn:
                conn.sendall(payload)
                if stream is not None:
                    for start in range(0, len(stream), _CHUNK):
                        chunk = stream[start : start + _CHUNK]
                        conn.sendall(struct.pack("!L", len(chunk)) + chunk)
                    conn.sendall(struct.pack("!L", 0))
                response = b""
                while not response.endswith(b"\0") and len(response) < _MAX_RESPONSE:
                    part = conn.recv(1024)
                    if not part:
                        break
                    response += part
        except OSError as exc:
            raise ScannerUnavailableError(f"clamd indisponível: {exc}") from exc
        return response.rstrip(b"\0").decode("utf-8", "replace").strip()

    def version(self) -> str:
        return self._command(b"zVERSION\0")

    def scan(self, content: bytes) -> ScanResult:
        engine = self.version()
        reply = self._command(b"zINSTREAM\0", stream=content)
        if reply.endswith(" OK") or reply == "stream: OK":
            return ScanResult(ScanVerdict.CLEAN, "", engine)
        if reply.endswith(" FOUND"):
            signature = reply.removeprefix("stream: ").removesuffix(" FOUND").strip()
            return ScanResult(ScanVerdict.INFECTED, signature, engine)
        raise ScannerUnavailableError(f"resposta inesperada do clamd: {reply[:200]!r}")
