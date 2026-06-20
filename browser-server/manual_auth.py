import asyncio
import logging
import os
import pathlib
import random
import re
import sys
import tempfile
from enum import Enum

from invisible_playwright.async_api import InvisiblePlaywright
from transitions.experimental.utils import with_model_definitions, add_transitions, transition
from transitions.extensions.asyncio import AsyncMachine

logging.basicConfig(level=logging.INFO)
# Set transitions' log level to INFO; DEBUG messages will be omitted
logging.getLogger('transitions').setLevel(logging.INFO)

def ref(func):
    """IDE-navigable method reference. Extracts __name__ for transitions library."""
    return func.__name__
from playwright.async_api import expect, Locator

downloads_path = pathlib.Path("./browser-downloads")
default_timeout = float(os.getenv("TIMEOUT_MILLIS", "30000"))


class LoginState(str, Enum):
    start = "start"
    email_entry = "email_entry"
    password_entry = "password_entry"
    password_submitted = "password_submitted"
    challenge_skotp = "challenge_skotp"
    challenge_select = "challenge_select"
    auth_success = "auth_success"


class GoogleLoginModel:
    state: LoginState = LoginState.start

    def __init__(self, page, timeout):
        self.page = page
        self.timeout = timeout

    @property
    def email_input(self) -> Locator:
        return self.page.locator("input[type=email]").or_(self.page.locator("input#identifierId"))

    @property
    def challenge_option(self) -> Locator:
        return self.page.locator('div[data-challengetype="39"]')

    @property
    def default_mfa_input(self) -> Locator:
        return self.page.locator("input#securityKeyOtpInputId")

    @add_transitions(transition(
        source=LoginState.start,
        dest=LoginState.email_entry,
        before="verify_signin",
        after="submit_email",
    ))
    async def sign_in(self): ...

    @add_transitions(transition(
        source=LoginState.email_entry,
        dest=LoginState.password_entry,
        before="fill_email_and_proceed",
        after="submit_password",
    ))
    async def submit_email(self): ...

    @add_transitions(transition(
        source=LoginState.password_entry,
        dest=LoginState.password_submitted,
        before="fill_password_and_proceed",
        after="route_after_password",
    ))
    async def submit_password(self): ...

    @add_transitions(
        transition(source=LoginState.password_submitted, dest=LoginState.challenge_skotp, conditions="is_skotp", before="verify_skotp_and_click_try_another_way", after="skip_skotp"),
        transition(source=LoginState.password_submitted, dest=LoginState.challenge_select, conditions="is_challenge_url", before="verify_challenge_and_select_acceptable_mfa", after="select_google_prompt"),
        transition(source=LoginState.password_submitted, dest=LoginState.auth_success, conditions="is_takeout_url"),
    )
    async def route_after_password(self): ...

    @add_transitions(transition(
        source=LoginState.challenge_skotp,
        dest=LoginState.challenge_select,
        before="verify_skotp_and_click_try_another_way",
        after="wait_for_mfa_confirmation",
    ))
    async def skip_skotp(self): ...

    @add_transitions(transition(
        source=LoginState.challenge_select,
        dest=LoginState.auth_success,
        before="choose_acceptable_mfa",
    ))
    async def wait_for_mfa_confirmation(self): ...

    async def is_skotp(self):
        return await self.default_mfa_input.is_visible(timeout=self.timeout)

    def is_challenge_url(self):
        return self.page.url.startswith("https://accounts.google.com/v3/signin/challenge")

    def is_takeout_url(self):
        return self.page.url.startswith("https://takeout.google.com")

    async def verify_signin(self):
        email_input = self.email_input
        await expect(email_input).to_be_visible(timeout=self.timeout)

    async def fill_email_and_proceed(self):
        email_input = self.email_input
        await expect(email_input).to_have_count(1, timeout=self.timeout)
        await expect(email_input).to_be_visible(timeout=self.timeout)
        email = os.getenv("USER_E")
        await email_input.focus()
        await email_input.press_sequentially(email, delay=random.randint(11, 49))
        await self.page.wait_for_timeout(random.randint(666, 973))
        await self.page.locator("button#identifierNext").or_(
            self.page.locator("div#identifierNext")
        ).click(timeout=self.timeout)

    async def fill_password_and_proceed(self):
        password_input = self.page.locator("input[type=password]:visible")
        await expect(password_input).to_have_count(1, timeout=self.timeout)
        await expect(password_input).to_be_visible(timeout=self.timeout)
        password = os.getenv("USER_P")
        await password_input.focus()
        await password_input.type(password, delay=random.randint(11, 49))
        await self.page.wait_for_timeout(random.randint(666, 973))
        await self.page.locator("button#passwordNext").or_(
            self.page.locator("div#passwordNext")
        ).click(timeout=self.timeout)

    async def verify_skotp_and_click_try_another_way(self):
        await expect(self.default_mfa_input).to_be_visible(timeout=self.timeout)
        button_panel = self.page.locator("[data-secondary-action-label]")
        await expect(button_panel).to_be_visible(timeout=self.timeout)
        target_button_text = await button_panel.get_attribute("data-secondary-action-label")
        await self.page.get_by_text(target_button_text).click(timeout=self.timeout)

    async def verify_challenge_and_select_acceptable_mfa(self):
        challenge_select = self.challenge_option
        await expect(challenge_select).to_be_visible(timeout=self.timeout)

    async def choose_acceptable_mfa(self):
        challenge_button = self.challenge_option
        await expect(challenge_button).to_be_visible(timeout=self.timeout)
        await challenge_button.click(timeout=self.timeout)

    async def verify_takeout(self):
        await expect(self.page).to_have_url(re.compile(r"takeout\.google\.com"))

    async def on_enter_auth_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = pathlib.Path(tmp)
            file_path = tmp_dir.joinpath("file.json")
            await self.page.context.storage_state(path=file_path)
            print()
            print(file_path.read_text())
            print()
            manual_auth_wait.pop()


@with_model_definitions
class GoogleLoginMachine(AsyncMachine):
    pass


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
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = pathlib.Path(tmp)
                file_path = tmp_dir.joinpath("file.json")
                await page.context.storage_state(path=file_path)
                print()
                print(file_path.read_text())
                print()
                manual_auth_wait.pop()

        page.on("close", handle_manual_auth_close)

        try:
            await page.goto("https://takeout.google.com/settings/takeout/custom/photos")
            if True:
                print(f"{headless_mode=}: executing automatic login script")
                if page.url.startswith("https://accounts.google.com/v3/signin"):
                    model = GoogleLoginModel(page=page, timeout=default_timeout)
                    GoogleLoginMachine(model, states=LoginState, initial=LoginState.start, queued=True)
                    await model.sign_in()
                elif page.url.startswith("https://takeout.google.com"):
                    await handle_manual_auth_close(page)
            else:
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
