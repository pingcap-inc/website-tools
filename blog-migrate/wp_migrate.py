#!/usr/bin/env python3
"""
WordPress blog migration: English site → Japanese site.
- Migrates posts published in the current (or a specified) month.
- Detects EN CDN images, re-downloads them, and uploads them to the JP WP media library.
- A JP-side plugin (e.g. WP Offload Media) pushes the new images to CDN.
- Rewrites every image URL in the post body to the new JP URL.
- Posts are saved as drafts.
"""

import requests
import base64
import json
import re
import os
import mimetypes
from datetime import datetime, timezone
from urllib.parse import urlparse
from typing import Optional, Tuple

import argparse

# ============================================================
# Configuration (edit to match your environment)
# ============================================================

# English site
EN_SITE_URL     = "https://www.pingcap.com"
EN_USERNAME     = os.environ.get("EN_USERNAME", "")
EN_APP_PASSWORD = os.environ.get("EN_APP_PASSWORD", "")

# EN CDN hostnames used to detect migratable images in post bodies
EN_CDN_DOMAINS = [
    "static.pingcap.com",
]

# Japanese WordPress site
JP_SITE_URL     = "https://pingcap.co.jp"
JP_USERNAME     = os.environ.get("JP_USERNAME", "")
JP_APP_PASSWORD = os.environ.get("JP_APP_PASSWORD", "")

# Target month. None means the current month (UTC). Format: "YYYY-MM".
TARGET_MONTH = None

# ============================================================
# Utility functions
# ============================================================

