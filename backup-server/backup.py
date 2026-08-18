import base64
import csv
import datetime
import hashlib
import json
import logging
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import sys
import time
import zipfile

import urllib3.exceptions
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from transitions import Event, Machine, State
from transitions.experimental.utils import add_transitions, transition, with_model_definitions

from auth import TAKEOUT_BASEURL, GoogleLoginMachine, GoogleLoginModel, States
from cookies import sanitize_cookies


class _SuppressSkipBinding(logging.Filter):
    def filter(self, record):
        return "Skip binding of" not in record.getMessage()


class QuotaExceededError(Exception):
    pass


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
    force=True,
)
transitions_logger = logging.getLogger("transitions.core")
transitions_logger.setLevel(logging.DEBUG)
transitions_logger.addFilter(_SuppressSkipBinding())

BACKUP_FRESHNESS_INTERVAL = datetime.timedelta(hours=int(os.getenv("BACKUP_FRESHNESS_THRESHOLD_HOURS", "12")))
TIMEOUT_MILLIS = int(os.getenv("TIMEOUT_MILLIS") or "30000")
DOWNLOAD_TIMEOUT_MILLIS = int(os.getenv("DOWNLOAD_TIMEOUT_MILLIS") or "3_600_000")
DOWNLOAD_ATTEMPTS = int(os.getenv("DOWNLOAD_ATTEMPTS") or "5")

auth_json_path = pathlib.Path(".auth_encoded")
downloads_path = pathlib.Path("downloads")
backup_path = pathlib.Path("photos")
timestamp_path = backup_path.joinpath(".timestamp")
text_labels_source = pathlib.Path(f"keys_{os.getenv('GOOGLE_LANG', 'RU')}.csv")

with text_labels_source.open(mode="rt") as labels_data:
    text_labels = {row[0]: row[1] for row in csv.reader(labels_data, delimiter="=")}


def archive_url(link):
    return link if link.startswith("http") else f"{TAKEOUT_BASEURL}{link.lstrip('/')}"


def parse_part_index(href):
    m = re.search(r"[?&]i=(\d+)", href or "")
    return int(m.group(1)) if m else None


class CheckingEvent(Event):
    def _trigger(self, event_data):
        result = super()._trigger(event_data)
        if not result and event_data.error is None:
            raise RuntimeError(f"No conditions matched in state '{event_data.model.state}' for event '{self.name}'")
        return result


def name_enricher(outer):
    def _wrapper(func):
        original_name = func.__name__
        result = outer(func)
        result.original_name = original_name
        return result

    return _wrapper


def ref(func):
    return getattr(func, "original_name", None) or func.__name__


class TakeoutStates:
    on_manage = State(name="on_manage")
    export_in_progress = State(name="export_in_progress", final=True)
    backup_fresh = State(name="backup_fresh", final=True)
    selecting_archive = State(name="selecting_archive", on_enter="find_most_recent_archive")
    requesting_archive = State(name="requesting_archive", on_enter="request_new_archive", final=True)
    on_archive = State(name="on_archive", on_enter="handle_archive_page")
    downloading = State(name="downloading", on_enter="download_archive_parts")
    complete = State(name="complete", final=True)

    @classmethod
    def as_list(cls):
        return [v for k, v in vars(cls).items() if isinstance(v, State)]


