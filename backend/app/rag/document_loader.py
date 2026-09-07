import re
from dataclasses import dataclass, field

from app.schemas.knowledge import KnowledgeDocumentCreate


MAX_CHUNK_CHARS = 1200
TARGET_CHUNK_CHARS = 900
CHUNK_OVERLAP_CHARS = 180

_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
_PAGE_MARKER_RE = re.compile(r"^Page\s+([0-9]+)\s*$")
_HEADING_PATTERNS = (
    _MARKDOWN_HEADING_RE,
    re.compile(r"^\s*(?:[0-9]+[.)]\s+|[A-Z][A-Z0-9 /_-]{2,60}$).+"),
    re.compile(r"^\s*(?:\u7b2c[\u4e00-\u9fff0-9]+[\u7ae0\u8282\u90e8\u5206\u7bc7]|[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+[\u3001.])\s*.+"),
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[\u3002\uff01\uff1f!?\uff1b;.])\s+")


@dataclass
class TextBlock:
    text: str
    block_type: str = "paragraph"
    heading_level: int | None = None
    heading_title: str | None = None
    page_number: int | None = None


@dataclass
class ChunkDraft:
    text: str
    heading_path: list[str] = field(default_factory=list)
    section_title: str | None = None
    source_page: int | None = None


def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS, overlap_chars: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """只返回切块后的文本列表，供不需要 metadata 的场景使用。"""
    return [chunk.text for chunk in chunk_text_with_metadata(text, max_chars=max_chars, overlap_chars=overlap_chars)]


def chunk_text_with_metadata(
    text: str,
    max_chars: int = MAX_CHUNK_CHARS,
    overlap_chars: int = CHUNK_OVERLAP_CHARS,
) -> list[ChunkDraft]:
    """按标题、段落和页码信息切分文档，并保留章节路径等 metadata。"""
    if not text:
        return []

    blocks = _split_structured_blocks(_normalize_text(text))
    chunks: list[ChunkDraft] = []
    current_parts: list[str] = []
    current_len = 0
    heading_path: list[str] = []
    active_heading = ""
    active_page: int | None = None

    def flush_current() -> None:
        """将已积累的段落写入带标题和页码的分块，并保留重叠文本供下一块使用。"""
        nonlocal current_parts, current_len
        if not current_parts:
            return
        chunk_text_value = _join_parts(current_parts)
        if chunk_text_value:
            chunks.append(
                ChunkDraft(
                    text=chunk_text_value,
                    heading_path=list(heading_path),
                    section_title=heading_path[-1] if heading_path else None,
                    source_page=active_page,
                )
            )
        current_parts = _overlap_parts(chunk_text_value, overlap_chars, active_heading)
        current_len = sum(len(part) for part in current_parts)

    for block in blocks:
        if block.block_type == "page":
            if current_parts:
                flush_current()
                current_parts = []
                current_len = 0
            active_page = block.page_number
            continue

        if block.block_type == "heading":
            if current_parts:
                chunk_text_value = _join_parts(current_parts)
                if chunk_text_value:
                    chunks.append(
                        ChunkDraft(
                            text=chunk_text_value,
                            heading_path=list(heading_path),
                            section_title=heading_path[-1] if heading_path else None,
                            source_page=active_page,
                        )
                    )
                current_parts = []
                current_len = 0
            _update_heading_path(heading_path, block.heading_level or 1, block.heading_title or _clean_heading_text(block.text))
            active_heading = block.text
            continue

        block_parts = _split_oversized_block(block.text, max_chars=max_chars, heading=active_heading)
        for part in block_parts:
            part_len = len(part)
            if current_parts and current_len + part_len > max_chars:
                flush_current()

            if active_heading and not current_parts:
                current_parts.append(active_heading)
                current_len += len(active_heading)

            current_parts.append(part)
            current_len += part_len

    if current_parts:
        chunk_text_value = _join_parts(current_parts)
        if chunk_text_value:
            chunks.append(
                ChunkDraft(
                    text=chunk_text_value,
                    heading_path=list(heading_path),
                    section_title=heading_path[-1] if heading_path else None,
                    source_page=active_page,
                )
            )

    return [chunk for chunk in chunks if chunk.text.strip()]


def document_to_chunks(document: KnowledgeDocumentCreate) -> list[dict]:
    """把知识库文档转成可写入 Qdrant 的 chunk payload。"""
    chunks = chunk_text_with_metadata(document.content)
    result: list[dict] = []
    for index, chunk in enumerate(chunks):
        chunk_metadata = dict(document.metadata or {})
        if chunk.section_title:
            chunk_metadata["section_title"] = chunk.section_title
        if chunk.heading_path:
            chunk_metadata["heading_path"] = chunk.heading_path
        if chunk.source_page is not None:
            chunk_metadata["source_page"] = chunk.source_page

        result.append(
            {
                "chunk_index": index,
                "text": chunk.text,
                "title": document.title,
                "category": document.category,
                "target_position": document.target_position,
                "metadata": chunk_metadata,
                "section_title": chunk.section_title,
                "heading_path": chunk.heading_path,
                "source_page": chunk.source_page,
            }
        )
    return result


