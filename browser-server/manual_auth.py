import asyncio
import csv
import os
import pathlib
import random
import sys
import tempfile

from invisible_playwright.async_api import InvisiblePlaywright

downloads_path = pathlib.Path("/app/browser-downloads")
default_timeout = float(os.getenv("TIMEOUT_MILLIS", "30000"))
text_labels_source = pathlib.Path(f"keys_{os.getenv('GOOGLE_LANG', 'RU')}.csv")

with text_labels_source.open(mode="rt") as labels_data:
    text_labels = {row[0]: row[1] for row in csv.reader(labels_data, delimiter="=")}


async def main():
    print(os.getenv("DISPLAY"))
    manual_auth_wait = [1]

    async def handle_manual_auth_close(page):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = pathlib.Path(tmp)
            file_path = tmp_dir.joinpath("file.json")
            await page.context.storage_state(path=file_path)
            print()
            print(file_path.read_text())
            print()
            manual_auth_wait.pop()

    headless_mode = os.getenv("HEADLESS_MODE", "headed")
    headless = bool(headless_mode.lower() == "headless")
    print(f"{headless_mode=}")
    async with InvisiblePlaywright(headless=headless) as browser:
        page = await browser.new_page()
        page.set_default_timeout(default_timeout)
        page.on("close", handle_manual_auth_close)
        try:
            await page.goto("https://takeout.google.com/settings/takeout/custom/photos")
            if headless:
                print(f"{headless_mode=}: executing automatic login script")
                if page.url.startswith("https://accounts.google.com/v3/signin"):
                    email = os.getenv("USER_E")
                    email_field = page.locator("input[type=email]").or_(page.locator("input#identifierId"))
                    await email_field.focus()
                    await email_field.press_sequentially(email, delay=random.randint(11, 49))
                    await page.wait_for_timeout(1666)
                    await page.locator(f"button#identifierNext").or_(
                        page.locator(f"div#identifierNext")
                    ).click()

                    await page.wait_for_selector("input[type=password]", timeout=default_timeout)
                    await page.focus(selector="input[type=password]")
                    password = os.getenv("USER_P")
                    await page.type(
                        selector="input[type=password]",
                        text=password,
                        delay=random.randint(11, 49),
                    )
                    await page.wait_for_timeout(random.randint(1523, 1997))
                    await page.locator(f"button#passwordNext").or_(
                        page.locator(f"div#passwordNext")
                    ).click(timeout=default_timeout)
                if page.url.startswith("https://accounts.google.com/v3/signin/challenge/skotp"):
                    await page.get_by_text(text_labels["try.another.factor"]).click()
                    await page.wait_for_url(
                        "https://accounts.google.com/v3/signin/challenge"
                    )
                if page.url.startswith(
                    "https://accounts.google.com/v3/signin/challenge"
                ):
                    # await page.screenshot(path=downloads_path.joinpath("2fa_page.jpg")
                    await page.locator(f'div[data-challengetype="39"]').click(timeout=default_timeout)
                    await page.wait_for_url(
                        "https://takeout.google.com/settings/takeout/custom/photos"
                    )
                    await handle_manual_auth_close(page)
            else:
                print(f"{headless_mode=}: expecting manual execution. Just close browser window when auth is successful")
        except Exception:
            try:
                if page and not page.is_closed():
                    downloads_path.joinpath(f"error_url.txt").write_text(page.url)
                    downloads_path.joinpath(f"error_html.html").write_text(
                        await page.content()
                    )
                    await page.screenshot(
                        path=downloads_path.joinpath(f"error_page_screenshot.jpg")
                    )
            except Exception as e:
                print(f"failed to collect diagnostic info: {e}", file=sys.stderr)
            raise
        while manual_auth_wait:
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
