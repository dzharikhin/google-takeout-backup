import asyncio
import os
import random
from urllib.parse import parse_qs, urlparse

from playwright.async_api import expect, Locator, Page
from transitions.experimental.utils import with_model_definitions, add_transitions, transition
from transitions.extensions.asyncio import AsyncMachine, AsyncState

TAKEOUT_URL = "https://takeout.google.com/settings/takeout/custom/photos"


def name_enricher(outer):
    def _wrapper(func):
        original_name = func.__name__
        result = outer(func)
        result.original_name = original_name
        return result

    return _wrapper


def ref(func):
    return getattr(func, 'original_name', None) or func.__name__


class States:
    start = AsyncState(name="start")
    email_entry = AsyncState(name="email_entry", on_enter="fill_email_and_proceed")
    password_entry = AsyncState(name="password_entry", on_enter="fill_password_and_proceed")
    challenge_skotp = AsyncState(name="challenge_skotp")
    account_chooser = AsyncState(name="account_chooser", on_enter="select_account")
    other_challenge_select = AsyncState(name="other_challenge_select")
    challenge_confirm = AsyncState(name="challenge_confirm", on_enter="handle_challenge_confirm")
    offer_to_restore = AsyncState(name="offer_to_restore")
    address_entry = AsyncState(name="address_entry", on_enter="skip_address")
    auth_success = AsyncState(name="auth_success", final=True)

    @classmethod
    def as_list(cls):
        return [v for k, v in vars(cls).items() if isinstance(v, AsyncState)]


