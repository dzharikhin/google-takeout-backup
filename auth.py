import logging
import os
from urllib.parse import parse_qs, urlparse

from playwright.async_api import expect, Locator, Page, TimeoutError as PlaywrightTimeoutError
from transitions.experimental.utils import with_model_definitions, add_transitions, transition
from transitions.extensions.asyncio import AsyncMachine, AsyncState, AsyncEvent

TAKEOUT_BASEURL = "https://takeout.google.com/"
TAKEOUT_URL = f"{TAKEOUT_BASEURL}settings/takeout/custom/photos"


def name_enricher(outer):
    def _wrapper(func):
        original_name = func.__name__
        result = outer(func)
        result.original_name = original_name
        return result

    return _wrapper


def ref(func):
    return getattr(func, "original_name", None) or func.__name__


class CheckingAsyncEvent(AsyncEvent):
    async def _trigger(self, event_data):
        result = await super()._trigger(event_data)
        if not result and event_data.error is None:
            raise RuntimeError(f"No conditions matched in state '{event_data.model.state}' for event '{self.name}'")
        return result


class States:
    start = AsyncState(name="start")
    email_entry = AsyncState(name="email_entry", on_enter="fill_email_and_proceed")
    password_entry = AsyncState(name="password_entry", on_enter="fill_password_and_proceed")
    challenge_skotp = AsyncState(name="challenge_skotp", on_enter="click_try_another_mfa")
    account_chooser = AsyncState(name="account_chooser", on_enter="select_account")
    other_challenge_select = AsyncState(name="other_challenge_select", on_enter="click_acceptable_mfa")
    challenge_confirm = AsyncState(name="challenge_confirm", on_enter="handle_challenge_confirm")
    offer_to_restore = AsyncState(name="offer_to_restore", on_enter="click_skip_restore")
    address_entry = AsyncState(name="address_entry", on_enter="skip_address")
    auth_success = AsyncState(name="auth_success", final=True)

    @classmethod
    def as_list(cls):
        return [v for k, v in vars(cls).items() if isinstance(v, AsyncState)]


