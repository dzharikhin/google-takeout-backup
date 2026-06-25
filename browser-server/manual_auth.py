import asyncio
import json
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

# Import invisible_playwright helpers for fingerprint settings
from invisible_playwright.config import get_default_stealth_prefs
from invisible_playwright.launcher import _CHROME_W, _CHROME_H, _TASKBAR_H


async def main():
    print(f"display: {os.getenv("DISPLAY")}")
    global manual_auth_wait
    manual_auth_wait = [1]

    headless_mode = os.getenv("HEADLESS_MODE", "virtual")
    headed = bool(headless_mode.lower() == "headed")
    print(f"{headless_mode=}")
    async with InvisiblePlaywright() as browser:
        page = await browser.new_page()
        page.set_default_timeout(default_timeout)

        async def handle_manual_auth_close(page):
            global manual_auth_wait
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_dir = pathlib.Path(tmp)
                    
                    # Save storage_state
                    file_path = tmp_dir.joinpath("file.json")
                    await page.context.storage_state(path=file_path)
                    print()
                    print(file_path.read_text())
                    print()
                    
                    # Save fingerprint settings for backup
                    # The browser variable is still in scope here (inside async with)
                    if browser and hasattr(browser, '_profile'):
                        profile = browser._profile
                        fp_settings = {
                            "viewport": {
                                "width": profile.screen.width - _CHROME_W,
                                "height": profile.screen.height - _TASKBAR_H - _CHROME_H,
                            },
                            "screen": {
                                "width": profile.screen.width,
                                "height": profile.screen.height,
                            },
                            "device_scale_factor": profile.screen.dpr,
                            "color_scheme": "dark" if profile.dark_theme else "light",
                            "timezone_id": browser._timezone if hasattr(browser, '_timezone') else "",
                            "locale": browser._locale if hasattr(browser, '_locale') else "en-US",
                            "humanize": browser._humanize if hasattr(browser, '_humanize') else True,
                            "seed": profile.seed if hasattr(profile, 'seed') else 42,
                        }
                        
                        # Save full prefs for reference
                        prefs = get_default_stealth_prefs(
                            seed=profile.seed if hasattr(profile, 'seed') else 42,
                            locale=browser._locale if hasattr(browser, '_locale') else "en-US",
                            timezone=browser._timezone if hasattr(browser, '_timezone') else "",
                            humanize=browser._humanize if hasattr(browser, '_humanize') else True,
                        )
                        prefs_path = downloads_path / ".fp_prefs.json"
                        prefs_path.write_text(json.dumps(prefs, indent=2))
                        print(f"Saved Firefox prefs to {prefs_path} ({len(prefs)} keys)")
                    else:
                        # Fallback: use default values
                        fp_settings = {
                            "viewport": {"width": 1906, "height": 949},
                            "screen": {"width": 1920, "height": 1080},
                            "device_scale_factor": 1.0,
                            "color_scheme": "light",
                            "timezone_id": "",
                            "locale": "en-US",
                            "humanize": True,
                            "seed": 42,
                        }
                    
                    settings_path = downloads_path / ".fp_settings.json"
                    settings_path.write_text(json.dumps(fp_settings, indent=2))
                    print(f"Saved fingerprint settings to {settings_path}")
            finally:
                manual_auth_wait.pop()

        try:
            await page.goto(TAKEOUT_URL)
            if headed:
                page.on("close", handle_manual_auth_close)
                print(f"{headless_mode=}: expecting manual execution. Just close browser window when auth is successful")
            else:
                print(f"{headless_mode=}: executing automatic login script")
                if page.url.startswith("https://accounts.google.com/v3/signin"):
                    model = GoogleLoginModel(page=page, timeout=default_timeout)
                    GoogleLoginMachine(model, states=States.as_list(), initial=States.start, queued=True)
                    await model.sign_in()
                    print("login script is finished")
                if page.url.startswith(TAKEOUT_URL):
                    await handle_manual_auth_close(page)
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
