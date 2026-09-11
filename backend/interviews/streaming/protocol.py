"""WebSocket 回传协议的确定性状态与校验。

目录：协议常量；ProtocolError；parse_control；EchoState。
EchoState 方法：hello、start、accept_chunk、finish、summary。
设计：消息校验与网络调度解耦；单连接只保留计数和元数据，不保存分片。
不变量：序号连续递增、计数仅在校验成功后更新、既定容量限制保持不变。
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
    """携带稳定错误码、可展示说明和 WebSocket 关闭码的协议异常。"""

    def __init__(self, code, detail, close_code=1008):
        """保存响应所需字段；默认关闭码 1008 表示违反应用协议。"""
        super().__init__(detail)
        self.code, self.detail, self.close_code = code, detail, close_code


def parse_control(text):
    """将有界 UTF-8 JSON 控制消息转换为字典。

    方法：先按编码后字节数限流，再解析 JSON，最后拒绝数组和标量。
    返回：字典。超限、格式错误分别抛出带既有 code 的 ProtocolError。
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
    """单连接状态容器；只保存常数规模元数据，不持有原始媒体。"""

    connection_id: str = field(default_factory=lambda: str(uuid4()))
    status: str = "disconnected"
    mode: str = ""
    mime_type: str = ""
    chunk_count: int = 0
    byte_count: int = 0
    verified_chunks: int = 0
    error_code: str = ""

    def hello(self):
        """构造握手公告，将连接 ID、容量和空闲超时显式提供给客户端。"""
        return {
            "type": "hello",
            "connection_id": self.connection_id,
            "max_chunk_bytes": MAX_CHUNK_BYTES,
            "max_total_bytes": MAX_TOTAL_BYTES,
            "idle_timeout_seconds": IDLE_TIMEOUT_SECONDS,
        }

    def start(self, message):
        """验证测试模式与 MIME 元数据，并将连接标记为已声明模式。

        输入：含 mode、mime_type 的字典；每个连接只允许成功声明一次。
        返回：started 响应；重复声明或字段错误抛出 ProtocolError。
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
        """校验一个二进制消息、更新计数并生成 ACK。

        方法：依次检查 start、非空载荷、容量和大端 uint32 序号，随后计算哈希。
        返回：包含 sequence、bytes、sha256 的 ACK。原消息由传输层原样回传。
        副作用：仅增加内存计数；任何校验失败均不改变计数。
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
        """确认客户端校验计数与服务端计数一致，构造完成响应。

        方法：严格检查整数类型，避免将布尔值视为计数；不要求 start，支持纯 ping。
        返回：finished 响应；计数不匹配抛出 ProtocolError，不写数据库。
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
        """生成协议公开统计，显式列字段以避免内部状态意外进入响应。"""
        return {
            "status": self.status,
            "mode": self.mode,
            "mime_type": self.mime_type,
            "chunk_count": self.chunk_count,
            "byte_count": self.byte_count,
            "verified_chunks": self.verified_chunks,
            "error_code": self.error_code,
        }