class GoogleLoginModel:
    def __init__(self, page: Page, timeout, mfa_confirm_timeout=None, email_env="USER_E", password_env="USER_P"):
        self.page = page
        self.timeout = timeout
        self.mfa_confirm_timeout = mfa_confirm_timeout or timeout * 3
        self.email_env = email_env
        self.password_env = password_env
        self.state = "start"

    async def wait_for_page_load(self):
        logging.debug("waiting for page load state")
        await self.page.wait_for_load_state(timeout=self.timeout)
        logging.debug("page is in load state, proceeding")

    async def is_refresh_complete(self):
        logging.debug("is_refresh_complete")
        if self.page.url.startswith(TAKEOUT_BASEURL):
            logging.debug("is_refresh_complete:no refresh bar on takeout screen")
            return
        progress_bar = self.page.locator('[role="progressbar"]')
        if await progress_bar.count() == 0:
            logging.debug("is_refresh_complete:no refresh bar found")
            return
        try:
            await expect(progress_bar).to_have_attribute("aria-hidden", "true", timeout=self.timeout)
        except AssertionError:
            if self.page.url.startswith(TAKEOUT_BASEURL):
                logging.debug("is_refresh_complete: refresh bar has aria-hidden=false, but we're on takeout, ok")
                return
            logging.debug("is_refresh_complete: refresh bar has aria-hidden=false, raising")
            raise

    @property
    def email_input(self) -> Locator:
        return self.page.locator("input[type=email]:visible").or_(self.page.locator("input#identifierId:visible"))

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
        logging.debug("is_skotp")
        await self.is_refresh_complete()
        try:
            await expect(self.default_mfa_input).to_be_visible(timeout=self.timeout)
            return True
        except (PlaywrightTimeoutError, AssertionError) as e:
            logging.debug(f"is_skotp:{e} returning false")
            return False

    async def is_on_account_choose_form(self):
        logging.debug("is_on_account_choose_form")
        await self.is_refresh_complete()
        try:
            await expect(self.account_chooser_item).to_be_visible(timeout=self.timeout)
            return True
        except (PlaywrightTimeoutError, AssertionError) as e:
            logging.debug(f"is_on_account_choose_form:{e} returning false")
            return False

    async def is_mfa_selection(self):
        logging.debug("is_mfa_selection")
        await self.is_refresh_complete()
        try:
            await expect(self.challenge_option).to_be_visible(timeout=self.timeout)
            return True
        except (PlaywrightTimeoutError, AssertionError) as e:
            logging.debug(f"is_mfa_selection:{e} returning false")
            return False

    async def is_restore(self):
        logging.debug("is_restore")
        await self.is_refresh_complete()
        try:
            await expect(
                self.page.locator('[href^="https://myaccount.google.com/signinoptions/password"]')
            ).to_be_visible(timeout=self.timeout)
            return True
        except (PlaywrightTimeoutError, AssertionError) as e:
            logging.debug(f"is_restore:{e} returning false")
            return False

    async def is_on_address_entry_form(self):
        logging.debug("is_on_address_entry_form")
        await self.is_refresh_complete()
        try:
            await self.page.wait_for_url(
                lambda u: (
                    parsed := urlparse(u),
                    "gds.google.com/web/homeaddress" in parsed.path or parsed.path.endswith("/homeaddress"),
                )[-1],
                timeout=self.timeout,
            )
            return True
        except (PlaywrightTimeoutError, AssertionError) as e:
            logging.debug(f"is_on_address_entry_form:{e} returning false")
            return False

    async def is_password_challenge(self):
        logging.debug("is_password_challenge")
        await self.is_refresh_complete()
        parsed = urlparse(self.page.url)
        if "challenge/pwd" not in parsed.path:
            return False
        password_input = self.page.locator("input[type=password]:visible")
        try:
            await expect(password_input).to_be_visible(timeout=self.timeout)
            return True
        except (PlaywrightTimeoutError, AssertionError) as e:
            logging.debug(f"is_password_challenge:{e} returning false")
            return False

    async def is_takeout_url(self):
        logging.debug("is_takeout_url")
        if not self.page.url.startswith(TAKEOUT_BASEURL):
            return False
        try:
            await self.page.wait_for_url(lambda u: not u.startswith(TAKEOUT_BASEURL), timeout=self.timeout)
            return False
        except PlaywrightTimeoutError as e:
            logging.debug(f"is_takeout_url:{e} returning true")
            return True

    async def is_signin(self):
        logging.debug("is_signin")
        await self.is_refresh_complete()
        try:
            await expect(self.email_input).to_be_visible(timeout=self.timeout)
            return True
        except (PlaywrightTimeoutError, AssertionError) as e:
            logging.debug(f"is_signin:{e} returning true")
            return False

    async def select_account(self):
        await self.account_chooser_item.click(timeout=self.timeout)
        await expect(self.account_chooser_item).to_be_hidden(timeout=self.timeout)

    async def fill_email_and_proceed(self):
        email_input = self.email_input
        await expect(email_input).to_have_count(1, timeout=self.timeout)
        await expect(email_input).to_be_visible(timeout=self.timeout)
        await email_input.fill(os.getenv(self.email_env))
        await (
            self.page.locator("button#identifierNext")
            .or_(self.page.locator("div#identifierNext"))
            .click(timeout=self.timeout)
        )
        await expect(email_input).to_be_hidden(timeout=self.timeout)

    async def fill_password_and_proceed(self):
        password_input = self.page.locator("input[type=password]:visible")
        await expect(password_input).to_have_count(1, timeout=self.timeout)
        await expect(password_input).to_be_visible(timeout=self.timeout)
        await password_input.fill(os.getenv(self.password_env))
        await (
            self.page.locator("button#passwordNext")
            .or_(self.page.locator("div#passwordNext"))
            .click(timeout=self.timeout)
        )
        await self.page.wait_for_url(
            lambda u: "challenge/pwd" not in u, timeout=self.timeout, wait_until="domcontentloaded"
        )

    async def click_try_another_mfa(self):
        button_panel = self.page.locator("[data-secondary-action-label]")
        await expect(button_panel).to_be_visible(timeout=self.timeout)
        target_button_text = await button_panel.get_attribute("data-secondary-action-label")
        await self.page.get_by_text(target_button_text).click(timeout=self.timeout)
        await expect(self.default_mfa_input).to_be_hidden(timeout=self.timeout)

    async def click_acceptable_mfa(self):
        challenge_button = self.challenge_option
        await challenge_button.click(timeout=self.timeout)
        await expect(self.challenge_option).to_be_hidden(timeout=self.timeout)

    async def click_skip_restore(self):
        proceed_button = self.page.locator('[href^="https://takeout.google.com/"]')
        await proceed_button.click(timeout=self.timeout)
        await expect(proceed_button).to_be_hidden(timeout=self.timeout)

    async def skip_address(self):
        parsed = urlparse(self.page.url)
        params = parse_qs(parsed.query)
        continue_url = params.get("continue", [TAKEOUT_BASEURL])[0]
        await self.page.goto(continue_url)
        await self.page.wait_for_url(
            lambda u: "homeaddress" not in u, timeout=self.timeout, wait_until="domcontentloaded"
        )

    async def handle_challenge_confirm(self):
        text = await self.page.locator("body").inner_text()
        print(text)
        await self.page.wait_for_url(
            lambda u: "signin/challenge/dp" not in u, timeout=self.mfa_confirm_timeout, wait_until="domcontentloaded"
        )
        await self.confirm_mfa()

    @name_enricher(
        add_transitions(
            transition(source=States.address_entry, dest=States.auth_success, conditions=ref(is_takeout_url)),
        )
    )
    async def leave_address(self): ...

    @name_enricher(
        add_transitions(
            transition(
                source=States.other_challenge_select,
                dest=States.challenge_confirm,
            )
        )
    )
    async def select_acceptable_mfa(self): ...

    @name_enricher(
        add_transitions(
            transition(
                source=States.challenge_skotp,
                dest=States.other_challenge_select,
                after=ref(select_acceptable_mfa),
            )
        )
    )
    async def skip_skotp(self): ...

    @name_enricher(
        add_transitions(
            transition(
                source=States.offer_to_restore,
                dest=States.address_entry,
                conditions=ref(is_on_address_entry_form),
                after=ref(leave_address),
            ),
            transition(source=States.offer_to_restore, dest=States.auth_success, conditions=ref(is_takeout_url)),
        )
    )
    async def skip_restoration(self): ...

    @name_enricher(
        add_transitions(
            transition(source=States.password_entry, dest=States.auth_success, conditions=ref(is_takeout_url)),
            transition(
                source=States.password_entry,
                dest=States.challenge_skotp,
                conditions=ref(is_skotp),
                after=ref(skip_skotp),
            ),
            transition(
                source=States.password_entry,
                dest=States.other_challenge_select,
                conditions=ref(is_mfa_selection),
                after=ref(select_acceptable_mfa),
            ),
            transition(
                source=States.password_entry,
                dest=States.offer_to_restore,
                conditions=ref(is_restore),
                after=ref(skip_restoration),
            ),
            transition(
                source=States.password_entry,
                dest=States.address_entry,
                conditions=ref(is_on_address_entry_form),
                after=ref(leave_address),
            ),
        )
    )
    async def submit_password(self): ...

    @name_enricher(
        add_transitions(
            transition(
                source=States.email_entry,
                dest=States.password_entry,
                after=ref(submit_password),
            )
        )
    )
    async def submit_email(self): ...

    @name_enricher(
        add_transitions(
            transition(
                source=States.account_chooser,
                dest=States.password_entry,
                after=ref(submit_password),
            )
        )
    )
    async def select_and_proceed(self): ...

    @name_enricher(
        add_transitions(
            transition(source=States.start, dest=States.auth_success, conditions=ref(is_takeout_url)),
            transition(
                source=States.start,
                dest=States.account_chooser,
                conditions=ref(is_on_account_choose_form),
                after=ref(select_and_proceed),
            ),
            transition(
                source=States.start,
                dest=States.password_entry,
                conditions=ref(is_password_challenge),
                after=ref(submit_password),
            ),
            transition(
                source=States.start, dest=States.email_entry, conditions=ref(is_signin), after=ref(submit_email)
            ),
            transition(
                source=States.start,
                dest=States.address_entry,
                conditions=ref(is_on_address_entry_form),
                after=ref(leave_address),
            ),
        )
    )
    async def sign_in(self): ...

    @name_enricher(
        add_transitions(
            transition(
                source=States.challenge_confirm,
                dest=States.offer_to_restore,
                conditions=ref(is_restore),
                after=ref(skip_restoration),
            ),
            transition(
                source=States.challenge_confirm,
                dest=States.address_entry,
                conditions=ref(is_on_address_entry_form),
                after=ref(leave_address),
            ),
            transition(source=States.challenge_confirm, dest=States.auth_success, conditions=ref(is_takeout_url)),
        )
    )
    async def confirm_mfa(self): ...


@with_model_definitions
class GoogleLoginMachine(AsyncMachine):
    event_cls = CheckingAsyncEvent

    def __init__(self, model, **kwargs):
        self._auth_model = model
        # if "prepare_event" not in kwargs:
        #     kwargs["prepare_event"] = ref(GoogleLoginModel.wait_for_page_load)
        super().__init__(model, **kwargs)

    def ensure_auth(self):
        if not self.get_state(self._auth_model.state).final:
            raise RuntimeError(f"Auth stuck in state: {self._auth_model.state}")
        return self._auth_model
