import asyncio
import csv
import datetime
import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import sys
import zipfile
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Error
from transitions.experimental.utils import with_model_definitions, add_transitions, transition
from transitions.extensions.asyncio import AsyncMachine, AsyncState

from auth import GoogleLoginModel, GoogleLoginMachine, States, TAKEOUT_BASEURL

logging.basicConfig(level=logging.INFO)
logging.getLogger("transitions.core").setLevel(logging.ERROR)

BACKUP_FRESHNESS_INTERVAL = datetime.timedelta(
    hours=int(os.getenv("BACKUP_FRESHNESS_THRESHOLD_HOURS", "12"))
)
TIMEOUT_MILLIS = int(os.getenv("TIMEOUT_MILLIS", "30000"))

auth_json_path = pathlib.Path(".auth_encoded")

downloads_path = pathlib.Path("downloads")
backup_path = pathlib.Path("photos")
timestamp_path = backup_path.joinpath(".timestamp")
text_labels_source = pathlib.Path(f"keys_{os.getenv('GOOGLE_LANG', 'RU')}.csv")

with text_labels_source.open(mode="rt") as labels_data:
    text_labels = {row[0]: row[1] for row in csv.reader(labels_data, delimiter="=")}


def name_enricher(outer):
    def _wrapper(func):
        original_name = func.__name__
        result = outer(func)
        result.original_name = original_name
        return result

    return _wrapper


def ref(func):
    return getattr(func, 'original_name', None) or func.__name__


class TakeoutStates:
    start = AsyncState(name="start")
    on_manage = AsyncState(name="on_manage")
    export_in_progress = AsyncState(name="export_in_progress", final=True)
    backup_fresh = AsyncState(name="backup_fresh", final=True)
    selecting_archive = AsyncState(name="selecting_archive", on_enter="find_most_recent_archive")
    requesting_archive = AsyncState(name="requesting_archive", on_enter="request_new_archive", final=True)
    on_archive = AsyncState(name="on_archive", on_enter="handle_archive_page")
    downloading = AsyncState(name="downloading", on_enter="download_archive_parts", final=True)
    complete = AsyncState(name="complete", final=True)

    @classmethod
    def as_list(cls):
        return [v for k, v in vars(cls).items() if isinstance(v, AsyncState)]


