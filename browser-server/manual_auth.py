import asyncio
import os
import pathlib
import random
import sys
import tempfile

from playwright.async_api import async_playwright


async def main(skip_automation: bool = False):
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
            args=sum(
                [
                    [
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-web-security",
                        "--disable-infobars",
                        "--disable-extensions",
                        "--start-maximized",
                    ]
                ],
                ["--ozone-platform=wayland"] if skip_automation else [],
            ),
            ignore_default_args=[
                "--disable-component-extensions-with-background-pages"
            ],
            headless=bool(os.getenv("RUN_HEADLESS", "False").lower() == "True".lower()),
        )
        page = await browser.new_page(
            viewport={"width": 1280, "height": 1024},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
        )
        page.on("close", handle_manual_auth_close)
        await page.goto("https://takeout.google.com/settings/takeout/custom/photos")
        if not skip_automation:
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
                await page.wait_for_timeout(5000)
                if not await page.get_by_text("Try again", exact=True).is_hidden():
                    pathlib.Path("/app/browser-downloads/html").write_text(
                        await page.content()
                    )
                    await page.screenshot(path="/app/browser-downloads/error_page.jpg")
                    return
                password = os.getenv("USER_P")
                await page.screenshot(path="/app/browser-downloads/password_page.jpg")
                await page.type(
                    selector="input[type=password]",
                    text=password,
                    delay=random.randint(11, 49),
                )
                await page.wait_for_timeout(1537)
                await page.locator(f"button#passwordNext").or_(
                    page.locator(f"div#passwordNext")
                ).click()
            if page.url.startswith("https://accounts.google.com/v3/signin/challenge"):
                # await page.screenshot(path="/app/browser-downloads/2fa_page.jpg")
                await page.locator(f'div[data-challengetype="39"]').click()
                await page.wait_for_url(
                    "https://takeout.google.com/settings/takeout/custom/photos"
                )
                await handle_manual_auth_close(page)
        while manual_auth_wait:
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main("--skip_automation" in sys.argv))