class TakeoutModel:
    def __init__(self, driver: webdriver.Remote, last_snapshot_timestamp):
        self.driver = driver
        self.timeout = TIMEOUT_MILLIS
        self._timeout_s = TIMEOUT_MILLIS / 1000
        self.last_snapshot_timestamp = last_snapshot_timestamp
        self.target_archive = None
        self.target_archive_timestamp = None
        self.target_archive_download_path = None
        self.archive_links = None
        self.archive_parts = None
        self.state = "start"

    def is_export_running(self):
        elements = self.driver.find_elements(By.CSS_SELECTOR, "button[data-job-id]")
        return any(e.is_displayed() for e in elements)

    def is_backup_fresh(self):
        if not self.last_snapshot_timestamp:
            return False
        now = datetime.datetime.now()
        return abs(now - self.last_snapshot_timestamp) < BACKUP_FRESHNESS_INTERVAL

    def has_archive_links(self):
        try:
            WebDriverWait(self.driver, self._timeout_s).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, 'a[href*="/manage/archive/"]')
            )
        except TimeoutException:
            logging.warning("no archive links appeared after wait; treating as none")
        links = self.driver.find_elements(By.CSS_SELECTOR, 'a[href*="/manage/archive/"]')
        hrefs = [link.get_attribute("href") for link in links]
        self.archive_links = [h for h in hrefs if h]
        return len(self.archive_links) > 0

    def has_newer_archive(self):
        return self.target_archive is not None

    def ensure_auth(self):
        auth_model = GoogleLoginModel(
            driver=self.driver,
            timeout=self.timeout,
            email_env="IF_YOU_NEED_THIS_HERE_IT_IS_BAD",
            password_env="ENCODED_PASS",
        )
        machine = GoogleLoginMachine(
            auth_model,
            states=States.as_list(),
            initial=States.start,
            queued=True,
        )
        auth_model.sign_in()
        machine.ensure_auth()

    def _assert_grid_downloads_empty(self):
        remaining = self.driver.get_downloadable_files()
        if remaining:
            raise RuntimeError(f"grid download dir not empty before download: {remaining}")

    def _clear_grid_downloads(self):
        self.driver.delete_downloadable_files()
        remaining = self.driver.get_downloadable_files()
        if remaining:
            raise RuntimeError(f"grid download dir not empty after delete_downloadable_files(): {remaining}")

    def _quota_dialog(self):
        elements = self.driver.find_elements(By.CSS_SELECTOR, 'div[role="dialog"][aria-modal="true"]')
        return next((e for e in elements if e.is_displayed()), None)

    def _dismiss_quota_dialog(self):
        dialog = self._quota_dialog()
        if dialog:
            button = dialog.find_element(By.CSS_SELECTOR, '[data-mdc-dialog-action="ok"]')
            button.click()

    def download_with_reauth(self, element):
        self._assert_grid_downloads_empty()
        self.driver.execute_script("arguments[0].click();", element)
        deadline = time.monotonic() + DOWNLOAD_TIMEOUT_MILLIS / 1000
        while time.monotonic() < deadline:
            try:
                downloadable = self.driver.get_downloadable_files()
            except Exception as e:
                logging.debug(f"download attempt failed: {e}")
                downloadable = []
            finished = [f for f in downloadable if not f.endswith(".part")]
            in_progress = any(f.endswith(".part") for f in downloadable)
            if finished and not in_progress:
                path = self._take_completed_download(finished)
                if path:
                    self._clear_grid_downloads()
                    return path

            current_url = self.driver.current_url
            if "accounts.google." in current_url:
                logging.info(f"Detected auth redirect to {current_url}, triggering reauth")
                self.ensure_auth()
                continue

            if self._quota_dialog():
                self._dismiss_quota_dialog()
                raise QuotaExceededError("archive download quota exceeded")

            time.sleep(2)

        raise TimeoutError("Download failed")

    def _stream_download(self, name):
        """Stream-download a file from the Grid file-stream plugin (port 4445)
        with PSK-authenticated AES-256-GCM decryption. Replaces driver.download_file()."""
        nonce = secrets.token_bytes(16)
        psk_hex = os.environ.get("FILE_STREAM_KEY", "")
        if not psk_hex:
            raise RuntimeError("FILE_STREAM_KEY environment variable is required for streaming downloads")

        psk = bytes.fromhex(psk_hex)

        browser_url = os.getenv("BROWSER_SERVER_URL", "http://localhost:4444")
        stream_base = os.getenv("FILE_STREAM_URL", browser_url.replace(":4444", ":4445"))
        url = f"{stream_base}/download/{self.driver.session_id}/{name}"

        http = urllib3.PoolManager()
        resp = http.request("GET", url, headers={"X-Stream-Nonce": base64.b64encode(nonce)}, preload_content=False)
        if resp.status != 200:
            raise RuntimeError(f"stream download failed: HTTP {resp.status} {resp.data.decode()}")

        client_nonce = base64.b64decode(resp.headers["X-Stream-Nonce"])
        if len(client_nonce) != 16:
            raise RuntimeError(f"invalid client nonce length: {len(client_nonce)}")

        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=client_nonce, info=b"takeout-file-stream-v1").derive(psk)
        aesgcm = AESGCM(key)

        plain_length = int(resp.headers["X-Plain-Length"])
        dest = downloads_path.joinpath(name)

        counter = 0
        total = 0
        with open(dest, "wb") as f:
            while total < plain_length:
                frame_len_bytes = resp.read(4)
                frame_len = int.from_bytes(frame_len_bytes, "big")
                blob = resp.read(frame_len)
                nonce12 = counter.to_bytes(12, "big")
                try:
                    plaintext = aesgcm.decrypt(nonce12, blob, None)
                except InvalidTag:
                    raise RuntimeError(f"GCM tag verification failed for {name}")
                f.write(plaintext)
                total += len(plaintext)
                counter += 1

        resp.release_conn()
        if total != plain_length:
            raise RuntimeError(f"stream download truncated: expected {plain_length}, got {total}")
        return dest

    def _take_completed_download(self, names):
        non_empty = []
        for name in names:
            max_retries = 3
            base_delay = 2
            for attempt in range(max_retries):
                try:
                    self._stream_download(name)
                    break
                except (
                    urllib3.exceptions.ProtocolError,
                    ConnectionResetError,
                    ConnectionAbortedError,
                    OSError,
                    InvalidTag,
                ) as e:
                    if attempt == max_retries - 1:
                        raise RuntimeError(f"failed to download file {name} after {max_retries} attempts: {e}")
                    delay = base_delay * (2**attempt)
                    logging.warning(f"download attempt {attempt + 1} failed for {name}, retrying in {delay}s: {e}")
                    time.sleep(delay)
            candidate = downloads_path.joinpath(name)
            if candidate.exists() and candidate.stat().st_size > 0:
                non_empty.append(candidate)
            else:
                candidate.unlink(missing_ok=True)
        if len(non_empty) > 1:
            raise RuntimeError(f"expected exactly 1 finished download, got {len(non_empty)}: {non_empty}")
        return non_empty[0] if non_empty else None

    def find_most_recent_archive(self):
        for archive_link in self.archive_links:
            self.driver.get(archive_url(archive_link))
            self.ensure_auth()
            buttons = self.driver.find_elements(
                By.CSS_SELECTOR, 'a[href*="takeout/download"]:not(div[data-download-uri] a)'
            )
            if not buttons:
                logging.info(f"skipping non-downloadable archive (expired): {archive_link}")
                continue
            path = self.download_with_reauth(buttons[0])
            current_archive_timestamp = parse_takeout_timestamp(path.name.split("-", 3)[1])
            logging.info(f"current archive timestamp: {current_archive_timestamp}")
            if not self.last_snapshot_timestamp or current_archive_timestamp > self.last_snapshot_timestamp:
                self.target_archive = archive_link
                self.target_archive_timestamp = current_archive_timestamp
                return

    def clean_downloads_dir(self):
        for f in downloads_path.iterdir():
            if f.is_file():
                f.unlink()
            elif f.is_dir():
                shutil.rmtree(f)
        self.target_archive_download_path = downloads_path.joinpath(self.target_archive.split("/")[-1])
        self.target_archive_download_path.mkdir()

    def navigate_to_archive(self):
        self.driver.get(archive_url(self.target_archive))
        self.ensure_auth()
        elements = self.driver.find_elements(By.CSS_SELECTOR, 'div[data-download-uri] a[href*="takeout/download"]')
        self.archive_parts = [parse_part_index(el.get_attribute("href")) for el in elements]
        return self.archive_parts

    def handle_archive_page(self):
        self.navigate_to_archive()

    def download_archive_parts(self):
        total = len(self.archive_parts)
        for i, target_index in enumerate(self.archive_parts, 1):

            def match_part(d):
                for link in d.find_elements(By.CSS_SELECTOR, 'div[data-download-uri] a[href*="takeout/download"]'):
                    if parse_part_index(link.get_attribute("href")) == target_index:
                        return link
                return None

            for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
                try:
                    part = WebDriverWait(self.driver, self._timeout_s).until(match_part)
                    path = self.download_with_reauth(part)
                except QuotaExceededError:
                    self.quota_exceeded()
                    return
                except (TimeoutError, TimeoutException) as e:
                    if attempt == DOWNLOAD_ATTEMPTS:
                        raise
                    logging.warning(
                        f"part {target_index} download attempt {attempt}/{DOWNLOAD_ATTEMPTS} failed: {e}; retrying"
                    )
                    time.sleep(30)
                    try:
                        self._clear_grid_downloads()
                    except Exception as clear_error:
                        logging.warning(f"failed to clear grid downloads: {clear_error}")
                    self.navigate_to_archive()
                else:
                    break
            for try_n in range(1, 4):
                try:
                    path.rename(self.target_archive_download_path.joinpath(path.name))
                    break
                except Exception:
                    if try_n >= 3:
                        raise
                    logging.info(f"retrying download {i} after {try_n}")
            logging.info(f"downloaded {i}/{total} parts")

    def request_new_archive(self):
        self.driver.get(f"{TAKEOUT_BASEURL}settings/takeout/custom/photos")
        WebDriverWait(self.driver, self._timeout_s).until(
            lambda d: next(
                (
                    b
                    for b in d.find_elements(
                        By.CSS_SELECTOR,
                        "div[data-jobid] > div:nth-of-type(2) button[aria-label]",
                    )
                    if b.is_displayed() and b.is_enabled()
                ),
                None,
            )
        ).click()
        WebDriverWait(self.driver, self._timeout_s).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "div[data-configure-step] button"))
        ).click()

    @name_enricher(
        add_transitions(
            transition(
                source=TakeoutStates.downloading,
                dest=TakeoutStates.complete,
            )
        )
    )
    def finish_download(self): ...

    @name_enricher(
        add_transitions(
            transition(
                source=TakeoutStates.on_archive,
                dest=TakeoutStates.downloading,
                before=ref(clean_downloads_dir),
                after=ref(finish_download),
            )
        )
    )
    def start_download(self): ...

    @name_enricher(
        add_transitions(
            transition(
                source=TakeoutStates.selecting_archive,
                dest=TakeoutStates.on_archive,
                conditions=ref(has_newer_archive),
                after=ref(start_download),
            ),
            transition(source=TakeoutStates.selecting_archive, dest=TakeoutStates.requesting_archive),
        )
    )
    def select_archive(self): ...

    @name_enricher(
        add_transitions(
            transition(
                source=TakeoutStates.downloading,
                dest=TakeoutStates.requesting_archive,
            )
        )
    )
    def quota_exceeded(self): ...

    @name_enricher(
        add_transitions(
            transition(
                source=TakeoutStates.on_manage, dest=TakeoutStates.export_in_progress, conditions=ref(is_export_running)
            ),
            transition(
                source=TakeoutStates.on_manage, dest=TakeoutStates.backup_fresh, conditions=ref(is_backup_fresh)
            ),
            transition(
                source=TakeoutStates.on_manage,
                dest=TakeoutStates.selecting_archive,
                conditions=ref(has_archive_links),
                after=ref(select_archive),
            ),
            transition(source=TakeoutStates.on_manage, dest=TakeoutStates.requesting_archive),
        )
    )
    def assess_manage_page(self): ...