class TakeoutModel:
    def __init__(self, page, last_snapshot_timestamp):
        self.page = page
        self.timeout = TIMEOUT_MILLIS
        self.last_snapshot_timestamp = last_snapshot_timestamp
        self.target_archive = None
        self.target_archive_timestamp = None
        self.target_archive_download_path = None
        self.ready_archive_links = None
        self.archive_parts = None
        self.state = "start"

    async def is_export_running(self):
        return not await self.page.locator('button[data-job-id]').is_hidden(timeout=self.timeout)

    async def is_backup_fresh(self):
        if not self.last_snapshot_timestamp:
            return False
        now = datetime.datetime.now()
        return abs(now - self.last_snapshot_timestamp) < BACKUP_FRESHNESS_INTERVAL

    async def has_ready_archive_links(self):
        links = await self.page.locator('a[href]:has(svg path[d*="l-8 8z"])').all()
        hrefs = []
        for link in links:
            href = await link.get_attribute("href")
            if href:
                hrefs.append(href)
        self.ready_archive_links = hrefs
        return len(hrefs) > 0

    async def has_newer_archive(self):
        return self.target_archive is not None

    async def is_download_dialog(self):
        return not await self.page.locator(f'div[role="dialog"]').is_hidden()

    async def navigate_to_manage(self):
        await self.page.goto(f"{TAKEOUT_BASEURL}manage")

    async def ensure_auth(self):
        auth_model = GoogleLoginModel(
            page=self.page,
            timeout=self.timeout,
            email_env="IF_YOU_NEED_THIS_HERE_IT_IS_BAD",
            password_env="ENCODED_PASS",
        )
        GoogleLoginMachine(
            auth_model,
            states=States.as_list(),
            initial=States.start,
            queued=True,
        )
        await auth_model.sign_in()

    async def download_with_reauth(self, click_action):
        """Click download and handle re-auth redirect (302 → login page → download)"""
        for attempt in range(2):
            try:
                async with self.page.expect_download(timeout=TIMEOUT_MILLIS * 2) as download_info:
                    await click_action()
                return await download_info.value
            except Exception as e:
                if "Timeout" in str(type(e).__name__) or "Timeout" in str(e):
                    if await self.page.locator(f'div[role="dialog"]').is_hidden():
                        parsed = urlparse(self.page.url)
                        if parsed.netloc == "accounts.google.com":
                            await self.ensure_auth()
                            if self.page.url.startswith(TAKEOUT_BASEURL):
                                continue
                raise
        raise TimeoutError("Download failed after re-auth attempt")

    async def find_most_recent_archive(self):
        for ready_archive_link in self.ready_archive_links:
            await self.page.goto(f"{TAKEOUT_BASEURL}{ready_archive_link}")
            await self.ensure_auth()
            report_download_button = self.page.locator(
                'a[href*="takeout/download"]:not(div[data-download-uri] a)'
            ).first
            download_meta = await self.download_with_reauth(
                lambda: report_download_button.click(timeout=self.timeout)
            )
            current_archive_timestamp = parse_takeout_timestamp(
                download_meta.suggested_filename.split("-", 3)[1]
            )
            await download_meta.cancel()
            if (
                not self.last_snapshot_timestamp
                or current_archive_timestamp > self.last_snapshot_timestamp
            ):
                self.target_archive = ready_archive_link
                self.target_archive_timestamp = current_archive_timestamp
                return

    async def clean_downloads_dir(self):
        for f in downloads_path.iterdir():
            if f.is_file():
                f.unlink()
            elif f.is_dir():
                shutil.rmtree(f)
        self.target_archive_download_path = downloads_path.joinpath(
            self.target_archive.split("/")[-1]
        )
        self.target_archive_download_path.mkdir()

    async def navigate_to_archive(self):
        await self.page.goto(f"{TAKEOUT_BASEURL}{self.target_archive}")
        await self.ensure_auth()
        self.archive_parts = await self.page.locator(
            'div[data-download-uri] a[href*="takeout/download"]'
        ).all()

    async def handle_archive_page(self):
        await self.navigate_to_archive()

    async def download_archive_parts(self):
        for i, archive_part in enumerate(self.archive_parts, 1):
            download_meta = await self.download_with_reauth(
                lambda: archive_part.click(timeout=self.timeout)
            )
            for try_n in range(1, 4):
                try:
                    await download_meta.save_as(
                        self.target_archive_download_path.joinpath(
                            download_meta.suggested_filename
                        )
                    )
                    break
                except Error:
                    if try_n >= 3:
                        raise
                    print(f"retrying download {i} after {try_n}")
            await download_meta.delete()
            print(f"downloaded {i}/{len(self.archive_parts)} parts")

    async def request_new_archive(self):
        await self.page.goto(f"{TAKEOUT_BASEURL}settings/takeout/custom/photos")
        await self.page.locator('div[data-jobid] button[aria-label]').click(timeout=self.timeout)
        await self.page.locator('div[data-configure-step] button').click(timeout=self.timeout)

    @name_enricher(add_transitions(
        transition(source=TakeoutStates.on_manage, dest=TakeoutStates.export_in_progress, conditions=ref(is_export_running)),
        transition(source=TakeoutStates.on_manage, dest=TakeoutStates.backup_fresh, conditions=ref(is_backup_fresh)),
        transition(source=TakeoutStates.on_manage, dest=TakeoutStates.selecting_archive, conditions=ref(has_ready_archive_links)),
        transition(source=TakeoutStates.on_manage, dest=TakeoutStates.requesting_archive),
    ))
    async def assess_manage_page(self): ...

    @name_enricher(add_transitions(transition(
        source=TakeoutStates.start,
        dest=TakeoutStates.on_manage,
        before=ref(navigate_to_manage),
        after=ref(assess_manage_page),
    )))
    async def run(self): ...

    @name_enricher(add_transitions(
        transition(source=TakeoutStates.selecting_archive, dest=TakeoutStates.on_archive, conditions=ref(has_newer_archive), after=ref(clean_downloads_dir)),
        transition(source=TakeoutStates.selecting_archive, dest=TakeoutStates.requesting_archive),
    ))
    async def evaluate_archives(self): ...

    @name_enricher(add_transitions(transition(
        source=TakeoutStates.complete,
        dest=TakeoutStates.complete,
    )))
    async def finish_download(self): ...


