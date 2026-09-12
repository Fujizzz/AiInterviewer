"""职责：本地 PDF 规则提取和有界页面渲染，不调用模型、不保存上传文件。
实现：pypdf 保留布局抽取，保守字符清理；PDFium 在互斥锁内生成 PNG。
关联：resume_api 将本模块移到工作线程，agents.resume_cleanup 接收单页证据。

目录：
- PdfInputError：可安全展示的输入校验错误。
- normalize_text：修复明确的排版字符并保持行列空白。
- extract_pdf：校验 PDF 并按页提取规则文本。
- render_pages：生成限定像素的图像并关闭所有 PDFium 原生资源。

关键变量：
- MAX_BYTES：此新增上传入口接受的最大 PDF 字节数。
- MAX_PAGES：此新增入口接受的最大页数，不截断超限文档。
- MAX_TEXT：单页可接受的最大提取字符数，超限明确失败。
- IMAGE_EDGE：最长边像素上限，限制图像内存及视觉输入规模。
- PDFIUM_LOCK：保护非线程安全的 PDFium 全部对象生命周期。
- CHAR_MAP：仅修复已知排版连字和不换行空格，不猜测 OCR 字符。
"""

from dataclasses import replace
from io import BytesIO
from math import isfinite, nextafter
from threading import Lock

import pypdfium2 as pdfium
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from agents.resume_cleanup import ResumePage

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10
MAX_TEXT = 30000
IMAGE_EDGE = 1800
PDFIUM_LOCK = Lock()
CHAR_MAP = str.maketrans(
    {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\u00a0": " ",
    }
)


class PdfInputError(ValueError):
    """功能：标识预期输入失败；逻辑：异常文本为固定诊断语句；约束：不含简历数据。"""


def normalize_text(text: str) -> str:
    """输入布局文本，输出统一换行及明确连字修复的文本。

    保留行首缩进和内部多空格以免进一步破坏分栏；不合并断词、不猜测乱码，
    不删除页眉或重复经历。原始文本由调用方独立保留，无 I/O 副作用。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").translate(CHAR_MAP)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip("\n")


def extract_pdf(data: bytes) -> list[ResumePage]:
    """输入完整 PDF 字节，返回原页序规则文本与风险提示；不做 OCR 或视觉调用。

    超限、加密、损坏或零页抛 PdfInputError，不截断、不尝试解密。
    无文本页仍保留，可供视觉转写。pypdf 解析错误转成固定诊断，不暴露文件内容。
    """
    if not data or len(data) > MAX_BYTES:
        raise PdfInputError("PDF 必须非空且不超过 10 MiB。")
    if not data.startswith(b"%PDF-"):
        raise PdfInputError("文件内容不是 PDF。")
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise PdfInputError("暂不接受加密 PDF，请先解密后上传。")
        if not 1 <= len(reader.pages) <= MAX_PAGES:
            raise PdfInputError("PDF 必须包含 1 至 10 页。")
        pages = []
        for number, page in enumerate(reader.pages, 1):
            raw = page.extract_text(extraction_mode="layout", layout_mode_strip_rotated=False)
            if len(raw) > MAX_TEXT:
                raise PdfInputError("单页文本超过 30000 字符，请拆分文档。")
            text = normalize_text(raw)
            warnings = ["规则提取保留布局空格；分栏、图表和阅读顺序需要核对。"]
            if not text.strip():
                warnings.append("未提取到文字，可能是扫描页或空白页；可使用视觉整理。")
            if "\ufffd" in text or "\x00" in text:
                warnings.append("检测到异常字符，请对照原 PDF 核对或使用视觉整理。")
            pages.append(ResumePage(number, raw, text, warnings))
        return pages
    except PdfInputError:
        raise
    except (PyPdfError, ValueError, TypeError, KeyError, OverflowError) as exc:
        raise PdfInputError("PDF 结构或文本编码无法解析，请检查或重新导出文件。") from exc


def render_pages(data: bytes, pages: list[ResumePage]) -> list[ResumePage]:
    """输入已校验 PDF 和页列表，输出带 PNG 的新列表；无磁盘写入。

    在同一互斥区创建、使用和关闭 PDFium 对象，防止并发线程破坏原生状态。
    最长边最多 IMAGE_EDGE 像素；任一页失败则整体抛错，不跳过页面。
    取消异步调用无法抢占已运行线程，但 finally 仍释放位图、页面和文档。
    """
    with PDFIUM_LOCK, pdfium.PdfDocument(data) as document:
        if len(document) != len(pages):
            raise PdfInputError("文本解析与页面渲染的页数不一致。")
        rendered = []
        for source in pages:
            page = document[source.number - 1]
            try:
                width, height = page.get_size()
                if not all(isfinite(value) and value > 0 for value in (width, height)):
                    raise PdfInputError("PDF 页面尺寸无效。")
                # PDFium 向上取整像素；向零取相邻浮点数，避免恰好上限时多出一像素。
                scale = min(2.5, nextafter(IMAGE_EDGE / max(width, height), 0))
                bitmap = page.render(scale=scale)
                try:
                    image = bitmap.to_pil()
                    try:
                        output = BytesIO()
                        image.save(output, format="PNG")
                        rendered.append(replace(source, image_png=output.getvalue()))
                    finally:
                        image.close()
                finally:
                    bitmap.close()
            finally:
                page.close()
        return rendered
