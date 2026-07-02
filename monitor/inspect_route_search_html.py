"""Inspect saved route-search HTML pages without network access."""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

DEFAULT_DEBUG_DIR = Path(__file__).resolve().parent / "debug"
DEFAULT_PATTERN = "04_uni_to_nakahashi_weekday_route_search_page_*_response.html"

KEYWORDS = (
    "金沢工業大学",
    "中橋",
    "系統",
    "方面",
    "行",
    "発",
    "着",
    "次の件",
    "／",
)
TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
BLOCK_TAG_RE = re.compile(r"</?(?:br|p|div|tr|td|th|table|select|option|li|h[1-6]|form)\b[^>]*>", re.I)
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE = re.compile(r"<(?:script|style)\b.*?</(?:script|style)>", re.I | re.S)
INPUT_VALUE_RE = re.compile(r"<input\b[^>]*\bvalue=(['\"])(.*?)\1[^>]*>", re.I | re.S)


def html_to_text_lines(content: str) -> list[str]:
    content = SCRIPT_STYLE_RE.sub("\n", content)
    content = INPUT_VALUE_RE.sub(lambda m: f" {m.group(2)} ", content)
    content = BLOCK_TAG_RE.sub("\n", content)
    content = TAG_RE.sub(" ", content)
    content = html.unescape(content)

    lines: list[str] = []
    for raw_line in content.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return lines


def is_interesting(line: str) -> bool:
    return any(keyword in line for keyword in KEYWORDS) or TIME_RE.search(line) is not None


def merged_context_ranges(matches: list[int], line_count: int, context: int) -> list[range]:
    ranges: list[range] = []
    for index in matches:
        start = max(0, index - context)
        end = min(line_count, index + context + 1)
        if ranges and start <= ranges[-1].stop:
            ranges[-1] = range(ranges[-1].start, max(ranges[-1].stop, end))
        else:
            ranges.append(range(start, end))
    return ranges


def inspect_file(path: Path, context: int, dump_text: bool) -> None:
    content = path.read_text(encoding="utf-8", errors="replace")
    lines = html_to_text_lines(content)
    matches = [i for i, line in enumerate(lines) if is_interesting(line)]

    print(f"\n## {path.name}")
    print(f"lines={len(lines)} matches={len(matches)}")

    if dump_text:
        text_path = path.with_name(path.stem + ".text.txt")
        text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"dumped {text_path.as_posix()}")

    if not matches:
        print("(no matching lines)")
        return

    for block_index, line_range in enumerate(merged_context_ranges(matches, len(lines), context), start=1):
        if block_index > 1:
            print("--")
        for line_index in line_range:
            marker = ">" if line_index in matches else " "
            print(f"{marker} {line_index + 1:04d}: {lines[line_index]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect saved route-search HTML structure.")
    parser.add_argument("--debug-dir", type=Path, default=DEFAULT_DEBUG_DIR)
    parser.add_argument("--pattern", default=DEFAULT_PATTERN)
    parser.add_argument("--context", type=int, default=3)
    parser.add_argument("--dump-text", action="store_true")
    args = parser.parse_args()

    if args.context < 0:
        parser.error("--context must be 0 or greater")

    html_files = sorted(path for path in args.debug_dir.glob(args.pattern) if path.is_file())
    if not html_files:
        print(f"No HTML files matched {args.pattern!r} under {args.debug_dir}", file=sys.stderr)
        return 1

    for html_file in html_files:
        inspect_file(html_file, args.context, args.dump_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())