@with_model_definitions
class TakeoutMachine(AsyncMachine):
    pass


def parse_takeout_timestamp(val):
    return datetime.datetime.strptime(val, "%Y%m%dT%H%M%SZ")


def encode_takeout_timestamp(val):
    return val.strftime("%Y%m%dT%H%M%SZ")


async def main():
    print(f"{TIMEOUT_MILLIS=}")
    if not auth_json_path:
        raise Exception(f"{auth_json_path} is required")
    if not os.getenv("ENCODED_PASS"):
        raise Exception("ENCODED_PASS env is required")

    last_snapshot_timestamp = None
    if timestamp_path.exists():
        last_snapshot_timestamp = parse_takeout_timestamp(timestamp_path.read_text())

    print("inited config")
    async with async_playwright() as playwright:
        # Read fingerprint settings
        fp_settings_path = pathlib.Path(".fp_settings.json")
        fp_settings = {}
        if fp_settings_path.exists():
            fp_settings = json.loads(fp_settings_path.read_text())
        
        async with await playwright.firefox.connect(
            os.getenv("BROWSER_SERVER_URL", f"ws://host.docker.internal:8082/srv"),
            timeout=TIMEOUT_MILLIS,
        ) as browser:
            print("inited browser")
            try:
                page = await asyncio.wait_for(
                    browser.new_page(
                        storage_state={"encoded_value": auth_json_path.read_text()},
                        accept_downloads=True,
                        **fp_settings,
                    ),
                    timeout=TIMEOUT_MILLIS / 1000,
                )
            except asyncio.TimeoutError:
                raise RuntimeError(
                    f"browser.new_page() timed out after {TIMEOUT_MILLIS}ms. "
                    f"Check that proxy and browser server are running and BROWSER_SERVER_URL={os.getenv('BROWSER_SERVER_URL', 'ws://host.docker.internal:8082/srv')}"
                )
            # FF150 about:newtab race condition: sleep 400ms after new_page
            await asyncio.sleep(0.4)
            page.set_default_timeout(TIMEOUT_MILLIS)
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
                model = TakeoutModel(page, last_snapshot_timestamp)
                TakeoutMachine(model, states=TakeoutStates.as_list(), initial=TakeoutStates.start.name, queued=True, prepare_event="ensure_auth")

                try:
                    await model.run()
                    state = await page.context.storage_state()
                    auth_json_path.write_text(state["encoded_value"])
                    print(f"completed with state: {model.state}")
                except Exception as e:
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

    if model.state == TakeoutStates.complete.name and hasattr(model, 'target_archive_download_path'):
        for f in model.target_archive_download_path.glob("*.zip"):
            with zipfile.ZipFile(f, "r") as archive:
                archive.extractall(model.target_archive_download_path)
        print("unpacked archives")

        unpacked_root_dir = [item for item in model.target_archive_download_path.iterdir() if item.is_dir()][0]
        renamed_folders = []
        for root, dirs, files in unpacked_root_dir.walk():
            for path in dirs:
                folder_path = pathlib.Path(root.joinpath(path))
                if m := re.match(text_labels["year.folder.template"], folder_path.stem):
                    new_path = folder_path.parent.joinpath(f"Photos from {m.group(1)}")
                    renamed_folders.append((folder_path, new_path))
                    folder_path.rename(new_path)

        print(f"renamed folders: {renamed_folders}")

        processed_photos_path = model.target_archive_download_path.joinpath("export")
        try:
            subprocess.run(
                [
                    "/app/utils/gpth",
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
        shutil.rmtree(model.target_archive_download_path)
        timestamp_path.write_text(encode_takeout_timestamp(model.target_archive_timestamp))
        print(f"successfully backed up up to {model.target_archive_timestamp}")


if __name__ == "__main__":
    asyncio.run(main())
