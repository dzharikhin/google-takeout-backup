import asyncio
import logging
import os
import pathlib
import sys
import tempfile

from invisible_playwright.async_api import InvisiblePlaywright

from auth import States, GoogleLoginModel, GoogleLoginMachine, TAKEOUT_URL

logging.basicConfig(level=logging.INFO)

downloads_path = pathlib.Path("./browser-downloads")
default_timeout = float(os.getenv("TIMEOUT_MILLIS", "30000"))


async def main():
    print(os.getenv("DISPLAY"))
    global manual_auth_wait
    manual_auth_wait = [1]

    headless_mode = os.getenv("HEADLESS_MODE", "headed")
    headless = bool(headless_mode.lower() == "headless")
    print(f"{headless_mode=}")
    async with InvisiblePlaywright(headless=headless) as browser:
        page = await browser.new_page()
        page.set_default_timeout(default_timeout)

        async def handle_manual_auth_close(page):
            global manual_auth_wait
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_dir = pathlib.Path(tmp)
                    file_path = tmp_dir.joinpath("file.json")
                    await page.context.storage_state(path=file_path)
                    print()
                    print(file_path.read_text())
                    print()
            finally:
                manual_auth_wait.pop()

        try:
            await page.goto(TAKEOUT_URL)
            if headless:
                print(f"{headless_mode=}: executing automatic login script")
                if page.url.startswith("https://accounts.google.com/v3/signin"):
                    model = GoogleLoginModel(page=page, timeout=default_timeout)
                    GoogleLoginMachine(model, states=States.as_list(), initial=States.start, queued=True)
                    await model.sign_in()
                    print("login script is finished")
                if page.url.startswith(TAKEOUT_URL):
                    await handle_manual_auth_close(page)
            else:
                page.on("close", handle_manual_auth_close)
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