@with_model_definitions
class TakeoutMachine(Machine):
    event_cls = CheckingEvent


def parse_takeout_timestamp(val):
    return datetime.datetime.strptime(val, "%Y%m%dT%H%M%SZ")


def encode_takeout_timestamp(val):
    return val.strftime("%Y%m%dT%H%M%SZ")


def merge_into_backup(src_root: pathlib.Path, dst_root: pathlib.Path) -> None:
    copied = 0
    skipped = 0
    renamed = 0

    for file in src_root.rglob("*"):
        if not file.is_file():
            continue

        rel = file.relative_to(src_root)
        target = dst_root / rel

        target.parent.mkdir(parents=True, exist_ok=True)

        if not target.exists():
            shutil.copy2(file, target)
            copied += 1
            continue

        src_size = file.stat().st_size
        dst_size = target.stat().st_size

        if src_size != dst_size:
            dest = _find_available_path(target)
            shutil.copy2(file, dest)
            renamed += 1
            continue

        src_hash = _file_sha256(file)
        dst_hash = _file_sha256(target)

        if src_hash == dst_hash:
            skipped += 1
            continue

        dest = _find_available_path(target)
        shutil.copy2(file, dest)
        renamed += 1

    logging.info(f"merge complete: {copied} copied, {skipped} skipped, {renamed} renamed")


