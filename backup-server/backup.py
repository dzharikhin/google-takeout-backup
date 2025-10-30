import asyncio
import csv
import datetime
import os
import pathlib
import re
import shutil
import subprocess
import sys
import zipfile

from playwright.async_api import async_playwright, TimeoutError, Error

TAKEOUT_BASEURL = "https://takeout.google.com/"
BACKUP_FRESHNESS_INTERVAL = datetime.timedelta(
    hours=int(os.getenv("BACKUP_FRESHNESS_THRESHOLD_HOURS", "12"))
)
TIMEOUT_MILLIS = int(os.getenv("TIMEOUT_MILLIS", "30000"))


class EarlyReturn(Exception):
    pass


auth_json_path = pathlib.Path(".auth_encoded")

downloads_path = pathlib.Path("downloads")
backup_path = pathlib.Path("photos")
timestamp_path = backup_path.joinpath(".timestamp")
text_labels_source = pathlib.Path(f"keys_{os.getenv("GOOGLE_LANG", "RU")}.csv")

with text_labels_source.open(mode="rt") as labels_data:
    text_labels = {row[0]: row[1] for row in csv.reader(labels_data, delimiter="=")}


async def filter_most_recent_archive(
    page, ready_archive_links, last_snapshot_timestamp: datetime.datetime
):
    for ready_archive_link in ready_archive_links:
        await page.goto(f"{TAKEOUT_BASEURL}{ready_archive_link}")
        await handle_reauth(page, target_url=f"{TAKEOUT_BASEURL}{ready_archive_link}")
        report_download_button = page.locator(
            f'a[aria-label="{text_labels["report.download"]}"]'
        )
        async with page.expect_download(timeout=TIMEOUT_MILLIS * 2) as download_info:
            await report_download_button.click()
            await handle_reauth(page)
        download_meta = await download_info.value
        current_archive_timestamp = parse_takeout_timestamp(
            download_meta.suggested_filename.split("-", 3)[1]
        )
        await download_meta.cancel()
        if (
            not last_snapshot_timestamp
            or current_archive_timestamp > last_snapshot_timestamp
        ):
            return ready_archive_link, current_archive_timestamp

    return None, None


async def handle_reauth(page, target_url=None, timeout_millis=TIMEOUT_MILLIS * 2, max_tries=3):
    tries = 0
    while tries < max_tries:
        if page.url.startswith("https://accounts.google.com/v3/signin/accountchooser"):
            await page.locator("form li>div").first.click()
        elif page.url.startswith("https://gds.google.com/web/homeaddress"):
            element_by_exact_text = page.get_by_text(f"{text_labels["skip"]}")
            await element_by_exact_text.click()
        await asyncio.sleep(timeout_millis / 1000 / max_tries)
        if page.url.startswith("https://accounts.google.com/v3/signin/challenge/pwd"):
            await page.fill(
                selector="input[type=password]", value=os.getenv("ENCODED_PASS")
            )
            await page.locator(f"button#passwordNext").or_(
                page.locator(f"div#passwordNext")
            ).click()
            if target_url:
                await page.wait_for_url(
                    lambda u: u.startswith(target_url), timeout=timeout_millis
                )
            return
        tries += 1