def get_auth_header():
    token = base64.b64encode(f"{JP_USERNAME}:{JP_APP_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}

def get_en_auth_header():
    if EN_USERNAME and EN_APP_PASSWORD:
        token = base64.b64encode(f"{EN_USERNAME}:{EN_APP_PASSWORD}".encode()).decode()
        return {"Authorization": f"Basic {token}"}
    return {}

def get_target_month():
    if TARGET_MONTH:
        return TARGET_MONTH
    return datetime.now(timezone.utc).strftime("%Y-%m")


def month_bounds(month: str) -> Tuple[str, str]:
    """Return (after, before) ISO timestamps for the given YYYY-MM."""
    year, mon = int(month[:4]), int(month[5:7])
    after = f"{year:04d}-{mon:02d}-01T00:00:00"
    if mon == 12:
        before = f"{year + 1:04d}-01-01T00:00:00"
    else:
        before = f"{year:04d}-{mon + 1:02d}-01T00:00:00"
    return after, before

# ============================================================
# Author / category / tag mapping (cached to avoid duplicate requests)
# ============================================================

_jp_user_cache = {}   # username -> user_id
_jp_cat_cache  = {}   # name -> cat_id
_jp_tag_cache  = {}   # name -> tag_id

def get_jp_user_id(username: str) -> Optional[int]:
    """Look up a JP user ID by username."""
    if username in _jp_user_cache:
        return _jp_user_cache[username]
    try:
        resp = requests.get(
            f"{JP_SITE_URL}/wp-json/wp/v2/users",
            params={"search": username, "per_page": 5},
            headers=get_auth_header(),
            timeout=15,
        )
        resp.raise_for_status()
        for user in resp.json():
            if user.get("name") == username or user.get("slug") == username:
                _jp_user_cache[username] = user["id"]
                return user["id"]
    except Exception as e:
        print(f"  ⚠️ user lookup failed for {username}: {e}")
    _jp_user_cache[username] = None
    return None

def get_or_create_jp_term(name: str, taxonomy: str, cache: dict) -> Optional[int]:
    """Find a JP category/tag by name, creating it if missing."""
    if name in cache:
        return cache[name]
    endpoint = "categories" if taxonomy == "category" else "tags"
    # search first
    try:
        resp = requests.get(
            f"{JP_SITE_URL}/wp-json/wp/v2/{endpoint}",
            params={"search": name, "per_page": 10},
            headers=get_auth_header(),
            timeout=15,
        )
        resp.raise_for_status()
        for term in resp.json():
            if term["name"] == name:
                cache[name] = term["id"]
                return term["id"]
    except Exception as e:
        print(f"  ⚠️ {taxonomy} lookup failed for {name}: {e}")
        return None
    # create if not found
    try:
        resp = requests.post(
            f"{JP_SITE_URL}/wp-json/wp/v2/{endpoint}",
            headers={**get_auth_header(), "Content-Type": "application/json"},
            data=json.dumps({"name": name}),
            timeout=15,
        )
        resp.raise_for_status()
        term_id = resp.json()["id"]
        cache[name] = term_id
        print(f"  ➕ created {taxonomy}: {name} (id={term_id})")
        return term_id
    except Exception as e:
        print(f"  ⚠️ failed to create {taxonomy} {name}: {e}")
        return None

def get_en_term_names(post_id: int, taxonomy: str) -> list:
    """Return the category or tag names for a post on the EN site."""
    endpoint = "categories" if taxonomy == "category" else "tags"
    try:
        resp = requests.get(
            f"{EN_SITE_URL}/wp-json/wp/v2/{endpoint}",
            params={"post": post_id, "per_page": 50, "_fields": "name"},
            timeout=15,
        )
        resp.raise_for_status()
        return [t["name"] for t in resp.json()]
    except Exception:
        return []


# ============================================================
# Fetch posts from EN site
# ============================================================

def fetch_en_posts(month: str) -> list:
    """Fetch all published posts from the EN site for the given month."""
    after, before = month_bounds(month)
    posts, page = [], 1
    print(f"\n📥 fetching EN posts for {month}...")

    while True:
        params = {
                "after":    after,
                "before":   before,
                "per_page": 20,
                "page":     page,
                "status":   "publish",
                "_fields":  "id,title,content,excerpt,slug,date,featured_media,author,author_info,categories,tags,yoast_head_json",
            }
        if EN_USERNAME and EN_APP_PASSWORD:
            params["context"] = "edit"  # request raw content when authenticated
        resp = requests.get(
            f"{EN_SITE_URL}/wp-json/wp/v2/posts",
            params=params,
            headers=get_en_auth_header(),
            timeout=30,
        )
        if resp.status_code == 400:
            break
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        posts.extend(batch)
        print(f"  page {page}: {len(batch)} posts")
        page += 1

    print(f"✅ fetched {len(posts)} posts in total")
    return posts

def fetch_en_featured_image_url(media_id: int) -> Optional[str]:
    """Return the source URL of an EN featured image."""
    try:
        resp = requests.get(
            f"{EN_SITE_URL}/wp-json/wp/v2/media/{media_id}",
            params={"_fields": "source_url"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("source_url")
    except Exception as e:
        print(f"  ⚠️ failed to fetch featured image media_id={media_id}: {e}")
        return None

# ============================================================
# Image handling: download → upload to JP WP media library
# ============================================================

def download_image(url: str) -> Tuple:
    """Download an image. Returns (bytes, mime_type) or (None, None)."""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        mime = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        return resp.content, mime
    except Exception as e:
        print(f"  ⚠️ download failed {url}: {e}")
        return None, None

def upload_to_wp_media(image_bytes: bytes, mime_type: str, filename: str) -> Tuple:
    """
    Upload an image to the JP WP media library.
    Returns (media_id, source_url); a JP-side plugin syncs to CDN automatically.
    """
    # fix extension
    ext = mimetypes.guess_extension(mime_type) or ".jpg"
    ext = ext.replace(".jpe", ".jpg")
    if not any(filename.lower().endswith(e) for e in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"]):
        filename += ext

    headers = get_auth_header()
    headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    headers["Content-Type"] = mime_type

    try:
        resp = requests.post(
            f"{JP_SITE_URL}/wp-json/wp/v2/media",
            headers=headers,
            data=image_bytes,
            timeout=60,
        )
        resp.raise_for_status()
        media = resp.json()
        # source_url is already the JP CDN URL (handled by WP Offload Media or similar)
        return media["id"], media["source_url"]
    except Exception as e:
        print(f"  ⚠️ upload failed {filename}: {e}")
        return None, None

# ============================================================
# Scan and rewrite EN CDN images in the post body
# ============================================================

def build_cdn_pattern() -> re.Pattern:
    """Build a regex that matches images on any configured EN CDN host."""
    domain_alts = "|".join(re.escape(d) for d in EN_CDN_DOMAINS)
    return re.compile(
        rf'(https?://(?:{domain_alts})[^\s"\'<>]*\.(?:jpg|jpeg|png|gif|webp|svg)(?:\?[^\s"\'<>]*)?)',
        re.IGNORECASE,
    )

def migrate_content_images(content: str) -> str:
    """Scan the body for CDN images, download them, upload to JP media, and rewrite URLs."""
    pattern = build_cdn_pattern()
    found_urls = list(set(pattern.findall(content)))

    if not found_urls:
        return content

    print(f"  🖼️  found {len(found_urls)} body images, uploading...")
    url_map = {}

    for url in found_urls:
        filename = os.path.basename(urlparse(url).path).split("?")[0] or "image.jpg"
        img_bytes, mime_type = download_image(url)
        if img_bytes:
            _, new_url = upload_to_wp_media(img_bytes, mime_type, filename)
            if new_url:
                url_map[url] = new_url
                print(f"    ✅ {filename} → {new_url}")

    # replace all old URLs in the body
    for old_url, new_url in url_map.items():
        content = content.replace(old_url, new_url)

    return content

# ============================================================
# Migrate a single post
# ============================================================

def jp_post_exists(slug: str) -> bool:
    """Check whether a post with the given slug already exists on the JP site."""
    try:
        resp = requests.get(
            f"{JP_SITE_URL}/wp-json/wp/v2/posts",
            params={"slug": slug, "status": "any", "per_page": 1},
            headers=get_auth_header(),
            timeout=15,
        )
        return resp.ok and len(resp.json()) > 0
    except Exception:
        return False


def migrate_post(post: dict) -> bool:
    title = post["title"]["rendered"]
    print(f"\n📝 migrating: {title}")

    if jp_post_exists(post["slug"]):
        print(f"  ⏭️  skipped (already exists on JP): {title}")
        return "skipped"

    # 1. Body images: download → upload to JP → rewrite URLs
    # When authenticated against EN use raw (preserves Block format); otherwise use rendered
    raw_content = post["content"].get("raw", "")
    html_content = raw_content if raw_content else post["content"]["rendered"]
    content = migrate_content_images(html_content)

    # 2. Featured image: download → upload to JP media library
    featured_media_id = None
    if post.get("featured_media"):
        en_img_url = fetch_en_featured_image_url(post["featured_media"])
        if en_img_url:
            img_bytes, mime_type = download_image(en_img_url)
            if img_bytes:
                filename = os.path.basename(urlparse(en_img_url).path).split("?")[0] or "featured.jpg"
                featured_media_id, cdn_url = upload_to_wp_media(img_bytes, mime_type, filename)
                if featured_media_id:
                    print(f"  🖼️  featured image uploaded → {cdn_url}")

    # 3. Map author
    author_id = None
    if post.get("author"):
        author_info = post.get("author_info", {})
        en_display_name = author_info.get("display_name") if isinstance(author_info, dict) else None
        if en_display_name:
            author_id = get_jp_user_id(en_display_name)
            if author_id:
                print(f"  👤 author mapped: {en_display_name} → id={author_id}")
            else:
                print(f"  ⚠️ JP user not found: {en_display_name}; falling back to the default author")

    # 4. Map categories and tags
    cat_names = get_en_term_names(post["id"], "category")
    tag_names = get_en_term_names(post["id"], "tag")

    jp_cat_ids = [i for i in [get_or_create_jp_term(n, "category", _jp_cat_cache) for n in cat_names] if i]
    jp_tag_ids = [i for i in [get_or_create_jp_term(n, "tag", _jp_tag_cache) for n in tag_names] if i]

    if cat_names:
        print(f"  🗂️  categories: {cat_names}")
    if tag_names:
        print(f"  🏷️  tags: {tag_names}")

    # 5. Create draft
    post_data = {
        "title":   title,
        "content": content,
        "excerpt": post["excerpt"]["rendered"],
        "slug":    post["slug"],
        "status":  "draft",
        "date":    post["date"],
    }
    if featured_media_id:
        post_data["featured_media"] = featured_media_id
    if author_id:
        post_data["author"] = author_id
    if jp_cat_ids:
        post_data["categories"] = jp_cat_ids
    if jp_tag_ids:
        post_data["tags"] = jp_tag_ids

    try:
        resp = requests.post(
            f"{JP_SITE_URL}/wp-json/wp/v2/posts",
            headers={**get_auth_header(), "Content-Type": "application/json"},
            data=json.dumps(post_data),
            timeout=30,
        )
        resp.raise_for_status()
        new_id = resp.json()["id"]
        print(f"  ✅ draft created: {JP_SITE_URL}/?p={new_id}")

        # Sync Yoast SEO
        yoast = post.get("yoast_head_json", {})
        if yoast:
            jp_slug = post["slug"]
            jp_canonical = f"{JP_SITE_URL}/blog/{jp_slug}/"
            seo_meta = {
                "_yoast_wpseo_title":        yoast.get("og_title") or yoast.get("title", ""),
                "_yoast_wpseo_metadesc":     yoast.get("og_description") or yoast.get("description", ""),
                "_yoast_wpseo_canonical":    jp_canonical,
                "_yoast_wpseo_opengraph-title":       yoast.get("og_title", ""),
                "_yoast_wpseo_opengraph-description": yoast.get("og_description", ""),
            }
            seo_resp = requests.post(
                f"{JP_SITE_URL}/wp-json/wp/v2/posts/{new_id}",
                headers={**get_auth_header(), "Content-Type": "application/json"},
                data=json.dumps({"meta": seo_meta}),
                timeout=30,
            )
            if seo_resp.ok:
                print(f"  🔍 SEO synced: {yoast.get('title', '')[:50]}")
            else:
                print(f"  ⚠️ SEO sync failed: {seo_resp.text[:100]}")

        return "migrated"
    except Exception as e:
        print(f"  ❌ publish failed: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"     response: {e.response.text[:300]}")
        return "failed"

# ============================================================
# Main entry
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Migrate WP blog posts from the EN site to the JP site")
    parser.add_argument("--month", help="Target month, format YYYY-MM (defaults to current month)")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of posts to migrate (0 = no limit)")
    parser.add_argument("--dry-run", action="store_true", help="List posts without migrating")
    args = parser.parse_args()

    if not EN_USERNAME or not EN_APP_PASSWORD:
        print("⚠️  EN auth not configured; falling back to rendered HTML (no Block format)")

    month = args.month or get_target_month()
    print(f"🚀 starting migration for {month}")
    print(f"   EN site:    {EN_SITE_URL}")
    print(f"   EN CDN:     {EN_CDN_DOMAINS}")
    print(f"   JP site:    {JP_SITE_URL}")
    if args.limit:
        print(f"   limit:      {args.limit} (test mode)")
    if args.dry_run:
        print(f"   mode:       dry-run (list only)")

    posts = fetch_en_posts(month)
    if not posts:
        print("⚠️ no posts found, exiting.")
        return

    if args.limit:
        posts = posts[:args.limit]

    if args.dry_run:
        print(f"\n📋 posts found (total {len(posts)}):")
        for i, post in enumerate(posts, 1):
            print(f"  {i}. [{post['date'][:10]}] {post['title']['rendered']}")
        print("\n✅ dry-run complete; remove --dry-run to start the actual migration.")
        return

    migrated, skipped, failed_list = [], [], []
    for post in posts:
        title = post["title"]["rendered"]
        result = migrate_post(post)
        if result == "migrated":
            migrated.append(title)
        elif result == "skipped":
            skipped.append(title)
        else:
            failed_list.append(title)

    print(f"\n{'='*50}")
    print(f"🎉 migration complete: {len(migrated)} migrated / {len(skipped)} skipped / {len(failed_list)} failed")
    print(f"   drafts:     {JP_SITE_URL}/cms-dashboard/edit.php?post_status=draft")

    summary = {
        "month": month,
        "migrated": migrated,
        "skipped": skipped,
        "failed": failed_list,
    }
    summary_json = json.dumps(summary, ensure_ascii=False)
    print(f"\n__SUMMARY_JSON__\n{summary_json}")

if __name__ == "__main__":
    main()
