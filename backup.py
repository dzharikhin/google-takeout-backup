import asyncio
import csv
import datetime
import os
import pathlib

from playwright.async_api import async_playwright, TimeoutError
import shutil

TAKEOUT_BASEURL = "https://takeout.google.com/"

auth_json_path = pathlib.Path("browser-server/auth.json")
auth_path = pathlib.Path("browser-server/browser-local-storage")
downloads_path = pathlib.Path("downloads")
timestamp_path = pathlib.Path("snapshot/.timestamp")
text_labels_source = pathlib.Path(f"keys_{os.getenv("GOOGLE_LANG", "RU")}.csv")
browser_params_source = pathlib.Path(f"browser-server/.env")

auth_path.mkdir(exist_ok=True)
downloads_path.mkdir(exist_ok=True)

with text_labels_source.open(mode="rt") as labels_data:
    text_labels = {row[0]: row[1] for row in csv.reader(labels_data, delimiter='=')}

with browser_params_source.open(mode="rt") as browser_env_data:
    browser_cfg = {row[0]: row[1] for row in csv.reader(browser_env_data, delimiter='=')}

async def manual_auth():
    auth_json_path.unlink(missing_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect(f"ws://localhost:{browser_cfg["PROXY_PUBLIC_PORT"]}/srv")
        page = await browser.new_page()
        await page.goto("https://takeout.google.com/settings/takeout/custom/photos")
        print(await page.title())
        input("Press Enter in the terminal to continue Playwright script...")
        await page.context.storage_state(path=auth_json_path)
        await browser.close()


async def filter_most_recent_archive(page, ready_archive_links, last_snapshot_timestamp: datetime.datetime):
    if not last_snapshot_timestamp:
        return ready_archive_links[0]

    for ready_archive_link in ready_archive_links:
        await page.goto(f"{TAKEOUT_BASEURL}{ready_archive_link}")
        report_download_button = page.locator(f"a[aria-label=\"{text_labels["report.download"]}\"]")
        async with page.expect_download() as download_info:
            await report_download_button.click()
            await handle_reauth(page)
        download_meta = await download_info.value
        current_archive_timestamp = parse_takeout_timestamp(download_meta.suggested_filename.split("-", 3)[1])
        if current_archive_timestamp > last_snapshot_timestamp:
            return ready_archive_link

    return None


async def handle_reauth(page):
    if page.url.startswith("https://accounts.google.com/v3/signin"):
        await page.fill(selector="input[type=password]", value=os.getenv("ENCODED_PASS"))
        await page.locator("div#passwordNext").click()


async def main():
    if not auth_json_path.exists():
        raise Exception("manual auth required")
    # snapshot_in_progress = any(downloads_path.iterdir())

    last_snapshot_timestamp = None
    if timestamp_path.exists():
        last_snapshot_timestamp = parse_takeout_timestamp(timestamp_path.read_text())
        
    
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect(f"ws://localhost:{browser_cfg["PROXY_PUBLIC_PORT"]}/srv")
        # .launch_persistent_context(auth_path, accept_downloads=True, args=["--disable-blink-features=AutomationControlled"], headless=bool(os.getenv("RUN_HEADLESS", "False") == "True"))
        page = await browser.new_page(storage_state=auth_json_path, accept_downloads=True)
        await page.goto(f"{TAKEOUT_BASEURL}manage")
        if page.url.startswith("https://accounts.google.com/v3/signin"):
            raise Exception("manual auth required")
        export_in_progress = page.locator(f"text={text_labels["decline.export"]}")
        if not await export_in_progress.is_hidden():
            await browser.close()
            return

        shutil.rmtree(downloads_path)
        downloads_path.mkdir(exist_ok=True)

        ready_archive_links = await page.locator("a", has=page.locator("p", has_text=f"{text_labels["export.ready.label"]}")).all()
        ready_archive_links = [await link.get_attribute("href") for link in ready_archive_links]
        target_archive = await filter_most_recent_archive(page, ready_archive_links, last_snapshot_timestamp)
        if not target_archive:
            await request_new_archive(page)
            await browser.close()
            return
        

        target_archive_download_path = downloads_path.joinpath(target_archive.split("/")[-1])
        target_archive_download_path.mkdir()
        await page.goto(f"{TAKEOUT_BASEURL}{target_archive}")
        archive_parts = await page.locator(f"a[href*=\"takeout/download\"]:not([aria-label*=\"{text_labels["report.download"]}\"])").all()
        try:
            for archive_part in archive_parts:
                async with page.expect_download() as download_info:
                    await archive_part.click()
                    await handle_reauth(page)
                download_meta = await download_info.value
                await download_meta.save_as(target_archive_download_path.joinpath(download_meta.suggested_filename))
                await download_meta.delete()
        except TimeoutError as e:
            report_error(e)
            await request_new_archive(page)
        await browser.close()


async def request_new_archive(page):
    await page.goto("https://takeout.google.com/settings/takeout/custom/photos")
    element_by_exact_text = page.get_by_text(f"{text_labels["proceed"]}")
    await element_by_exact_text.click()
    element_by_exact_text = page.get_by_text(f"{text_labels["create.export"]}")
    await element_by_exact_text.click()


def parse_takeout_timestamp(val):
    return datetime.datetime.strptime(val, "%Y%m%dT%H%M%SZ")


def report_error(err):
    print(err)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        report_error(e)
        raise