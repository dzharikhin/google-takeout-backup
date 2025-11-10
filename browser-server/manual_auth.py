import asyncio
import os
import pathlib
import random
import sys
import tempfile

from playwright.async_api import async_playwright

downloads_path = pathlib.Path("/app/browser-downloads")
default_timeout = float(os.getenv("TIMEOUT_MILLIS", "30000"))


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

    async with async_playwright() as playwright:
        headless_mode = os.getenv("HEADLESS_MODE", "headed")
        print(f"executing script with {headless_mode=}")
        browser = await playwright.chromium.launch(
            args=sum(
                [
                    [
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-web-security",
                        "--disable-infobars",
                        "--disable-extensions",
                        "--start-maximized",
                        "--disable-gpu",
                    ]
                ],
                ["--ozone-platform=wayland"] if headless_mode == "headed" else [],
            ),
            ignore_default_args=[
                "--disable-component-extensions-with-background-pages"
            ],
            headless=bool(headless_mode.lower() == "headless".lower()),
        )
        page = await browser.new_page(
            viewport={"width": 1280, "height": 1024},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
        )
        page.set_default_timeout(default_timeout)
        page.on("close", handle_manual_auth_close)
        try:
            await page.goto("https://takeout.google.com/settings/takeout/custom/photos")
            if headless_mode == "headed":
                print(
                    f"{headless_mode=}: expecting manual execution. Just close browser window when auth is successfull"
                )
            else:
                print(f"{headless_mode=}: executing automatic login script")
                if page.url.startswith("https://accounts.google.com/v3/signin"):
                    email = os.getenv("USER_E")
                    await page.focus(selector="input[type=email]")
                    await page.type(
                        selector="input[type=email]",
                        text=email,
                        delay=random.randint(11, 49),
                    )
                    await page.wait_for_timeout(1666)
                    await page.locator(f"button#identifierNext").or_(
                        page.locator(f"div#identifierNext")
                    ).click()
                    if not await page.get_by_text("Try again", exact=True).is_hidden(timeout=default_timeout):
                        raise Exception("failed to enter email")
                    password = os.getenv("USER_P")
                    await page.type(
                        selector="input[type=password]",
                        text=password,
                        delay=random.randint(11, 49),
                    )
                    await page.wait_for_timeout(random.randint(1523, 1997))
                    await page.locator(f"button#passwordNext").or_(
                        page.locator(f"div#passwordNext")
                    ).click()
                if page.url.startswith(
                    "https://accounts.google.com/v3/signin/challenge"
                ):
                    # await page.screenshot(path=downloads_path.joinpath("2fa_page.jpg")
                    await page.locator(f'div[data-challengetype="39"]').click(timeout=default_timeout)
                    await page.wait_for_url(
                        "https://takeout.google.com/settings/takeout/custom/photos"
                    )
                    await handle_manual_auth_close(page)
        except Exception:
            try:
                if page and not page.is_closed():
                    downloads_path.joinpath(f"error_url").write_text(page.url)
                    downloads_path.joinpath(f"error_html").write_text(
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
