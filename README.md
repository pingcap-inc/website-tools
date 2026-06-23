# website-tools

PingCAP website operations toolkit.

## Tools

| Tool | Description |
|------|-------------|
| [blog-migrate](./blog-migrate/) | Migrate blog posts from the English site to the Japanese site |
| [content-ops](./content-ops/) | Content operation tools such as batch trash by URL |

---

## blog-migrate

Migrate posts from `pingcap.com` (English) to `pingcap.co.jp` (Japanese) by month. Features:

- Preserves Block editor format (requires authentication on the English site)
- Automatically downloads images and uploads them to the Japanese site's media library (CDN sync included)
- Maps authors by Display name
- Matches categories and tags by name; creates them automatically if missing
- Syncs Yoast SEO data (requires `functions.php` configuration on the Japanese site)
- Saves posts as drafts for manual review before publishing

### Requirements

- Python 3.10+
- WordPress Application Password for both the English and Japanese sites

### Install

```bash
cd blog-migrate
pip3 install requests
```

### Configuration

Edit the configuration block at the top of `wp_migrate.py`:

```python
# English site
EN_USERNAME     = "your_en_username"
EN_APP_PASSWORD = "xxxx xxxx xxxx xxxx xxxx xxxx"

# Japanese site
JP_USERNAME     = "your_jp_username"
JP_APP_PASSWORD = "xxxx xxxx xxxx xxxx xxxx xxxx"
```

To generate an Application Password: WP Admin → Users → Your Profile → bottom of the page "Application Passwords" → enter a name → Add New → copy the value.

### Usage

```bash
# List the current month's posts (no migration)
python3 wp_migrate.py --dry-run

# List posts for a specific month
python3 wp_migrate.py --month 2026-05 --dry-run

# Migrate 1 post as a test
python3 wp_migrate.py --month 2026-05 --limit 1

# Migrate all posts for the current month
python3 wp_migrate.py

# Migrate all posts for a specific month
python3 wp_migrate.py --month 2026-05
```

After migration, review the drafts in the Japanese site admin: `/cms-dashboard/edit.php?post_status=draft`

## content-ops

WordPress content operation tools for batch actions across blog/article content.

### Batch move posts to trash by URL

Edit the configuration block at the top of `content-ops/wp_trash_posts.py`:

```python
SITE_URL = "https://www.pingcap.com"
USERNAME = "your_wp_username"
APP_PASSWORD = "xxxx xxxx xxxx xxxx xxxx xxxx"
```

Then run:

```bash
cd content-ops

# Preview only
python3 wp_trash_posts.py --url https://www.pingcap.com/blog/example-post/ --dry-run

# Trash multiple URLs
python3 wp_trash_posts.py \
  --url https://www.pingcap.com/blog/post-a/ \
  --url https://www.pingcap.com/blog/post-b/

# Trash from a file (one URL per line)
python3 wp_trash_posts.py --file urls.txt

# Custom post type endpoints are tried first
python3 wp_trash_posts.py --file urls.txt --post-type articles --dry-run

# Large batches: add a small delay and save failed URLs
python3 wp_trash_posts.py --file urls.txt --post-type articles --delay 0.2 --failed-file failed-urls.txt
```

The tool supports standard post URLs and `?p=<post_id>` URLs. It moves posts to the WordPress trash, not permanent deletion.

### Generate Redirection import CSV

Generate a Redirection plugin import CSV from a text file of paths or URLs:

```bash
cd content-ops
python3 generate_redirection_csv.py --file urls.txt --target https://www.pingcap.com/
```

This writes `redirection-import.csv` with one redirect per line. Input lines may be full URLs or relative paths such as `/blog/example-post/`.
The generated CSV uses the Redirection CSV columns `source,target,regex,code`.

### What gets migrated

| Item | Notes |
|------|-------|
| Post body | Block format (requires English-site auth); falls back to HTML otherwise |
| Featured image | Downloaded and uploaded to the Japanese site's media library |
| Inline images | English-site CDN domains are detected and migrated automatically |
| Author | Matched to a Japanese-site user by Display name |
| Categories / Tags | Matched by name; created automatically if missing |
| Yoast SEO | title, description, canonical, OG metadata |
| Publish status | Draft (requires manual review before publishing) |
