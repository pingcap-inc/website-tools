#!/usr/bin/env python3
"""
Generate a Redirection plugin import CSV from a URL list.

Input file rules:
- one URL/path per line
- blank lines and lines starting with # are ignored

Output format:
- CSV columns for Redirection CSV import:
  source,target,regex,code
"""

import argparse
import csv
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_TARGET = "https://www.pingcap.com/"
DEFAULT_OUTPUT = "redirection-import.csv"


def normalize_source(value: str) -> str:
    raw = value.strip()
    if not raw or raw.startswith("#"):
        return ""

    parsed = urlparse(raw)
    path = parsed.path if parsed.scheme or parsed.netloc else raw
    path = path.strip()

    if not path.startswith("/"):
        path = f"/{path}"

    if not path:
        path = "/"

    return path


def read_sources(file_path: Path) -> list[str]:
    seen = set()
    sources = []

    with file_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            source = normalize_source(line)
            if not source or source in seen:
                continue
            seen.add(source)
            sources.append(source)

    return sources


def write_csv(output_path: Path, sources: list[str], target: str) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["source", "target", "regex", "code"],
        )
        writer.writeheader()
        for source in sources:
            writer.writerow(
                {
                    "source": source,
                    "target": target,
                    "regex": 0,
                    "code": 301,
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Redirection plugin CSV from a URL list")
    parser.add_argument("--file", default="urls.txt", help="Input text file containing one path/URL per line")
    parser.add_argument("--target", default=DEFAULT_TARGET, help="Redirect target URL")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output CSV filename")
    args = parser.parse_args()

    input_path = Path(args.file)
    output_path = Path(args.output)

    if not input_path.exists():
        raise SystemExit(f"输入文件不存在: {input_path}")

    sources = read_sources(input_path)
    if not sources:
        raise SystemExit("未读取到可用路径")

    write_csv(output_path, sources, args.target)

    print(f"✅ 已生成 CSV: {output_path}")
    print(f"   Rows: {len(sources)}")
    print(f"   Target: {args.target}")


if __name__ == "__main__":
    main()