class GoogleLoginModel:
    def __init__(self, page: Page, timeout):
        self.page = page
        self.timeout = timeout
        self.state = "start"

    async def is_refresh_complete(self):
        progress_bar = self.page.locator('[role="progressbar"]')
        if await progress_bar.count() == 0:
            return
        await expect(progress_bar).to_have_attribute("aria-hidden", "true", timeout=self.timeout)

    @property
    def email_input(self) -> Locator:
        return self.page.locator("input[type=email]").or_(self.page.locator("input#identifierId"))

    @property
    def challenge_option(self) -> Locator:
        return self.page.locator('div[data-challengetype="39"]')

    @property
    def default_mfa_input(self) -> Locator:
        return self.page.locator("input#securityKeyOtpInputId")

    @property
    def account_chooser_item(self) -> Locator:
        return self.page.locator("div[data-button-type=multipleChoiceIdentifier]")

    async def is_skotp(self):
        await self.is_refresh_complete()
        return await self.default_mfa_input.is_visible(timeout=self.timeout)

    async def is_account_chooser(self):
        await self.is_refresh_complete()
        return await self.account_chooser_item.is_visible(timeout=self.timeout)

    async def is_mfa_selection(self):
        await self.is_refresh_complete()
        await self.challenge_option.is_visible(timeout=self.timeout)

    async def is_restore(self):
        await self.is_refresh_complete()
        await self.page.locator('[href^="https://myaccount.google.com/signinoptions/password"]').is_visible(timeout=self.timeout)

    async def is_address_entry(self):
        await self.is_refresh_complete()
        parsed = urlparse(self.page.url)
        return "gds.google.com/web/homeaddress" in parsed.path or parsed.path.endswith("/homeaddress")

    async def is_takeout_url(self):
        await self.page.wait_for_url(TAKEOUT_URL, timeout=self.timeout)
        return self.page.url.startswith(TAKEOUT_URL)

    async def is_signin(self):
        await self.is_refresh_complete()
        return await self.email_input.is_visible(timeout=self.timeout)

    async def select_account(self):
        await self.account_chooser_item.click(timeout=self.timeout)

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

    async def click_try_another_mfa(self):
        button_panel = self.page.locator("[data-secondary-action-label]")
        await expect(button_panel).to_be_visible(timeout=self.timeout)
        target_button_text = await button_panel.get_attribute("data-secondary-action-label")
        await self.page.get_by_text(target_button_text).click(timeout=self.timeout)

    async def click_acceptable_mfa(self):
        challenge_button = self.challenge_option
        await challenge_button.click(timeout=self.timeout)

    async def click_skip_restore(self):
        proceed_button = self.page.locator('[href^="https://takeout.google.com/settings/takeout/custom/photos"]')
        await proceed_button.click(timeout=self.timeout)

    async def skip_address(self):
        parsed = urlparse(self.page.url)
        params = parse_qs(parsed.query)
        continue_url = params.get("continue", [TAKEOUT_URL])[0]
        await self.page.goto(continue_url)

    async def handle_challenge_confirm(self):
        text = await self.page.locator("body").inner_text()
        print(text)
        await asyncio.wait({
            asyncio.create_task(self.is_restore()),
            asyncio.create_task(self.is_takeout_url())
        }, return_when=asyncio.FIRST_COMPLETED)
        await self.confirm_mfa()

    @name_enricher(add_transitions(
        transition(source=States.offer_to_restore, dest=States.address_entry, conditions=ref(is_address_entry), after=ref(skip_address)),
        transition(source=States.offer_to_restore, dest=States.auth_success, before=ref(click_skip_restore)),
    ))
    async def skip_restoration(self): ...

    @name_enricher(add_transitions(
        transition(source=States.challenge_confirm, dest=States.offer_to_restore, conditions=ref(is_restore), after=ref(skip_restoration)),
        transition(source=States.challenge_confirm, dest=States.address_entry, conditions=ref(is_address_entry), after=ref(skip_address)),
        transition(source=States.challenge_confirm, dest=States.auth_success, conditions=ref(is_takeout_url)),
    ))
    async def confirm_mfa(self): ...

    @name_enricher(add_transitions(transition(
        source=States.other_challenge_select,
        dest=States.challenge_confirm,
        before=ref(click_acceptable_mfa),
    )))
    async def select_acceptable_mfa(self): ...

    @name_enricher(add_transitions(transition(
        source=States.challenge_skotp,
        dest=States.other_challenge_select,
        before=ref(click_try_another_mfa),
        after=ref(select_acceptable_mfa),
    )))
    async def skip_skotp(self): ...

    @name_enricher(add_transitions(
        transition(source=States.password_entry, dest=States.challenge_skotp, conditions=ref(is_skotp), after=ref(skip_skotp)),
        transition(source=States.password_entry, dest=States.other_challenge_select, conditions=ref(is_mfa_selection), after=ref(select_acceptable_mfa)),
        transition(source=States.password_entry, dest=States.offer_to_restore, conditions=ref(is_restore), after=ref(skip_restoration)),
        transition(source=States.password_entry, dest=States.address_entry, conditions=ref(is_address_entry), after=ref(skip_address)),
        transition(source=States.password_entry, dest=States.auth_success, conditions=ref(is_takeout_url)),
    ))
    async def submit_password(self): ...

    @name_enricher(add_transitions(transition(
        source=States.email_entry,
        dest=States.password_entry,
        after=ref(submit_password),
    )))
    async def submit_email(self): ...

    @name_enricher(add_transitions(transition(
        source=States.account_chooser,
        dest=States.password_entry,
        after=ref(submit_password),
    )))
    async def select_and_proceed(self): ...

    @name_enricher(add_transitions(
        transition(source=States.start, dest=States.account_chooser, conditions=ref(is_account_chooser), after=ref(select_and_proceed)),
        transition(source=States.start, dest=States.email_entry, conditions=ref(is_signin), after=ref(submit_email)),
        transition(source=States.start, dest=States.address_entry, conditions=ref(is_address_entry), after=ref(skip_address)),
        transition(source=States.start, dest=States.auth_success),
    ))
    async def sign_in(self): ...


@with_model_definitions
class GoogleLoginMachine(AsyncMachine):
    pass
