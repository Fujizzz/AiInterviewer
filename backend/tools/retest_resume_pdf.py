"""职责：显式重测本地 PDF 的原有规则/视觉管线，输出有限诊断与私有结果文件。
实现：默认传统提取；advanced 使用当前行号协议和严格范围校验，
保留已配置模型参数，不重试、不回退、不调整预算。
关联：pdf_sandbox、ResumeVision、ResumeCleanupAgent；只在直接执行时调用真实模型。
目录：
- ObservedVision：保存本次校对结构以生成不含原文的定位/无效修改统计。
- ObservedVision.__init__：创建原视觉客户端并初始化观察状态。
- ObservedVision.review：委派原模型并记录返回结构，不改写输入或输出。
- retest：按模式运行管线并保存成功结果；高级模式另存页面，失败只打印有限错误定位。
- retest_http：尝试取得 CSRF 并调用上传接口；无匿名页面时明确失败，不绕过会话认证。
- main：校验命令行路径并加载后端设置，执行一次显式真实重测。
关键变量：
（无模块级变量。）
配置说明：
ObservedVision.result 保存单页原响应；路径仅来自 CLI，输出目录应为私有目录。
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


class ObservedVision:
    """功能：诊断端口；逻辑：包装原适配器；约束：不修改生产模型结果或失败语义。"""

    def __init__(self):
        """无显式输入，原客户端读取既有环境；result 初始为空，调用者负责关闭 client。"""
        from interviews.resume_vision import ResumeVision

        self.client = ResumeVision()
        self.result = None

    async def review(self, page, prompt):
        """输入原页面和提示，输出原 PageReview；先清空上页观察结果，模型异常直接传播。"""
        self.result = None
        self.result = await self.client.review(page, prompt)
        return self.result


async def retest(source, output, *, mode="traditional"):
    """输入 PDF/私有输出路径及模式，返回成功布尔；传统模式不构造模型，高级模式严格校验。
    仅输出行号、范围有效标记、替换长度和空理由标记，不打印正文、供应商异常或密钥。
    """
    from interviews.pdf_sandbox import parse_pdf
    from interviews.resume_api import resume_failure_code

    from agents.resume_cleanup import ResumeCleanupAgent, replacement_text, source_lines

    pages = await parse_pdf(source.read_bytes())
    output.mkdir(parents=True, exist_ok=True)
    print(f"rules_completed pages={len(pages)}", flush=True)
    if mode == "traditional":
        results = [
            {
                "number": page.number,
                "text": page.text,
                "changed": False,
                "uncertainties": page.warnings,
            }
            for page in pages
        ]
        (output / "result.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("pipeline_completed mode=traditional", flush=True)
        return True
    vision = ObservedVision()
    results = []
    try:
        for page in pages:
            (output / f"page-{page.number}.png").write_bytes(page.image_png)
            try:
                result = await ResumeCleanupAgent(vision).clean_page(page)
            except Exception as exc:
                print(
                    f"pipeline_failed page={page.number} code={resume_failure_code(exc)} "
                    f"exception={type(exc).__name__}",
                    flush=True,
                )
                if vision.result is not None:
                    lines = source_lines(page.text)
                    for index, item in enumerate(vision.result.corrections):
                        valid = 1 <= item.start_line <= item.end_line <= len(lines)
                        same = valid and "".join(
                            lines[item.start_line - 1 : item.end_line]
                        ) == replacement_text(item, lines)
                        print(
                            json.dumps(
                                {
                                    "correction_index": index,
                                    "start_line": item.start_line,
                                    "end_line": item.end_line,
                                    "range_valid": valid,
                                    "replacement_chars": len(item.text),
                                    "same_text": same,
                                    "reason_blank": not item.reason.strip(),
                                }
                            ),
                            flush=True,
                        )
                return False
            results.append(result.model_dump(mode="json"))
            print(
                f"page_completed page={page.number} changed={result.changed} "
                f"corrections={len(result.corrections)} uncertainties={len(result.uncertainties)}",
                flush=True,
            )
        (output / "result.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("pipeline_completed", flush=True)
        return True
    finally:
        await vision.client.close()


async def retest_http(source, output, url, *, mode="traditional"):
    """输入 PDF、私有目录、本地服务 URL 和模式，返回成功布尔；取得 CSRF 后消费所选 NDJSON。
    前置条件为 /agent/ 允许匿名访问；当前受保护页面会返回设置失败，不创建或窃取用户会话。
    HTTP 客户端等待 180 秒覆盖原 90 秒模型预算与沙箱开销，不修改服务模型预算或重试。
    只打印阶段和固定失败码；结果存私有文件，缺失终态或 HTTP 拒绝均失败，不自动采用文本。
    """
    import httpx

    output.mkdir(parents=True, exist_ok=True)
    completed = False
    async with httpx.AsyncClient(timeout=180, follow_redirects=False) as client:
        page = await client.get(url + "/agent/")
        if page.status_code != 200:
            print(f"http_setup_failed status={page.status_code}", flush=True)
            return False
        headers = {
            "Origin": url,
            "Referer": url + "/agent/",
            "X-CSRFToken": client.cookies.get("csrftoken", ""),
        }
        async with client.stream(
            "POST",
            url + "/api/resume/parse/",
            files={"file": ("resume.pdf", source.read_bytes(), "application/pdf")},
            data={"mode": mode},
            headers=headers,
        ) as response:
            if response.status_code != 200:
                print(f"http_failed status={response.status_code}", flush=True)
                return False
            async for line in response.aiter_lines():
                if not line:
                    continue
                event = json.loads(line)
                print(
                    f"http_event type={event['type']} stage={event.get('stage', '')} "
                    f"code={event.get('code', '')}",
                    flush=True,
                )
                if event["type"] == "error":
                    return False
                if event["type"] == "result":
                    (output / "http-result.json").write_text(
                        json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    completed = True
    print(f"http_completed success={completed}", flush=True)
    return completed


def main():
    """输入 CLI 路径、模式（默认传统）和可选 HTTP URL；加载原环境，单次运行，失败退出 1。"""
    backend = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    parser = argparse.ArgumentParser(description="Explicit live PDF pipeline retest; no retries.")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--http-url", help="Explicit local backend URL for real HTTP retest.")
    parser.add_argument("--mode", choices=["traditional", "advanced"], default="traditional")
    args = parser.parse_args()
    operation = (
        retest_http(args.source, args.output, args.http_url.rstrip("/"), mode=args.mode)
        if args.http_url
        else retest(args.source, args.output, mode=args.mode)
    )
    return 0 if asyncio.run(operation) else 1


if __name__ == "__main__":
    raise SystemExit(main())
