"""Responsibilities: execute PDF extraction and rendering inside isolated Linux filesystem and
network namespace.

Implementation: first apply address space, CPU, file, and system call limits, then import original
parsing module; stdin/stdout are the only data channels.
Related Modules: pdf_supervisor mounts only this file, resume_pdf, resume_cleanup, and dependencies,
not project directory or .env.

Declaration Index:
- install_limits: set non-upgradable process resource limits and seccomp deny rules.
- probe: validate network, host path, environment, and derived process limits, for explicit
  operational self-check only.
- main: read PDF from bounded stdin, extract and render with original parameters, return JSON page
  or fixed error code.

Variable Index:
None
Constraints:
This file runs only in Linux sandbox, does not load Django, does not read any model credentials,
does not call visual services.
--probe is trusted startup argument, not from uploaded file; used to verify OS boundaries, cannot
replace long-term testing against malicious files.
"""

import base64
import ctypes
import errno
import json
import logging
import os
import sys


def install_limits(memory_mib, cpu_seconds):
    """Set non-upgradable process resource limits and seccomp deny rules; any failure causes
    immediate exit.

    Input: positive integers in MiB and CPU seconds. Limits take effect before PDF library import,
    do not alter PDF page count, pixels, or extraction algorithm.
    Prohibit process spawning/replacement and socket creation, preventing bypassing per-process
    limits via child processes; namespace provides additional network isolation.
    """
    import resource

    for key, value in (
        (resource.RLIMIT_AS, memory_mib * 1024 * 1024),
        (resource.RLIMIT_CPU, cpu_seconds),
        (resource.RLIMIT_FSIZE, 0),
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_NOFILE, 64),
    ):
        resource.setrlimit(key, (value, value))
    library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    context = library.seccomp_init(0x7FFF0000)
    if not context:
        raise RuntimeError("seccomp initialization failed")
    try:
        for name in (
            b"fork",
            b"vfork",
            b"clone",
            b"clone3",
            b"execve",
            b"execveat",
            b"socket",
            b"socketpair",
            b"ptrace",
            b"process_vm_readv",
            b"process_vm_writev",
            b"setsid",
            b"setpgid",
            b"prctl",
            b"mount",
            b"unshare",
            b"setns",
        ):
            number = library.seccomp_syscall_resolve_name(name)
            if number < 0 or library.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0):
                raise RuntimeError("seccomp rule installation failed")
        if library.seccomp_load(context):
            raise RuntimeError("seccomp activation failed")
    finally:
        library.seccomp_release(context)


def probe():
    """Validate network, host path, environment, and derived process limits, for explicit
    operational self-check only; return boolean evidence.
    """
    import resource
    import socket

    evidence = {"host_hidden": not os.path.exists("/mnt") and not os.path.exists("/home")}
    evidence["secrets_absent"] = not any("KEY" in key or "SECRET" in key for key in os.environ)
    try:
        socket.socket()
    except PermissionError:
        evidence["socket_blocked"] = True
    try:
        os.fork()
    except PermissionError:
        evidence["fork_blocked"] = True
    else:
        os._exit(97)
    evidence["memory_bytes"] = resource.getrlimit(resource.RLIMIT_AS)[0]
    try:
        bytearray(evidence["memory_bytes"])
    except MemoryError:
        evidence["memory_enforced"] = True
    evidence["cpu_seconds"] = resource.getrlimit(resource.RLIMIT_CPU)[0]
    return evidence


def main():
    """Read PDF from bounded stdin, extract and render with original parameters, return JSON page or
    fixed error code.

    Implicit inputs: fixed sandbox /runtime/lib and /worker mounts and trusted CLI limits; no file
    persistence.
    Suppress parsing library logs, error content does not leave sandbox; memory exhaustion, CPU
    over-limit, or crash identified by supervising process.
    """
    install_limits(int(sys.argv[1]), int(sys.argv[2]))
    if len(sys.argv) > 3 and sys.argv[3] == "probe":
        print(json.dumps(probe()))
        return
    sys.path[:0] = ["/runtime/lib", "/worker"]
    logging.disable(logging.CRITICAL)
    from resume_pdf import MAX_BYTES, PdfInputError, extract_pdf, render_pages

    data = sys.stdin.buffer.read(MAX_BYTES + 1)
    try:
        pages = render_pages(data, extract_pdf(data))
        result = {
            "pages": [
                {
                    "number": page.number,
                    "raw_text": page.raw_text,
                    "text": page.text,
                    "warnings": page.warnings,
                    "image_png": base64.b64encode(page.image_png).decode("ascii"),
                }
                for page in pages
            ]
        }
    except PdfInputError:
        result = {"error": "invalid_pdf"}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
