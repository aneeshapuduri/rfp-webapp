#!/usr/bin/env python3
"""
Static accessibility audit against the live app's actual rendered HTML and CSS.

Not a substitute for a full browser-based scan (axe/pa11y) — this sandbox can't download a
browser (Puppeteer's Chrome download is blocked by network egress restrictions here). But
since these pages are server-rendered Jinja2 templates with no client-side JS altering the
DOM, checking the real rendered HTML output plus computing actual WCAG contrast ratios from
the CSS covers the criteria that matter most and don't require a browser: missing alt text,
unlabeled form fields, heading structure, table captions, and color contrast.
"""
import re
import sys

import requests
from bs4 import BeautifulSoup

BASE = "http://127.0.0.1:8000"


def contrast_ratio(hex1: str, hex2: str) -> float:
    def to_rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    def luminance(rgb):
        def chan(c):
            c = c / 255
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        r, g, b = [chan(c) for c in rgb]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    l1 = luminance(to_rgb(hex1))
    l2 = luminance(to_rgb(hex2))
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def check_page(path: str, name: str) -> list[str]:
    issues = []
    resp = requests.get(BASE + path)
    if resp.status_code != 200:
        return [f"Page returned {resp.status_code}, could not scan"]
    soup = BeautifulSoup(resp.text, "html.parser")

    # 1. Images without alt text
    for img in soup.find_all("img"):
        if not img.get("alt") and img.get("aria-hidden") != "true":
            issues.append(f"<img> missing alt text: {img}")

    # 2. Form inputs without associated labels
    for inp in soup.find_all(["input", "select", "textarea"]):
        if inp.get("type") == "hidden":
            continue
        input_id = inp.get("id")
        has_label = False
        if input_id:
            has_label = bool(soup.find("label", attrs={"for": input_id}))
        if not has_label and inp.find_parent("label"):
            has_label = True
        if not has_label and (inp.get("aria-label") or inp.get("aria-labelledby")):
            has_label = True
        if not has_label:
            issues.append(f"Form field missing associated label: {inp}")

    # 3. Heading hierarchy shouldn't skip levels (h1 -> h3 with no h2)
    headings = [int(h.name[1]) for h in soup.find_all(re.compile(r"^h[1-6]$"))]
    for i in range(1, len(headings)):
        if headings[i] - headings[i - 1] > 1:
            issues.append(f"Heading level skips from h{headings[i-1]} to h{headings[i]}")

    # 4. Tables should have a caption or aria-label for screen readers
    for table in soup.find_all("table"):
        has_caption = table.find("caption") is not None
        has_aria = table.get("aria-label") or table.get("aria-labelledby")
        if not has_caption and not has_aria:
            issues.append("Table missing <caption> or aria-label")

    # 5. Skip link present (helps keyboard users bypass repeated nav)
    if path in ("/",) and not soup.find("a", class_="skip-link"):
        issues.append("No skip-to-content link found")

    # 6. Every link/button has discernible text
    for el in soup.find_all(["a", "button"]):
        text = el.get_text(strip=True)
        if not text and not el.get("aria-label"):
            issues.append(f"Link/button with no discernible text: {el}")

    return issues


def check_contrast() -> list[str]:
    """WCAG AA requires 4.5:1 for normal text, 3:1 for large text / UI components."""
    issues = []
    checks = [
        ("Navy heading text on white", "#1f2d50", "#ffffff", 4.5),
        ("Gray body/meta text on white", "#595959", "#ffffff", 4.5),
        ("Accent link color on white", "#2e74b5", "#ffffff", 4.5),
        ("White nav text on navy header", "#ffffff", "#1f2d50", 4.5),
        ("Light blue nav links on navy header", "#dce6f1", "#1f2d50", 4.5),
        ("Green status text on white", "#1e7a34", "#ffffff", 4.5),
        ("Orange status text on white", "#b35c00", "#ffffff", 4.5),
        ("Red status/error text on white", "#b3261e", "#ffffff", 4.5),
        ("Table header navy bg, white text", "#ffffff", "#1f2d50", 4.5),
    ]
    for label, fg, bg, required in checks:
        ratio = contrast_ratio(fg, bg)
        status = "PASS" if ratio >= required else "FAIL"
        print(f"  [{status}] {label}: {ratio:.2f}:1 (needs {required}:1)")
        if ratio < required:
            issues.append(f"{label} fails contrast: {ratio:.2f}:1 < {required}:1 required")
    return issues


def main():
    all_issues = {}

    print("=" * 70)
    print("STATIC ACCESSIBILITY AUDIT")
    print("=" * 70)

    pages = [
        ("/", "Dashboard"),
        ("/new", "New Project Form"),
        ("/audit", "Audit Log"),
    ]
    scan_pid = open("/tmp/scan_pid.txt").read().strip() if __import__("os").path.exists("/tmp/scan_pid.txt") else None
    if scan_pid:
        pages.append((f"/projects/{scan_pid}", "Project Detail"))

    for path, name in pages:
        print(f"\n--- {name} ({path}) ---")
        issues = check_page(path, name)
        if issues:
            for i in issues:
                print(f"  ISSUE: {i}")
        else:
            print("  No structural issues found.")
        all_issues[name] = issues

    print(f"\n--- Color Contrast (computed against actual style.css values) ---")
    contrast_issues = check_contrast()
    all_issues["Contrast"] = contrast_issues

    print("\n" + "=" * 70)
    total_issues = sum(len(v) for v in all_issues.values())
    if total_issues == 0:
        print(f"PASS — no structural or contrast issues found across {len(pages)} pages + contrast checks.")
    else:
        print(f"FOUND {total_issues} issue(s) — see above.")
    print("=" * 70)
    print("\nNote: this covers structural HTML issues and color contrast, not full browser-based")
    print("checks (e.g. actual keyboard tab order, screen reader announcement testing). Native")
    print("HTML elements (a, button, input, select) are used throughout rather than custom")
    print("widgets, which covers most keyboard-accessibility risk by default, but a full")
    print("axe/pa11y or manual AT audit is still recommended before wide rollout.")

    return 0 if total_issues == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
