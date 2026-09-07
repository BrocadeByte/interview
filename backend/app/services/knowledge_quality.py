from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass


MOJIBAKE_MARKERS = (
    "\u00ef\u00bf\u00bd",  # UTF-8 替换字符的字节被误按 Latin-1 解码产生的乱码。
    "\u951f\u65a4\u62f7",  # 中文替换字符常见的乱码形式。
    "\u93c2\u56e8",  # UTF-8 中文被旧代码页错误解码产生的乱码。
    "\u6d93\ue15f",
    "\u93b4\u621c",
    "\u6d60\u8bd9",
    "\u70eb\u70eb\u70eb",
    "\u5c6f\u5c6f\u5c6f",
    "\ufffd",
    "\u00c3",
    "\u00c2",
)
MAX_MOJIBAKE_RATIO = 0.015
MAX_ABNORMAL_CHAR_RATIO = 0.08
MAX_EMPTY_PAGE_RATIO = 0.25
MAX_DUPLICATE_BLOCK_RATIO = 0.45
MIN_DUPLICATE_BLOCKS = 4
_PAGE_PATTERN = re.compile(r"(?m)^Page\s+(\d+)\s*$")


@dataclass(frozen=True)
class KnowledgeQualityReport:
    character_count: int
    mojibake_ratio: float
    abnormal_character_ratio: float
    page_count: int
    empty_page_count: int
    empty_page_ratio: float
    duplicate_block_ratio: float
    duplicate_block_count: int
    rejection_reasons: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        """根据是否存在拒绝原因判断文本质量检查是否通过。"""
        return not self.rejection_reasons

    def to_metadata(self) -> dict:
        """将质量报告转为可序列化的元数据，补充通过标记并转换拒绝原因列表。"""
        data = asdict(self)
        data["accepted"] = self.accepted
        data["rejection_reasons"] = list(self.rejection_reasons)
        return data


class KnowledgeQualityError(ValueError):
    def __init__(self, report: KnowledgeQualityReport) -> None:
        """保存完整质量报告，并以拒绝原因构造异常信息。"""
        self.report = report
        super().__init__("; ".join(report.rejection_reasons) or "Knowledge content failed quality checks")


def analyze_knowledge_text(text: str) -> KnowledgeQualityReport:
    """统计可见字符、乱码、异常字符、空页及重复内容，依据阈值生成质量报告。"""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    visible = [char for char in normalized if not char.isspace()]
    character_count = len(visible)

    mojibake_count = sum(normalized.count(marker) * len(marker) for marker in MOJIBAKE_MARKERS)
    abnormal_count = sum(1 for char in visible if _is_abnormal_character(char))
    mojibake_ratio = min(1.0, _ratio(mojibake_count, character_count))
    abnormal_ratio = _ratio(abnormal_count, character_count)

    pages = split_pages(normalized)
    empty_pages = sum(1 for page in pages if not page.strip())
    empty_page_ratio = _ratio(empty_pages, len(pages))

    blocks = _content_blocks(normalized)
    counts = Counter(blocks)
    duplicate_block_count = sum(count - 1 for count in counts.values() if count > 1)
    duplicate_ratio = _ratio(duplicate_block_count, len(blocks))

    reasons: list[str] = []
    if character_count == 0:
        reasons.append("content is empty")
    if mojibake_ratio > MAX_MOJIBAKE_RATIO:
        reasons.append(f"mojibake ratio {mojibake_ratio:.3f} exceeds {MAX_MOJIBAKE_RATIO:.3f}")
    if abnormal_ratio > MAX_ABNORMAL_CHAR_RATIO:
        reasons.append(f"abnormal character ratio {abnormal_ratio:.3f} exceeds {MAX_ABNORMAL_CHAR_RATIO:.3f}")
    if pages and empty_page_ratio > MAX_EMPTY_PAGE_RATIO:
        reasons.append(f"empty page ratio {empty_page_ratio:.3f} exceeds {MAX_EMPTY_PAGE_RATIO:.3f}")
    if len(blocks) >= MIN_DUPLICATE_BLOCKS and duplicate_ratio > MAX_DUPLICATE_BLOCK_RATIO:
        reasons.append(f"duplicate block ratio {duplicate_ratio:.3f} exceeds {MAX_DUPLICATE_BLOCK_RATIO:.3f}")

    return KnowledgeQualityReport(
        character_count=character_count,
        mojibake_ratio=round(mojibake_ratio, 6),
        abnormal_character_ratio=round(abnormal_ratio, 6),
        page_count=len(pages),
        empty_page_count=empty_pages,
        empty_page_ratio=round(empty_page_ratio, 6),
        duplicate_block_ratio=round(duplicate_ratio, 6),
        duplicate_block_count=duplicate_block_count,
        rejection_reasons=tuple(reasons),
    )


def validate_knowledge_text(text: str) -> KnowledgeQualityReport:
    """分析知识文本质量，未通过检查时抛出携带报告的异常。"""
    report = analyze_knowledge_text(text)
    if not report.accepted:
        raise KnowledgeQualityError(report)
    return report


def split_pages(text: str) -> list[str]:
    """按解析文本中的页码标记切分页面正文，无页码标记时返回空列表。"""
    matches = list(_PAGE_PATTERN.finditer(text))
    if not matches:
        return []
    pages: list[str] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        pages.append(text[start:end].strip())
    return pages


def _content_blocks(text: str) -> list[str]:
    """按段落或页码提取并规范化可比较的内容块，数量不足时改用较长文本行。"""
    blocks = []
    for raw in re.split(r"\n\s*\n|^Page\s+\d+\s*$", text, flags=re.MULTILINE):
        normalized = " ".join(raw.split()).casefold()
        if len(normalized) >= 12:
            blocks.append(normalized)
    if len(blocks) < MIN_DUPLICATE_BLOCKS:
        blocks = [" ".join(line.split()).casefold() for line in text.splitlines() if len(" ".join(line.split())) >= 12]
    return blocks


def _is_abnormal_character(char: str) -> bool:
    """判断字符是否为异常控制符、代理字符、私用字符、未分配字符或替换字符。"""
    if char in {"\n", "\r", "\t"}:
        return False
    category = unicodedata.category(char)
    if category in {"Cc", "Cs", "Co", "Cn"}:
        return True
    return char == "\ufffd"


def _ratio(numerator: int, denominator: int) -> float:
    """计算占比，分母为零时返回零。"""
    return numerator / denominator if denominator else 0.0
