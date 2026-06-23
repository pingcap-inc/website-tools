#!/usr/bin/env python3
"""
Move WordPress posts to trash in batch by URL.

Supported inputs:
- One or more URLs passed via repeated --url
- A text file passed via --file (one URL per line; blank lines and # comments ignored)

The script resolves each URL to a post ID and sends a REST API delete request
with force=false, which moves the post to the WordPress trash instead of
permanently deleting it.
"""

import argparse
import base64
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import parse_qs, urlparse

import requests


# ============================================================
# Configuration (edit to match your environment)
# ============================================================

SITE_URL = "https://www.pingcap.com"  # WordPress site base URL (no trailing slash)
USERNAME = ""       # WP username
APP_PASSWORD = ""   # WP Application Password


# ============================================================
# Models / helpers
# ============================================================

ACTIVE_STATUSES = ["publish", "draft", "future", "pending", "private"]
DEFAULT_ENDPOINTS = ["posts", "articles", "article", "pages"]


@dataclass
class ResolvedPost:
    post_id: int
    endpoint: str
    slug: str
    status: str
    title: str
    link: str


def get_auth_header() -> dict:
    token = base64.b64encode(f"{USERNAME}:{APP_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def normalize_site_url(site_url: str) -> str:
    return site_url.rstrip("/")


def extract_post_id_from_url(url: str) -> Optional[int]:
    parsed = urlparse(url)
    post_id = parse_qs(parsed.query).get("p", [None])[0]
    if post_id and post_id.isdigit():
        return int(post_id)
    return None


def extract_slug_from_url(url: str) -> Optional[str]:
    parsed = urlparse(url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        return None
    return segments[-1]


def extract_path_from_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme or parsed.netloc:
        return parsed.path or "/"
    return url.strip() or "/"


def infer_endpoint_candidates(url: str, extra_endpoints: Optional[list[str]] = None) -> list[str]:
    path = extract_path_from_url(url).strip("/")
    first_segment = path.split("/", 1)[0] if path else ""

    prioritized = []
    if first_segment == "blog":
        prioritized = ["posts"]
    elif first_segment == "article":
        prioritized = ["articles", "article", "posts"]
    elif first_segment:
        prioritized = [first_segment, f"{first_segment}s", "posts"]

    ordered = []
    for endpoint in (extra_endpoints or []) + prioritized + DEFAULT_ENDPOINTS:
        if endpoint not in ordered:
            ordered.append(endpoint)
    return ordered


def read_urls(args_urls: list[str], file_path: Optional[str]) -> list[str]:
    urls = list(args_urls)
    if file_path:
        with open(file_path, "r", encoding="utf-8") as handle:
            for line in handle:
                cleaned = line.strip()
                if cleaned and not cleaned.startswith("#"):
                    urls.append(cleaned)
    deduped = []
    seen = set()
    for url in urls:
        normalized = url.strip()
        if normalized and normalized not in seen:
            deduped.append(normalized)
            seen.add(normalized)
    return deduped


def fetch_post_by_id(site_url: str, post_id: int, endpoint: str) -> Optional[ResolvedPost]:
    try:
        response = requests.get(
            f"{site_url}/wp-json/wp/v2/{endpoint}/{post_id}",
            params={"context": "edit"},
            headers=get_auth_header(),
            timeout=20,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        post = response.json()
        return ResolvedPost(
            post_id=post["id"],
            endpoint=endpoint,
            slug=post["slug"],
            status=post["status"],
            title=post["title"]["rendered"],
            link=post.get("link", ""),
        )
    except Exception as exc:
        print(f"  ⚠️ 按 ID 查找失败 post_id={post_id} endpoint={endpoint}: {exc}")
        return None


def fetch_post_by_slug(site_url: str, slug: str, endpoint: str) -> Optional[ResolvedPost]:
    for status in ACTIVE_STATUSES:
        try:
            response = requests.get(
                f"{site_url}/wp-json/wp/v2/{endpoint}",
                params={
                    "slug": slug,
                    "status": status,
                    "context": "edit",
                    "per_page": 20,
                    "_fields": "id,slug,status,title,link",
                },
                headers=get_auth_header(),
                timeout=20,
            )
            response.raise_for_status()
            posts = response.json()
            if not posts:
                continue
            post = posts[0]
            return ResolvedPost(
                post_id=post["id"],
                endpoint=endpoint,
                slug=post["slug"],
                status=post["status"],
                title=post["title"]["rendered"],
                link=post.get("link", ""),
            )
        except Exception as exc:
            if getattr(exc, "response", None) is not None and exc.response.status_code == 404:
                break
            print(f"  ⚠️ 按 slug 查找失败 slug={slug} endpoint={endpoint} status={status}: {exc}")
            return None
    return None


def resolve_post(site_url: str, url: str, extra_endpoints: Optional[list[str]] = None) -> Optional[ResolvedPost]:
    post_id = extract_post_id_from_url(url)
    slug = extract_slug_from_url(url)
    if not slug:
        print(f"  ⚠️ 无法从 URL 解析 slug: {url}")
        return None

    for endpoint in infer_endpoint_candidates(url, extra_endpoints):
        if post_id:
            post = fetch_post_by_id(site_url, post_id, endpoint)
        else:
            post = fetch_post_by_slug(site_url, slug, endpoint)
        if post:
            return post

    return None


def trash_post(site_url: str, endpoint: str, post_id: int) -> bool:
    try:
        response = requests.delete(
            f"{site_url}/wp-json/wp/v2/{endpoint}/{post_id}",
            params={"force": "false"},
            headers=get_auth_header(),
            timeout=20,
        )
        response.raise_for_status()
        return True
    except Exception as exc:
        print(f"  ❌ 移到垃圾桶失败 post_id={post_id} endpoint={endpoint}: {exc}")
        if hasattr(exc, "response") and exc.response is not None:
            print(f"     response: {exc.response.text[:300]}")
        return False


def iter_resolution(
    site_url: str,
    urls: Iterable[str],
    extra_endpoints: Optional[list[str]] = None,
) -> Iterable[tuple[str, Optional[ResolvedPost]]]:
    for url in urls:
        yield url, resolve_post(site_url, url, extra_endpoints)


def write_failed_urls(file_path: str, urls: list[str]) -> None:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for url in urls:
            handle.write(f"{url}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Move WordPress posts to trash by URL")
    parser.add_argument("--url", action="append", default=[], help="Post URL; repeat the flag for multiple URLs")
    parser.add_argument("--file", help="Text file containing URLs, one per line")
    parser.add_argument(
        "--post-type",
        action="append",
        default=[],
        help="Custom WP REST endpoint to try first, e.g. articles or article",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Seconds to sleep between requests during deletion, e.g. 0.2",
    )
    parser.add_argument(
        "--failed-file",
        default="failed-urls.txt",
        help="Write unresolved or failed URLs to this file",
    )
    parser.add_argument("--dry-run", action="store_true", help="Resolve posts only; do not move anything to trash")
    parser.add_argument("--site-url", default=SITE_URL, help="WordPress site base URL")
    args = parser.parse_args()

    if not USERNAME or not APP_PASSWORD:
        raise SystemExit("请先在脚本顶部配置 USERNAME 和 APP_PASSWORD")

    site_url = normalize_site_url(args.site_url)
    urls = read_urls(args.url, args.file)
    if not urls:
        raise SystemExit("请通过 --url 或 --file 提供至少一个文章 URL")

    print(f"🗑️  准备处理 {len(urls)} 个 URL")
    print(f"   Site: {site_url}")
    if args.post_type:
        print(f"   Preferred endpoints: {args.post_type}")
    if args.delay > 0:
        print(f"   Delay: {args.delay:.2f}s")
    print(f"   Failed file: {args.failed_file}")
    if args.dry_run:
        print("   Mode: dry-run")

    resolved = []
    unresolved_urls = []

    for index, (url, post) in enumerate(iter_resolution(site_url, urls, args.post_type), 1):
        print(f"\n[{index}/{len(urls)}] {url}")
        if not post:
            unresolved_urls.append(url)
            print("  ⚠️ 未找到对应文章")
            continue
        resolved.append((url, post))
        print(f"  ✅ 命中文章: id={post.post_id}, endpoint={post.endpoint}, status={post.status}, slug={post.slug}")
        print(f"     title: {post.title}")

    if args.dry_run:
        if unresolved_urls:
            write_failed_urls(args.failed_file, unresolved_urls)
            print(f"   未找到 URL 已写入: {args.failed_file}")
        print(f"\n✅ dry-run 完成：找到 {len(resolved)} 篇，未找到 {len(unresolved_urls)} 篇")
        return

    success = 0
    failed_urls = list(unresolved_urls)

    for index, (url, post) in enumerate(resolved, 1):
        print(f"\n🗑️  移到垃圾桶: {post.title}")
        print(f"   Progress: {index}/{len(resolved)}")
        print(f"   Source URL: {url}")
        if trash_post(site_url, post.endpoint, post.post_id):
            success += 1
            print(f"  ✅ 已移到垃圾桶: id={post.post_id}, endpoint={post.endpoint}")
        else:
            failed_urls.append(url)
        if args.delay > 0 and index < len(resolved):
            time.sleep(args.delay)

    if failed_urls:
        write_failed_urls(args.failed_file, failed_urls)
        print(f"   失败或未找到 URL 已写入: {args.failed_file}")

    print(f"\n{'=' * 50}")
    print(f"🎉 处理完成：{success} 成功 / {len(failed_urls) - len(unresolved_urls)} 删除失败 / {len(unresolved_urls)} 未找到")
    print(f"   Trash: {site_url}/cms-dashboard/edit.php?post_status=trash&post_type=post")


if __name__ == "__main__":
    main()
