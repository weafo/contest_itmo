#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pathlib
import re
import json
from playwright.sync_api import sync_playwright

OUT = pathlib.Path("data")
OUT.mkdir(exist_ok=True, parents=True)

PAGES = [
    ("ai", "https://abit.itmo.ru/program/master/ai"),
    ("ai_product", "https://abit.itmo.ru/program/master/ai_product"),
]

def main():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(accept_downloads=True)

        for code, url in PAGES:
            try:
                page = ctx.new_page()
                print(f"[INFO] Открываю страницу {code}...")
                # Ждём только загрузки DOM
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # Ждём появления кнопки
                page.wait_for_selector("button:has-text('Скачать учебный план')", timeout=15000)

                btn = page.get_by_role("button", name=re.compile(r"скачать.*учебн.*план", re.I))
                if not btn.count():
                    print(f"[WARN] Кнопка не найдена: {code}")
                    page.close()
                    continue

                # Ловим download
                with page.expect_download(timeout=15000) as d:
                    btn.first.click()
                dl = d.value
                filepath = OUT / dl.suggested_filename
                dl.save_as(filepath)

                try:
                    url_src = dl.url
                except Exception:
                    url_src = ""

                print(f"[OK] {code}: {filepath.name}")
                results.append({
                    "program": code,
                    "url": url_src,
                    "file": str(filepath)
                })

            except Exception as e:
                print(f"[ERR] {code}: {e}")

            finally:
                try:
                    page.close()
                except Exception:
                    pass

        ctx.close()
        browser.close()

    (OUT / "plan_files.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        "utf-8"
    )
    print(f"Saved: {OUT/'plan_files.json'}")

if __name__ == "__main__":
    main()