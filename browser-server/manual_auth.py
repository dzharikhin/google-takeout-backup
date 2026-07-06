import json
import logging
import os
import pathlib
import sys
import threading
import time

from selenium import webdriver
from selenium.webdriver.firefox.options import Options

from auth import States, GoogleLoginModel, GoogleLoginMachine, TAKEOUT_BASEURL
from cookies import sanitize_cookies

logging.basicConfig(level=logging.INFO)
logging.getLogger("transitions.core").setLevel(logging.ERROR)

downloads_path = pathlib.Path("./browser-downloads")
default_timeout = float(os.getenv("TIMEOUT_MILLIS", "30000"))


def main():
    print(f"display: {os.getenv('DISPLAY')}")

    display_mode = os.getenv("DISPLAY_MODE", "virtual")
    print(f"{display_mode=}")

    options = Options()
    options.set_capability("se:downloadsEnabled", True)
    options.set_capability("webSocketUrl", True)

    driver = webdriver.Remote(
        command_executor="http://localhost:4444",
        options=options,
    )
    print("Session capabilities:", json.dumps(driver.capabilities, default=str))
    driver.set_script_timeout(default_timeout / 1000)
    driver.implicitly_wait(default_timeout / 1000)

    def wait_for_session_cookies():
        deadline = time.monotonic() + 30
        last_names = []
        while time.monotonic() < deadline:
            current = driver.get_cookies()
            last_names = [c["name"] for c in current]
            psidts = [n for n in last_names if "PSIDTS" in n]
            print(f"poll: {len(last_names)} cookies, PSIDTS present: {psidts}")
            if "__Secure-1PSIDTS" in last_names and "__Secure-3PSIDTS" in last_names:
                print("PSIDTS cookies present, capturing")
                return current
            time.sleep(2)
        print(f"PSIDTS did not appear within 30s; capturing {len(last_names)} cookies anyway")
        return driver.get_cookies()

    def handle_manual_auth_close(cookies=None):
        try:
            if cookies is None:
                cookies = wait_for_session_cookies()

            file_path = downloads_path.joinpath(".auth_encoded")
            data = {"cookies": sanitize_cookies(cookies)}
            file_path.write_text(json.dumps(data))

            print()
            print(f"auth state saved to {file_path}")
            print("copy browser-server/browser-downloads/.auth_encoded to backup-server/.auth_encoded")
            print()
        finally:
            driver.quit()

    try:
        driver.get(TAKEOUT_BASEURL)
        print(f"Current URL: {driver.current_url}")

        if "virtual" == display_mode:
            print(f"{display_mode=}: executing automatic login script")
            if driver.current_url.startswith("https://accounts.google.com/v3/signin"):
                model = GoogleLoginModel(driver=driver, timeout=default_timeout)
                machine = GoogleLoginMachine(model, states=States.as_list(), initial=States.start, queued=True)
                model.sign_in()
                machine.ensure_auth()
                print("login script is finished")

            if driver.current_url.startswith(TAKEOUT_BASEURL):
                handle_manual_auth_close()
                return
        else:
            print(f"{display_mode=}: log in in the visible window; cookies are saved once Takeout loads")
            print("(closing the browser before that aborts without saving)")

            try:
                my_context = driver.current_window_handle
                done = threading.Event()

                def on_load(data):
                    url = getattr(data, "url", None) or (data.get("url") if isinstance(data, dict) else "") or ""
                    if url.startswith(TAKEOUT_BASEURL):
                        print(f"BiDi load event for takeout URL: {url}")
                        done.set()

                driver.browsing_context.add_event_handler("load", on_load, contexts=[my_context])

                while not done.wait(1.0):
                    try:
                        _ = driver.current_url
                    except Exception:
                        print("Browser closed before reaching Takeout; aborting without saving.")
                        try:
                            driver.quit()
                        except Exception:
                            pass
                        return

                print("Reached Takeout — saving auth state...")
                try:
                    cookies = wait_for_session_cookies()
                except Exception as e:
                    print(f"Failed to fetch cookies; aborting. {e}", file=sys.stderr)
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    return
                handle_manual_auth_close(cookies=cookies)
            except Exception as e:
                print(f"BiDi unavailable (Grid may not expose webSocketUrl): {e}", file=sys.stderr)
                print("Falling back to polled URL detection (with bounded race).", file=sys.stderr)
                time.sleep(1)
                if not driver.current_url.startswith(TAKEOUT_BASEURL):
                    print("User navigated away from takeout, saving auth state...")
                    handle_manual_auth_close()
                    return
                else:
                    print("URL is takeout, saving...")
                    handle_manual_auth_close()
                    return

    except Exception:
        try:
            if driver and not driver.current_url.startswith("about:"):
                downloads_path.joinpath("error_url.txt").write_text(driver.current_url)
                downloads_path.joinpath("error_html.html").write_text(driver.page_source)
                driver.save_screenshot(downloads_path.joinpath("error_page_screenshot.png"))
        except Exception as e:
            print(f"failed to collect diagnostic info: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