async def main():
    if not auth_json_path:
        raise Exception(f"{auth_json_path} is required")
    if not os.getenv("ENCODED_PASS"):
        raise Exception("ENCODED_PASS env is required")
    # snapshot_in_progress = any(downloads_path.iterdir())

    last_snapshot_timestamp = None
    if timestamp_path.exists():
        last_snapshot_timestamp = parse_takeout_timestamp(timestamp_path.read_text())

    print("inited config")
    async with async_playwright() as playwright:
        async with await playwright.chromium.connect(
            os.getenv("BROWSER_SERVER_URL", f"ws://localhost:8082/srv"),
            timeout=TIMEOUT_MILLIS,
        ) as browser:
            print("inited browser")
            page = await browser.new_page(
                storage_state={"encoded_value": auth_json_path.read_text()},
                accept_downloads=True,
                viewport={"width": 1280, "height": 1024},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
            )
            async with page:
                console = []

                async def handle_console(msg):
                    console.append(msg.text)

                page.on("console", handle_console)

                network = []

                async def handle_request(request):
                    network.append(f"Request: {request.method} {request.url}")

                async def handle_response(response):
                    network.append(f"Response: {response.status} {response.url}")

                page.on("request", handle_request)
                page.on("response", handle_response)

                print("inited page")
                now = datetime.datetime.now()
                try:
                    await page.goto(f"{TAKEOUT_BASEURL}manage")
                    if page.url.startswith("https://accounts.google.com/v3/signin"):
                        print(f"auth required, trying to reauth on {page.url}")
                        await handle_reauth(page, target_url=f"{TAKEOUT_BASEURL}manage")

                    export_in_progress = page.locator(
                        f"text={text_labels["decline.export"]}"
                    )
                    if (
                        last_snapshot_timestamp
                        and abs(from_last_backup := now - last_snapshot_timestamp)
                        < BACKUP_FRESHNESS_INTERVAL
                    ):
                        raise EarlyReturn(
                            f"Last backup was made {from_last_backup.total_seconds() // 3600} hours ago. "
                            f"Skipping new for at least {BACKUP_FRESHNESS_INTERVAL.total_seconds() // 3600} hours lag"
                        )
                    else:
                        print(
                            f"Last backup was made at {last_snapshot_timestamp}, {now=}. Checking if new backup is available"
                        )
                    if not await export_in_progress.is_hidden():
                        raise EarlyReturn("Currently export is in progress, exiting")

                    for f in downloads_path.iterdir():
                        if f.is_file():
                            f.unlink()
                        elif f.is_dir():
                            shutil.rmtree(f)

                    ready_archive_links = await page.locator(
                        "a",
                        has=page.locator(
                            "p", has_text=f"{text_labels["export.ready.label"]}"
                        ),
                    ).all()
                    ready_archive_links = [
                        await link.get_attribute("href") for link in ready_archive_links
                    ]
                    target_archive, target_archive_timestamp = (
                        await filter_most_recent_archive(
                            page, ready_archive_links, last_snapshot_timestamp
                        )
                    )
                    if not target_archive:
                        await request_new_archive(page)
                        raise EarlyReturn(
                            "We need new backup, requested export and exiting"
                        )

                    print(
                        f"selected target archive: {target_archive}, {target_archive_timestamp=}"
                    )
                    target_archive_download_path = downloads_path.joinpath(
                        target_archive.split("/")[-1]
                    )
                    target_archive_download_path.mkdir()
                    await page.goto(f"{TAKEOUT_BASEURL}{target_archive}")
                    await handle_reauth(page, target_url=f"{TAKEOUT_BASEURL}{target_archive}")
                    archive_parts = await page.locator(
                        f'a[href*="takeout/download"]:not([aria-label*="{text_labels["report.download"]}"])'
                    ).all()
                    print(f"going to download {len(archive_parts)} parts")
                    try:
                        for i, archive_part in enumerate(archive_parts, 1):
                            async with page.expect_download(timeout=TIMEOUT_MILLIS * 2) as download_info:
                                await archive_part.click()
                                await handle_reauth(page)
                            download_meta = await download_info.value
                            for try_n in range(1, 4):
                                try:
                                    await download_meta.save_as(
                                        target_archive_download_path.joinpath(
                                            download_meta.suggested_filename
                                        )
                                    )
                                    break
                                except Error:
                                    if try_n >= 3:
                                        raise
                                    print(f"retrying download {i} after {try_n}")

                            await download_meta.delete()
                            print(f"downloaded {i}/{len(archive_parts)} parts")
                    except TimeoutError:
                        if not await page.locator(f'div[role="dialog"]').is_hidden():
                            await request_new_archive(page)
                            raise EarlyReturn(
                                "We need new backup, requested export and exiting"
                            )
                        else:
                            raise
                    state = await page.context.storage_state()
                    auth_json_path.write_text(state["encoded_value"])
                except EarlyReturn as e:
                    state = await page.context.storage_state()
                    auth_json_path.write_text(state["encoded_value"])
                    print(e)
                    return
                except Exception:
                    try:
                        if page and not page.is_closed():
                            now = datetime.datetime.now()
                            encoded_timestamp = encode_takeout_timestamp(now)
                            downloads_path.joinpath(
                                f"{encoded_timestamp}.url"
                            ).write_text(page.url)
                            downloads_path.joinpath(
                                f"{encoded_timestamp}.html"
                            ).write_text(await page.content())
                            await page.screenshot(
                                path=downloads_path.joinpath(f"{encoded_timestamp}.jpg")
                            )
                            if console:
                                downloads_path.joinpath(
                                    f"{encoded_timestamp}.console"
                                ).write_text("\n".join(console))
                            if network:
                                downloads_path.joinpath(
                                    f"{encoded_timestamp}.net"
                                ).write_text("\n".join(network))
                    except Exception as e:
                        print(f"failed to collect diagnostic info with {e}, ignoring")
                    raise

    print("closed browser")

    for f in target_archive_download_path.glob("*.zip"):
        with zipfile.ZipFile(f, "r") as archive:
            # unarchived_path = target_archive_download_path.joinpath(os.path.commonpath(archive.namelist()))
            archive.extractall(target_archive_download_path)
    print("unpacked archives")
    unpacked_root_dir = [item for item in target_archive_download_path.iterdir() if item.is_dir()][0]
    for root, dirs, files in unpacked_root_dir.walk():
        for path in dirs:
            folder_path = pathlib.Path(root.joinpath(path))
            if m := re.match(text_labels["year.folder.template"], folder_path.stem):
                folder_path.rename(folder_path.parent.joinpath(f"Photos from {m.group(1)}"))

    processed_photos_path = target_archive_download_path.joinpath("export")
    try:
        subprocess.run(
            [
                "gpth",
                "--copy",
                "-i",
                unpacked_root_dir,
                "-o",
                processed_photos_path,
                "--albums",
                "duplicate-copy",
                "--no-divide-to-dates",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"Stderr: {e.stderr}", file=sys.stderr)
        print(f"Stdout: {e.stdout}")
        raise e

    all_photos_path = processed_photos_path.joinpath(
        os.getenv("GPTH_DEFAULT_FOLDER_NAME", "ALL_PHOTOS")
    )
    for f in all_photos_path.iterdir():
        shutil.move(f, processed_photos_path.joinpath(f.name))
    all_photos_path.rmdir()
    print("processed archives")
    shutil.copytree(processed_photos_path, backup_path, dirs_exist_ok=True)
    shutil.rmtree(target_archive_download_path)
    timestamp_path.write_text(encode_takeout_timestamp(target_archive_timestamp))
    print(f"successfully backed up up to {target_archive_timestamp}")


async def request_new_archive(page):
    await page.goto("https://takeout.google.com/settings/takeout/custom/photos")
    element_by_exact_text = page.get_by_text(f"{text_labels["proceed"]}")
    await element_by_exact_text.click()
    element_by_exact_text = page.get_by_text(f"{text_labels["create.export"]}")
    await element_by_exact_text.click()


def parse_takeout_timestamp(val):
    return datetime.datetime.strptime(val, "%Y%m%dT%H%M%SZ")


def encode_takeout_timestamp(val):
    return val.strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    asyncio.run(main())