def _file_sha256(path: pathlib.Path) -> str:
    sha256 = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _find_available_path(path: pathlib.Path) -> pathlib.Path:
    stem = path.stem
    suffix = path.suffix
    counter = 1
    while True:
        candidate = path.parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def load_auth_cookies(auth_json_path):
    auth_data = json.loads(auth_json_path.read_text())
    if isinstance(auth_data, list):
        return sanitize_cookies(auth_data)
    return sanitize_cookies(auth_data.get("cookies", []))


def set_cookies_for_domains(driver, cookies):
    driver.get("https://www.google.com/")
    driver.delete_all_cookies()
    for cookie in cookies:
        driver.add_cookie(cookie)


def main():
    logging.info(f"{TIMEOUT_MILLIS=}")
    if not auth_json_path.exists():
        raise Exception(f"{auth_json_path} is required")
    if not os.getenv("ENCODED_PASS"):
        raise Exception("ENCODED_PASS env is required")

    last_snapshot_timestamp = None
    if timestamp_path.exists():
        last_snapshot_timestamp = parse_takeout_timestamp(timestamp_path.read_text())

    logging.info("inited config")

    options = Options()
    options.set_capability("se:downloadsEnabled", True)
    options.set_capability("webSocketUrl", True)

    driver = webdriver.Remote(
        command_executor=os.getenv("BROWSER_SERVER_URL", "http://localhost:4444"),
        options=options,
    )

    try:
        cookies = load_auth_cookies(auth_json_path)

        set_cookies_for_domains(driver, cookies)

        if os.getenv("DEBUG_BROWSER_COOKIES", "false").lower() == "true":
            try:
                browser_cookies = driver.execute_script("return document.cookie")
                logging.info(f"document.cookie after add: {browser_cookies}")
                stored = [c["name"] for c in driver.get_cookies()]
                logging.info(f"get_cookies names after add ({len(stored)}): {stored}")
            except Exception as e:
                logging.warning(f"failed to read cookies: {e}")

        driver.get(f"{TAKEOUT_BASEURL}manage")
        model = TakeoutModel(driver, last_snapshot_timestamp)
        TakeoutMachine(
            model,
            states=TakeoutStates.as_list(),
            initial=TakeoutStates.on_manage,
            queued=True,
            prepare_event=ref(TakeoutModel.ensure_auth),
        )

        try:
            model.assess_manage_page()

            cookies = driver.get_cookies()
            auth_json_path.write_text(json.dumps({"cookies": sanitize_cookies(cookies)}))
            logging.info(f"completed with state: {model.state}")
        except Exception:
            try:
                if driver and not driver.current_url.startswith("about:"):
                    now = datetime.datetime.now()
                    encoded_timestamp = encode_takeout_timestamp(now)
                    downloads_path.joinpath(f"{encoded_timestamp}.url").write_text(driver.current_url)
                    downloads_path.joinpath(f"{encoded_timestamp}.html").write_text(driver.page_source)
                    driver.save_screenshot(downloads_path.joinpath(f"{encoded_timestamp}.png"))
            except Exception as e:
                logging.error(f"failed to collect diagnostic info with {e}, ignoring")
            raise
    finally:
        try:
            driver.quit()
        except Exception as e:
            logging.error(f"failed to quit driver: {e}")

    if model.state == TakeoutStates.complete.name and hasattr(model, "target_archive_download_path"):
        for f in model.target_archive_download_path.glob("*.zip"):
            with zipfile.ZipFile(f, "r") as archive:
                archive.extractall(model.target_archive_download_path)
        logging.info("unpacked archives")

        unpacked_root_dir = [item for item in model.target_archive_download_path.iterdir() if item.is_dir()][0]
        renamed_folders = []
        for root, dirs, files in unpacked_root_dir.walk():
            for path in dirs:
                folder_path = pathlib.Path(root.joinpath(path))
                if m := re.match(text_labels["year.folder.template"], folder_path.stem):
                    new_path = folder_path.parent.joinpath(f"Photos from {m.group(1)}")
                    renamed_folders.append((folder_path, new_path))
                    folder_path.rename(new_path)

        logging.info(f"renamed folders: {renamed_folders}")

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
            logging.error(f"Stderr: {e.stderr}")
            logging.error(f"Stdout: {e.stdout}")
            raise e

        all_photos_path = processed_photos_path.joinpath(os.getenv("GPTH_DEFAULT_FOLDER_NAME", "ALL_PHOTOS"))
        for f in all_photos_path.iterdir():
            shutil.move(f, processed_photos_path.joinpath(f.name))
        all_photos_path.rmdir()
        logging.info("processed archives")
        merge_into_backup(processed_photos_path, backup_path)
        shutil.rmtree(model.target_archive_download_path)
        timestamp_path.write_text(encode_takeout_timestamp(model.target_archive_timestamp))
        logging.info(f"successfully backed up up to {model.target_archive_timestamp}")


if __name__ == "__main__":
    main()
