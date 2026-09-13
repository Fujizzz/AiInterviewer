"""职责：在隔离的 Linux 文件系统和网络命名空间内执行 PDF 提取与渲染。

实现：先限制地址空间、CPU、文件与系统调用，再导入原解析模块；stdin/stdout 为唯一数据通道。
关联：pdf_supervisor 只挂载本文件、resume_pdf 与 resume_cleanup 及依赖，不挂载项目目录或 .env。

目录：
- install_limits：设置不可提升的进程资源限制及 seccomp 拒绝规则。
- probe：验证网络、宿主路径、环境和派生进程限制，仅供显式运维自检。
- main：从有界 stdin 读取 PDF，以原参数提取渲染，返回 JSON 页面或固定错误码。

关键变量：
（无模块级变量。）
约束：
本文件只在 Linux 沙箱中运行，不加载 Django、不读取任何模型凭据、不调用视觉服务。
--probe 是可信启动参数，不来自上传文件；用于验证操作系统边界，不能替代对恶意文件的长期测试。
"""

import base64
import ctypes
import errno
import json
import logging
import os
import sys


def install_limits(memory_mib, cpu_seconds):
    """设置不可提升的进程资源限制及 seccomp 拒绝规则；任何设置失败立即退出。

    输入为正整数 MiB 与 CPU 秒。限制在 PDF 库导入前生效，不改变 PDF 页数、像素和提取算法。
    禁止派生/替换进程和创建 socket，避免通过子进程规避每进程限额；命名空间额外隔离网络。
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
    """验证网络、宿主路径、环境和派生进程限制，仅供显式运维自检；返回布尔证据。"""
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
    """从有界 stdin 读取 PDF，以原参数提取渲染，返回 JSON 页面或固定错误码。

    隐式输入为沙箱固定 /runtime/lib 与 /worker 挂载及可信 CLI 限额；无文件持久化。
    抑制解析库日志，错误正文不离开沙箱；内存耗尽、CPU 超限或崩溃由监督进程识别。
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
