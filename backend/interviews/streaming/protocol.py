"""Responsibilities: Validate the deterministic state and control messages of the WebSocket echo
protocol.
Implementation: Enforce connection, MIME, chunk, byte, and completion-count boundaries using bounded
metadata only.
Related Modules: streaming.websocket dispatches protocol operations and sends responses over ASGI.

Declaration Index:
- ProtocolError: Carry a stable error code, display detail, and WebSocket close code.
- ProtocolError.__init__: Store response fields; default close code 1008 represents an application
  protocol violation.
- parse_control: Decode a bounded UTF-8 JSON control message into a dictionary.
- EchoState: Hold constant-size metadata for one connection without retaining raw media.
- EchoState.hello: Build the handshake notice with connection ID, capacity, and idle timeout.
- EchoState.start: Validate the declared test mode and MIME metadata.
- EchoState.accept_chunk: Validate one binary message, update counters, and build its ACK.
- EchoState.finish: Match client-reported counts with server counts and build the completion
  response.
- EchoState.summary: Return explicit public protocol statistics.

Variable Index:
- IDLE_TIMEOUT_SECONDS: Stream-diagnostic receive timeout in seconds; it does not apply to Agent
  response time.
- MAX_CHUNK_BYTES: Maximum binary payload bytes, excluding the four-byte sequence header.
- MAX_CONTROL_BYTES: Maximum UTF-8 encoded size of a stream control JSON message.
- MAX_TOTAL_BYTES: Maximum cumulative payload bytes per connection.

State and Constraints:
EchoState.connection_id identifies the connection; status/mode/mime_type describe diagnostic state.
chunk_count/byte_count update only after valid chunks.
verified_chunks comes from the client completion report and is not trusted proof from a potentially
malicious client.
"""

import hashlib
import json
import struct
from dataclasses import dataclass, field
from uuid import uuid4

MAX_CHUNK_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
IDLE_TIMEOUT_SECONDS = 30
MAX_CONTROL_BYTES = 4096


class ProtocolError(Exception):
    """Carry a stable error code, display detail, and WebSocket close code.
    """

    def __init__(self, code, detail, close_code=1008):
        """Store response fields; default close code 1008 represents an application protocol
        violation.
        """
        super().__init__(detail)
        self.code, self.detail, self.close_code = code, detail, close_code


def parse_control(text):
    """Decode a bounded UTF-8 JSON control message into a dictionary.

    Enforce the encoded-byte limit before parsing and reject arrays or scalars. Size, encoding, and
    JSON failures raise
    ProtocolError with the established error code.
    """
    if len(text.encode("utf-8")) > MAX_CONTROL_BYTES:
        raise ProtocolError("invalid_message", "Control messages must not exceed 4096 bytes.")
    try:
        message = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ProtocolError("invalid_json", "Expected a JSON object.") from exc
    if not isinstance(message, dict):
        raise ProtocolError("invalid_message", "Expected a JSON object.")
    return message


@dataclass
class EchoState:
    """Hold constant-size metadata for one connection without retaining raw media.
    """

    connection_id: str = field(default_factory=lambda: str(uuid4()))
    status: str = "disconnected"
    mode: str = ""
    mime_type: str = ""
    chunk_count: int = 0
    byte_count: int = 0
    verified_chunks: int = 0
    error_code: str = ""

    def hello(self):
        """Build the handshake notice with connection ID, capacity limits, and idle timeout.
        """
        return {
            "type": "hello",
            "connection_id": self.connection_id,
            "max_chunk_bytes": MAX_CHUNK_BYTES,
            "max_total_bytes": MAX_TOTAL_BYTES,
            "idle_timeout_seconds": IDLE_TIMEOUT_SECONDS,
        }

    def start(self, message):
        """Validate mode and MIME metadata, then mark this connection's mode as declared.

        Input is a dictionary containing mode and mime_type; each connection may declare a mode
        once.
        Return a started response. Duplicate declarations or invalid fields raise ProtocolError.
        """
        mode, mime = message.get("mode"), message.get("mime_type")
        if self.mode:
            raise ProtocolError("already_started", "Each connection supports one test.")
        if (
            mode not in ("binary", "audio", "video", "synthetic")
            or not isinstance(mime, str)
            or not 1 <= len(mime) <= 100
        ):
            raise ProtocolError(
                "invalid_start",
                "Provide a supported mode and a 1-100 character mime_type.",
            )
        self.mode, self.mime_type = mode, mime
        return {"type": "started", "mode": mode}

    def accept_chunk(self, binary):
        """Validate one binary message, update counters, and return ACK metadata.

        Require start, a non-empty payload, limits, and a contiguous big-endian uint32 sequence
        before hashing.
        The ACK contains sequence, byte count, and SHA-256; the transport layer echoes the original
        message unchanged.
        Only in-memory counters change, and validation failures leave them unchanged.
        """
        if not self.mode:
            raise ProtocolError("not_started", "Send start before binary frames.")
        if len(binary) < 5:
            raise ProtocolError(
                "invalid_frame",
                "Frame requires a 4-byte sequence and nonempty payload.",
            )
        sequence = struct.unpack("!I", binary[:4])[0]
        payload = memoryview(binary)[4:]
        if len(payload) > MAX_CHUNK_BYTES or self.byte_count + len(payload) > MAX_TOTAL_BYTES:
            raise ProtocolError(
                "size_limit",
                "Frame or total payload exceeds the advertised limit.",
                1009,
            )
        if sequence != self.chunk_count + 1:
            raise ProtocolError(
                "sequence_mismatch",
                "Sequences must start at 1 and increase by exactly 1.",
            )
        self.chunk_count += 1
        self.byte_count += len(payload)
        return {
            "type": "ack",
            "sequence": sequence,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    def finish(self, message):
        """Verify client-reported counts against server counts and build the completion response.

        Require exact integers so booleans cannot act as counts. A start is not required, supporting
        ping-only connections.
        Return a finished response; count mismatches raise ProtocolError. No database writes occur.
        """
        verified, verified_bytes = message.get("verified_chunks"), message.get("verified_bytes")
        if (
            type(verified) is not int
            or type(verified_bytes) is not int
            or verified != self.chunk_count
            or verified_bytes != self.byte_count
        ):
            raise ProtocolError(
                "verification_mismatch",
                "Verified counts must match the received payloads.",
            )
        self.status, self.verified_chunks = "finished", verified
        return {"type": "finished", "connection_id": self.connection_id, **self.summary()}

    def summary(self):
        """Return public protocol statistics using explicit fields to prevent internal state
        leakage.
        """
        return {
            "status": self.status,
            "mode": self.mode,
            "mime_type": self.mime_type,
            "chunk_count": self.chunk_count,
            "byte_count": self.byte_count,
            "verified_chunks": self.verified_chunks,
            "error_code": self.error_code,
        }
