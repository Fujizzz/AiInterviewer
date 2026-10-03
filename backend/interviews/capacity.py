"""Responsibilities: Enforce shared same-host service capacity across processes without database
counters.
Implementation: Acquire one non-blocking OS lock file per slot and use reference counts so cancelled
synchronous work retains its slot.
Related Modules: resource_gate admits ASGI requests; BackendLLM borrows the connection slot until
the actual call returns.

Declaration Index:
- CapacityExceeded: Indicate that all slots are occupied and callers must reject work explicitly.
- Lease: Hold an OS file lock and a thread-safe reference count.
- Lease.__init__: Store an acquired file lock with one initial request reference.
- Lease.retain: Add an in-flight-work reference; released leases cannot be reused.
- Lease.release: Drop a reference and unlock/close only when the last reference is released.
- take_slot: Try a bounded set of lock files without queueing or retrying business work.

Variable Index:
- logger: Records resource name and admission outcome only.

Constraints:
All workers must share the same directory and limit. Do not delete lock files while the service
runs; the OS releases locks on process exit.
This is a same-host capacity limit, not a cross-host quota or a guarantee that provider billing can
be cancelled.
"""

import errno
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class CapacityExceeded(Exception):
    """Indicate that capacity is exhausted and callers must reject instead of using an Agent
    fallback.
    """


class Lease:
    """Hold a kernel file lock and thread-safe reference count; the file handle is not passed to
    child processes.
    """

    def __init__(self, file):
        """Store the locked file with one initial admission reference; take_slot is the only source
        of this input.
        """
        self.file = file
        self.references = 1
        self.lock = threading.Lock()

    def retain(self):
        """Add a reference for in-flight work and return this lease; a released lease cannot be
        reused.
        """
        with self.lock:
            if self.references == 0:
                raise RuntimeError("Capacity lease has been released")
            self.references += 1
        return self

    def release(self):
        """Release one reference and close the lock file only for the final reference; duplicate
        release is a programming error.
        """
        with self.lock:
            if self.references <= 0:
                raise RuntimeError("Capacity lease released twice")
            self.references -= 1
            if self.references == 0:
                self.file.close()


def take_slot(name, limit, directory=None):
    """Try a bounded set of shared lock files without waiting; reject when capacity is exhausted.

    Inputs are a fixed resource name, positive integer limit, and optional test directory;
    production uses SERVICE_CAPACITY_DIR.
    Return a Lease. Only lock contention means full capacity; permission and other errors propagate
    so the capacity gate cannot be bypassed.
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
