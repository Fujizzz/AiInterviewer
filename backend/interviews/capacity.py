"""职责：通过操作系统文件锁提供同机多进程共享的服务容量，不把计数写入业务数据库。

实现：每个名额对应一个锁文件，非阻塞获取；引用计数使取消后的同步调用仍占用名额。
关联：resource_gate 在 ASGI 入口准入，BackendLLM 借用连接名额直至实际调用返回。

目录：
- CapacityExceeded：名额已用尽，调用方须明确拒绝，不能进入 Agent 回退路径。
- Lease：持有一个内核文件锁及线程安全引用计数。
- Lease.__init__：保存已加锁文件，初始引用为准入请求本身。
- Lease.retain：增加在途工作引用，已释放的租约不能重新使用。
- Lease.release：归还引用，最后一个引用才解锁关闭文件。
- take_slot：非阻塞尝试有限个共享锁文件，满额时拒绝，不排队或重试业务。

关键变量：
- logger：只记录资源名称和准入结果。

约束：
所有 worker 必须使用相同的目录与限额。锁文件不可在服务运行时删除，进程退出由内核释放锁。
这是同机服务容量限制，不是跨主机配额或供应商计费取消保证。
"""

import errno
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class CapacityExceeded(Exception):
    """名额已用尽，调用方须明确拒绝，不能进入 Agent 回退路径。"""


class Lease:
    """持有一个内核文件锁及线程安全引用计数；文件句柄不会传给子进程。"""

    def __init__(self, file):
        """保存已加锁文件，初始引用为准入请求本身；输入只能由 take_slot 提供。"""
        self.file = file
        self.references = 1
        self.lock = threading.Lock()

    def retain(self):
        """增加在途工作引用，已释放的租约不能重新使用；返回本租约，不执行模型调用。"""
        with self.lock:
            if self.references == 0:
                raise RuntimeError("Capacity lease has been released")
            self.references += 1
        return self

    def release(self):
        """归还引用，最后一个引用才解锁关闭文件；重复释放是编程错误。"""
        with self.lock:
            if self.references <= 0:
                raise RuntimeError("Capacity lease released twice")
            self.references -= 1
            if self.references == 0:
                self.file.close()


def take_slot(name, limit, directory=None):
    """非阻塞尝试有限个共享锁文件，满额时拒绝，不排队或重试业务。

    输入为固定资源名、正整数限额及可选测试目录；生产目录由 SERVICE_CAPACITY_DIR 指定。
    返回 Lease。仅锁竞争视为满额，目录权限或其他错误直接传播，不能跳过资源控制。
    """
    if name not in {"agent", "pdf"} or type(limit) is not int or limit < 1:
        raise ValueError("Invalid service capacity configuration")
    root = Path(
        directory
        or os.environ.get(
            "SERVICE_CAPACITY_DIR", str(Path(__file__).resolve().parents[1] / ".capacity")
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    for index in range(limit):
        file = (root / f"{name}-{index}.lock").open("a+b")
        try:
            file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            file.close()
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
        else:
            logger.info("Service capacity admitted resource=%s slot=%d", name, index)
            return Lease(file)
    logger.warning("Service capacity exhausted resource=%s limit=%d", name, limit)
    raise CapacityExceeded("Service capacity exhausted")
