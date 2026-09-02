#!/usr/bin/env python3
"""Build a Lark card message from the migration summary JSON."""

import json
import os
import sys
import requests


def build_card(d):
    month = d["month"]
    pub = d.get("published", [])
    dra = d.get("drafted", [])
    ski = d.get("skipped", [])
    fai = d.get("failed", [])

    has_fail = len(fai) > 0
    header_title = (
        f"Blog migration {month} needs attention"
        if has_fail
        else f"Blog migration {month} completed"
    )
    header_color = "red" if has_fail else "green"

    elements = []

    stats = (
        f"**Published:** {len(pub)}  |  **Drafts:** {len(dra)}  "
        f"|  **Skipped:** {len(ski)}  |  **Failed:** {len(fai)}"
    )
    elements.append({"tag": "markdown", "content": stats})

    if pub:
        md = "\n".join(f'• [{item["title"]}]({item["url"]})' for item in pub)
        elements.append({"tag": "markdown", "content": f"**Published:**\n{md}"})

    if dra:
        lines = []
        for item in dra:
            lines.append(
                f'• [{item["title"]}]({item["url"]})  '
                f'`missing: {item["missing_author"]}`'
            )
        md = "\n".join(lines)
        elements.append(
            {"tag": "markdown", "content": f"**Drafts (author not found):**\n{md}"}
        )

    if ski:
        elements.append({"tag": "hr"})
        md = "\n".join(f'• [{item["title"]}]({item["url"]})' for item in ski)
        elements.append(
            {
                "tag": "markdown",
                "content": f"**Skipped - already on JP ({len(ski)}):**\n{md}",
            }
        )

    if fai:
        md = "\n".join(f"• {t}" for t in fai)
        elements.append({"tag": "markdown", "content": f"**Failed:**\n{md}"})

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": header_title},
                "template": header_color,
            },
            "body": {"elements": elements},
        },
    }


def main():
    summary_json = os.environ.get("SUMMARY", "")
    webhook = os.environ.get("LARK_WEBHOOK", "")

    if not summary_json or not webhook:
        print("SUMMARY or LARK_WEBHOOK not set", file=sys.stderr)
        sys.exit(1)

    d = json.loads(summary_json)
    card = build_card(d)

    resp = requests.post(
        webhook,
        headers={"Content-Type": "application/json"},
        data=json.dumps(card, ensure_ascii=False),
        timeout=15,
    )
    print(resp.text)


if __name__ == "__main__":
    main()
