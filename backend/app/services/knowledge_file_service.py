from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import HTTPException, UploadFile, status

from app.schemas.knowledge import KnowledgeDocumentCreate
from app.services.knowledge_quality import KnowledgeQualityError, validate_knowledge_text


@dataclass
class ParsedKnowledgeUpload:
    title: str
    category: str
    target_position: str
    content: str
    metadata: dict
    original_content: bytes
    filename: str
    file_type: str
    file_size: int


ALLOWED_KNOWLEDGE_SUFFIXES = {".txt", ".md", ".pdf"}
MAX_KNOWLEDGE_FILE_BYTES = 10 * 1024 * 1024
MAX_DATABASE_CONTENT_BYTES = 3 * 1024 * 1024


class DocumentParser(ABC):
    @abstractmethod
    def parse(self, content: bytes, file_name: str) -> str:
        """将文档二进制内容解析为可供切片和检索的纯文本。"""
        raise NotImplementedError


class TextDocumentParser(DocumentParser):
    def parse(self, content: bytes, file_name: str) -> str:
        """按支持的文本编码解码 TXT 文件。"""
        return decode_text_file(content)


class MarkdownDocumentParser(DocumentParser):
    def parse(self, content: bytes, file_name: str) -> str:
        """解码 Markdown 文件，并保留有助于后续切片的标题和列表标记。"""
        return decode_text_file(content)


class PdfDocumentParser(DocumentParser):
    def parse(self, content: bytes, file_name: str) -> str:
        """优先提取 PDF 文本层，未提取到有效文本时回退到 OCR。"""
        text = extract_pdf_text(content)
        if any(page.strip() for page in _page_contents(text)):
            return text
        return extract_pdf_text_with_ocr(content)


PARSERS_BY_SUFFIX: dict[str, DocumentParser] = {
    ".txt": TextDocumentParser(),
    ".md": MarkdownDocumentParser(),
    ".pdf": PdfDocumentParser(),
}


async def build_document_from_upload(
    file: UploadFile,
    title: str | None,
    category: str,
    target_position: str | None,
) -> ParsedKnowledgeUpload:
    """校验并解析上传文件，返回包含原文件与规范化文本的知识文档数据。"""
    filename = file.filename or "uploaded"
    suffix = Path(filename).suffix.lower()
    parser = get_document_parser(suffix)

    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")
    if len(content) > MAX_KNOWLEDGE_FILE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Uploaded file is too large")

    text = normalize_extracted_text(parser.parse(content, filename))
    if not text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No readable text was extracted from the file")
    try:
        quality_report = validate_knowledge_text(text)
    except KnowledgeQualityError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Knowledge document quality check failed: {exc}",
        ) from exc
    return ParsedKnowledgeUpload(
        title=(title or Path(filename).stem).strip(),
        category=category.strip(),
        target_position=(target_position or "general").strip() or "general",
        content=text,
        metadata={"source": "upload", "filename": filename, "file_type": suffix.lstrip("."), "quality_report": quality_report.to_metadata()},
        original_content=content,
        filename=filename,
        file_type=suffix.lstrip("."),
        file_size=len(content),
    )


def get_document_parser(suffix: str) -> DocumentParser:
    """根据小写文件扩展名返回解析器，不支持的类型抛出 HTTP 400。"""
    parser = PARSERS_BY_SUFFIX.get(suffix)
    if not parser:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .txt, .md and .pdf files are supported",
        )
    return parser


def decode_text_file(content: bytes) -> str:
    """依次尝试常用中英文编码解码文本，全部失败时返回客户端错误。"""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Text file encoding is not supported")


def extract_pdf_text(content: bytes) -> str:
    """使用 pypdf 按页提取 PDF 文本层，并保留页码边界。"""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PDF parsing dependency is missing. Install pypdf to enable PDF uploads.",
        ) from exc

    with TemporaryDirectory() as temp_dir:
        pdf_path = Path(temp_dir) / "upload.pdf"
        pdf_path.write_bytes(content)
        reader = PdfReader(str(pdf_path))
        pages = []
        for index, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text() or ""
            pages.append(f"Page {index}\n{page_text.strip()}")
        return "\n\n".join(pages)


def extract_pdf_text_with_ocr(content: bytes) -> str:
    """将扫描型 PDF 逐页转为图片并通过中英文 OCR 提取文本。"""
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "No selectable text was found in this PDF. Install pdf2image, pytesseract, "
                "Poppler and Tesseract OCR to support scanned PDF uploads."
            ),
        ) from exc

    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        pdf_path = temp_path / "upload.pdf"
        pdf_path.write_bytes(content)
        try:
            images = convert_from_path(str(pdf_path))
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="PDF OCR rendering failed. Check Poppler installation and PDF file validity.",
            ) from exc

        pages = []
        for index, image in enumerate(images, start=1):
            try:
                page_text = pytesseract.image_to_string(image, lang="chi_sim+eng")
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="PDF OCR failed. Check Tesseract installation and language data.",
                ) from exc
            pages.append(f"Page {index}\n{page_text.strip()}")
        return "\n\n".join(pages)


def normalize_extracted_text(text: str) -> str:
    """统一换行、清理行首尾空白，并将连续空行压缩为一个空行。"""
    lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    cleaned_lines: list[str] = []
    blank_seen = False
    for line in lines:
        if not line:
            if not blank_seen and cleaned_lines:
                cleaned_lines.append("")
            blank_seen = True
            continue
        cleaned_lines.append(line)
        blank_seen = False
    return "\n".join(cleaned_lines).strip()

def _page_contents(text: str) -> list[str]:
    """提取 PDF 各页正文，并忽略解析过程中添加的 Page N 页码标题。"""
    pages: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("Page ") and line[5:].strip().isdigit():
            if current is not None:
                pages.append("\n".join(current))
            current = []
        elif current is not None:
            current.append(line)
    if current is not None:
        pages.append("\n".join(current))
    return pages
