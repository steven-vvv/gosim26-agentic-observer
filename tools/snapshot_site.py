"""Render the public GOSIM handbook and keep comparable local snapshots."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from markdownify import markdownify
from playwright.sync_api import sync_playwright

PAGES = ("brief", "rules", "docs", "resources", "faq")
BASE = "https://create.gosim.org/survey26/platform"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("/tmp/gosim-site"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    fetched = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path="/usr/bin/chromium-browser",
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        for lang in ("zh", "en"):
            page = browser.new_page(locale="zh-CN" if lang == "zh" else "en-US")
            for name in PAGES:
                url = f"{BASE}/{name}?lang={lang}"
                page.goto(url, wait_until="networkidle", timeout=30000)
                page.locator("main").wait_for(timeout=10000)
                if name == "faq":
                    page.locator("details.faq").evaluate_all(
                        "nodes => nodes.forEach(node => { node.open = true })"
                    )
                html = page.locator("main").inner_html()
                text = page.locator("main").inner_text()
                stem = args.out / f"{lang}-{name}"
                stem.with_suffix(".html").write_text(html, encoding="utf-8")
                stem.with_suffix(".txt").write_text(text, encoding="utf-8")
                stem.with_suffix(".md").write_text(
                    f"---\nsource: {url}\nfetched_utc: {fetched}\n"
                    "method: rendered main; all FAQ entries expanded\n---\n\n"
                    + markdownify(html, heading_style="ATX"),
                    encoding="utf-8",
                )
                print(json.dumps({"url": url, "chars": len(text), "title": page.title()}, ensure_ascii=False))
            page.close()
        browser.close()


if __name__ == "__main__":
    main()
