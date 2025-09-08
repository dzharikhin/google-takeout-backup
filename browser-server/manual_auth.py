import asyncio
import os
import pathlib
import tempfile

from playwright.async_api import async_playwright


async def main():
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
        browser = await playwright.chromium.launch(
            args=[
                "--disable-blink-features=AutomationControlled",
                "--ozone-platform=wayland",
            ],
            headless=bool(os.getenv("RUN_HEADLESS", "False").lower() == "True".lower()),
        )
        page = await browser.new_page()
        page.on("close", handle_manual_auth_close)
        await page.goto("https://takeout.google.com/settings/takeout/custom/photos")
        while manual_auth_wait:
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