def _normalize_text(text: str) -> str:
    """统一换行和空白，为后续切块做基础清理。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_structured_blocks(text: str) -> list[TextBlock]:
    """把文本拆成页码标记、标题和普通段落块。"""
    blocks: list[TextBlock] = []
    paragraph_lines: list[str] = []

    def flush_paragraph() -> None:
        """将积累的段落行合并为文本块并清空段落缓冲区。"""
        nonlocal paragraph_lines
        if paragraph_lines:
            blocks.append(TextBlock("\n".join(paragraph_lines).strip()))
            paragraph_lines = []

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            continue

        page_number = _parse_page_marker(line)
        if page_number is not None:
            flush_paragraph()
            blocks.append(TextBlock(text=line, block_type="page", page_number=page_number))
            continue

        heading_info = _parse_heading(line)
        if heading_info:
            flush_paragraph()
            level, title = heading_info
            blocks.append(TextBlock(text=line, block_type="heading", heading_level=level, heading_title=title))
            continue

        paragraph_lines.append(line)

    flush_paragraph()
    return blocks


def _parse_page_marker(text: str) -> int | None:
    """从 PDF 解析产生的 Page N 标记中提取页码。"""
    match = _PAGE_MARKER_RE.match(text)
    if not match:
        return None
    return int(match.group(1))


def _parse_heading(text: str) -> tuple[int, str] | None:
    """识别 Markdown、数字编号、英文大写和中文章节标题。"""
    if len(text) > 100:
        return None
    markdown_match = _MARKDOWN_HEADING_RE.match(text)
    if markdown_match:
        return len(markdown_match.group(1)), markdown_match.group(2).strip()
    for pattern in _HEADING_PATTERNS[1:]:
        if pattern.match(text):
            return 1, _clean_heading_text(text)
    return None


def _is_heading(text: str) -> bool:
    """判断一行文本是否应该被当作章节标题。"""
    return _parse_heading(text) is not None


def _clean_heading_text(text: str) -> str:
    """去掉标题语法符号，返回可读的标题名称。"""
    markdown_match = _MARKDOWN_HEADING_RE.match(text)
    if markdown_match:
        return markdown_match.group(2).strip()
    return text.strip()


def _update_heading_path(path: list[str], level: int, title: str) -> None:
    """根据新标题级别更新当前章节层级路径。"""
    normalized_level = max(1, min(level, 6))
    del path[normalized_level - 1 :]
    path.append(title)


def _split_oversized_block(block: str, max_chars: int, heading: str) -> list[str]:
    """将超过 chunk 大小的段落拆分，优先按句子边界切。"""
    if len(block) <= max_chars:
        return [block]

    sentences = [sentence.strip() for sentence in _SENTENCE_SPLIT_RE.split(block) if sentence.strip()]
    if len(sentences) <= 1:
        return _split_by_soft_length(block, max_chars=max_chars)

    parts: list[str] = []
    current: list[str] = []
    current_len = 0
    prefix_len = len(heading) if heading else 0
    budget = max_chars - prefix_len - 2

    for sentence in sentences:
        if current and current_len + len(sentence) > budget:
            parts.append(" ".join(current).strip())
            current = []
            current_len = 0
        current.append(sentence)
        current_len += len(sentence)

    if current:
        parts.append(" ".join(current).strip())

    return parts


def _split_by_soft_length(text: str, max_chars: int) -> list[str]:
    """当文本没有明显句子边界时，按软长度做兜底切分。"""
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            break_at = max(text.rfind("\n", start, end), text.rfind(" ", start, end), text.rfind(",", start, end))
            if break_at > start + max_chars * 0.5:
                end = break_at + 1
        parts.append(text[start:end].strip())
        start = end
    return [part for part in parts if part]


def _overlap_parts(previous_chunk: str, overlap_chars: int, heading: str) -> list[str]:
    """为下一个 chunk 生成重叠上下文，减少语义断裂。"""
    if overlap_chars <= 0:
        return [heading] if heading else []
    tail = previous_chunk[-overlap_chars:].strip()
    parts: list[str] = []
    if heading:
        parts.append(heading)
    if tail and tail != heading:
        parts.append(tail)
    return parts


def _join_parts(parts: list[str]) -> str:
    """把 chunk 内的非空片段按段落间距拼接起来。"""
    return "\n\n".join(part.strip() for part in parts if part.strip())
