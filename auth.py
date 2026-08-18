import logging
import os
import time
from urllib.parse import parse_qs, urlparse

from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException as SeleniumTimeoutException

from transitions import Machine, State, Event
from transitions.experimental.utils import with_model_definitions, add_transitions, transition

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


class CheckingEvent(Event):
    def _trigger(self, event_data):
        result = super()._trigger(event_data)
        if not result and event_data.error is None:
            raise RuntimeError(f"No conditions matched in state '{event_data.model.state}' for event '{self.name}'")
        return result


class States:
    start = State(name="start")
    email_entry = State(name="email_entry", on_enter="fill_email_and_proceed")
    password_entry = State(name="password_entry", on_enter="fill_password_and_proceed")
    challenge_skotp = State(name="challenge_skotp", on_enter="click_try_another_mfa")
    account_chooser = State(name="account_chooser", on_enter="select_account")
    other_challenge_select = State(name="other_challenge_select", on_enter="click_acceptable_mfa")
    challenge_confirm = State(name="challenge_confirm", on_enter="handle_challenge_confirm")
    offer_to_restore = State(name="offer_to_restore", on_enter="click_skip_restore")
    address_entry = State(name="address_entry", on_enter="skip_address")
    auth_success = State(name="auth_success", final=True)

    @classmethod
    def as_list(cls):
        return [v for k, v in vars(cls).items() if isinstance(v, State)]


class GoogleLoginModel:
    _EMAIL_INPUT = (By.CSS_SELECTOR, "input[type=email], input#identifierId")
    _CHALLENGE_OPTION = (By.CSS_SELECTOR, 'div[data-challengetype="39"]')
    _DEFAULT_MFA_INPUT = (By.CSS_SELECTOR, "input#securityKeyOtpInputId")
    _ACCOUNT_CHOOSER = (By.CSS_SELECTOR, "div[data-button-type=multipleChoiceIdentifier]")
    _PROGRESS_BAR = (By.CSS_SELECTOR, '[role="progressbar"]')
    _IDENTIFIER_NEXT = (By.CSS_SELECTOR, "button#identifierNext, div#identifierNext")
    _PASSWORD_NEXT = (By.CSS_SELECTOR, "button#passwordNext, div#passwordNext")

    def __init__(self, driver: WebDriver, timeout, mfa_confirm_timeout=None, email_env="USER_E", password_env="USER_P"):
        self.driver = driver
        self.timeout = timeout
        self._timeout_s = timeout / 1000
        self.mfa_confirm_timeout = mfa_confirm_timeout or timeout * 3
        self._mfa_confirm_timeout_s = self.mfa_confirm_timeout / 1000
        self.email_env = email_env
        self.password_env = password_env
        self.state = "start"

    def wait_for_page_load(self):
        logging.debug("waiting for page load state")
        try:
            WebDriverWait(self.driver, self._timeout_s).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            logging.debug("page is in load state, proceeding")
        except SeleniumTimeoutException:
            logging.warning(
                "page did not reach readyState=complete within %.1fs, proceeding",
                self._timeout_s,
            )

    def _net_error_target(self):
        try:
            uri = self.driver.execute_script("return document.documentURI")
        except Exception:
            return None
        if not isinstance(uri, str) or not uri.startswith("about:neterror"):
            return None
        return parse_qs(urlparse(uri).query).get("u", [None])[0]

    def recover_from_net_error(self):
        failing_url = self._net_error_target()
        if not failing_url:
            return
        logging.warning(f"network error page detected, failing url: {failing_url}")
        host = urlparse(failing_url).hostname or ""
        if host.startswith("accounts.google.") and host != "accounts.google.com":
            fallback_url = failing_url.replace(host, "accounts.google.com", 1)
            logging.warning(f"retrying via {fallback_url}")
            self.driver.get(fallback_url)
            self.wait_for_page_load()
            if not self._net_error_target():
                return
            logging.warning("retry via accounts.google.com failed too")
        logging.warning(f"navigating straight to {TAKEOUT_URL}")
        self.driver.get(TAKEOUT_URL)
        self.wait_for_page_load()

    def wait_for_navigation_settle(self):
        deadline = time.monotonic() + self._timeout_s
        while True:
            self.wait_for_page_load()
            self.recover_from_net_error()
            self.is_refresh_complete()
            if self.driver.current_url.startswith(TAKEOUT_BASEURL):
                return
            last_url = self.driver.current_url
            url_stable = True
            for _ in range(3):
                time.sleep(1.0)
                current_url = self.driver.current_url
                if current_url != last_url:
                    url_stable = False
                    break
                last_url = current_url
            if url_stable or time.monotonic() >= deadline:
                return

    def is_refresh_complete(self):
        logging.debug("is_refresh_complete")
        if self.driver.current_url.startswith(TAKEOUT_BASEURL):
            logging.debug("is_refresh_complete:no refresh bar on takeout screen")
            return
        progress_bars = self.driver.find_elements(*self._PROGRESS_BAR)
        if len(progress_bars) == 0:
            logging.debug("is_refresh_complete:no refresh bar found")
            return
        try:
            WebDriverWait(self.driver, self._timeout_s).until(
                lambda d: d.find_element(*self._PROGRESS_BAR).get_attribute("aria-hidden") == "true"
            )
        except SeleniumTimeoutException:
            if self.driver.current_url.startswith(TAKEOUT_BASEURL):
                logging.debug("is_refresh_complete: refresh bar has aria-hidden=false, but we're on takeout, ok")
                return
            logging.debug("is_refresh_complete: refresh bar has aria-hidden=false, raising")
            raise

    @property
    def email_input(self):
        return self.driver.find_element(*self._EMAIL_INPUT)

    @property
    def challenge_option(self):
        return self.driver.find_element(*self._CHALLENGE_OPTION)

    @property
    def default_mfa_input(self):
        return self.driver.find_element(*self._DEFAULT_MFA_INPUT)

    @property
    def account_chooser_item(self):
        return self.driver.find_element(*self._ACCOUNT_CHOOSER)

    def is_skotp(self):
        logging.debug("is_skotp")
        self.is_refresh_complete()
        try:
            WebDriverWait(self.driver, self._timeout_s).until(EC.visibility_of_element_located(self._DEFAULT_MFA_INPUT))
            return True
        except SeleniumTimeoutException as e:
            logging.debug(f"is_skotp:{e} returning false")
            return False

    def is_on_account_choose_form(self):
        logging.debug("is_on_account_choose_form")
        self.is_refresh_complete()
        try:
            WebDriverWait(self.driver, self._timeout_s).until(EC.visibility_of_element_located(self._ACCOUNT_CHOOSER))
            return True
        except SeleniumTimeoutException as e:
            logging.debug(f"is_on_account_choose_form:{e} returning false")
            return False

    def is_mfa_selection(self):
        logging.debug("is_mfa_selection")
        self.is_refresh_complete()
        try:
            WebDriverWait(self.driver, self._timeout_s).until(EC.visibility_of_element_located(self._CHALLENGE_OPTION))
            return True
        except SeleniumTimeoutException as e:
            logging.debug(f"is_mfa_selection:{e} returning false")
            return False

    def is_restore(self):
        logging.debug("is_restore")
        self.is_refresh_complete()
        try:
            WebDriverWait(self.driver, self._timeout_s).until(
                EC.visibility_of_element_located(
                    (By.CSS_SELECTOR, '[href^="https://myaccount.google.com/signinoptions/password"]')
                )
            )
            return True
        except SeleniumTimeoutException as e:
            logging.debug(f"is_restore:{e} returning false")
            return False

    def is_on_address_entry_form(self):
        logging.debug("is_on_address_entry_form")
        self.is_refresh_complete()

        def _check_url(driver):
            parsed = urlparse(driver.current_url)
            return "gds.google.com/web/homeaddress" in parsed.path or parsed.path.endswith("/homeaddress")

        try:
            WebDriverWait(self.driver, self._timeout_s).until(_check_url)
            return True
        except SeleniumTimeoutException as e:
            logging.debug(f"is_on_address_entry_form:{e} returning false")
            return False

    def is_password_challenge(self):
        logging.debug("is_password_challenge")
        self.is_refresh_complete()
        parsed = urlparse(self.driver.current_url)
        if "challenge/pwd" not in parsed.path:
            return False
        try:
            WebDriverWait(self.driver, self._timeout_s).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, "input[type=password]"))
            )
            return True
        except SeleniumTimeoutException as e:
            logging.debug(f"is_password_challenge:{e} returning false")
            return False

    def is_takeout_url(self):
        logging.debug("is_takeout_url")
        if not self.driver.current_url.startswith(TAKEOUT_BASEURL):
            return False
        try:
            WebDriverWait(self.driver, max(5.0, self._timeout_s / 3)).until(
                lambda d: not d.current_url.startswith(TAKEOUT_BASEURL)
            )
            return False
        except SeleniumTimeoutException as e:
            logging.debug(f"is_takeout_url:{e} returning true")
            return True

    def is_signin(self):
        logging.debug("is_signin")
        self.is_refresh_complete()
        try:
            WebDriverWait(self.driver, self._timeout_s).until(EC.visibility_of_element_located(self._EMAIL_INPUT))
            return True
        except SeleniumTimeoutException as e:
            logging.debug(f"is_signin:{e} returning false")
            return False

    def select_account(self):
        account_chooser = WebDriverWait(self.driver, self._timeout_s).until(
            EC.element_to_be_clickable(self._ACCOUNT_CHOOSER)
        )
        account_chooser.click()
        WebDriverWait(self.driver, self._timeout_s).until(EC.invisibility_of_element_located(self._ACCOUNT_CHOOSER))

    def fill_email_and_proceed(self):
        email_input = WebDriverWait(self.driver, self._timeout_s).until(
            EC.visibility_of_element_located(self._EMAIL_INPUT)
        )
        email_input.clear()
        email_input.send_keys(os.getenv(self.email_env))
        next_btn = WebDriverWait(self.driver, self._timeout_s).until(EC.element_to_be_clickable(self._IDENTIFIER_NEXT))
        next_btn.click()
        WebDriverWait(self.driver, self._timeout_s).until(EC.invisibility_of_element_located(self._EMAIL_INPUT))

    def fill_password_and_proceed(self):
        password_input = WebDriverWait(self.driver, self._timeout_s).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, "input[type=password]"))
        )
        password_input.clear()
        password_input.send_keys(os.getenv(self.password_env))
        next_btn = WebDriverWait(self.driver, self._timeout_s).until(EC.element_to_be_clickable(self._PASSWORD_NEXT))
        next_btn.click()
        WebDriverWait(self.driver, self._timeout_s).until(lambda d: "challenge/pwd" not in d.current_url)

    def click_try_another_mfa(self):
        button_panel = WebDriverWait(self.driver, self._timeout_s).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, "[data-secondary-action-label]"))
        )
        target_button_text = button_panel.get_attribute("data-secondary-action-label")
        target_button = self.driver.find_element(By.XPATH, f"//*[contains(text(), '{target_button_text}')]")
        target_button.click()
        WebDriverWait(self.driver, self._timeout_s).until(EC.invisibility_of_element_located(self._DEFAULT_MFA_INPUT))

    def click_acceptable_mfa(self):
        challenge_button = WebDriverWait(self.driver, self._timeout_s).until(
            EC.element_to_be_clickable(self._CHALLENGE_OPTION)
        )
        challenge_button.click()
        WebDriverWait(self.driver, self._timeout_s).until(EC.invisibility_of_element_located(self._CHALLENGE_OPTION))

    def click_skip_restore(self):
        proceed_button = WebDriverWait(self.driver, self._timeout_s).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, '[href^="https://takeout.google.com/"]'))
        )
        proceed_button.click()
        WebDriverWait(self.driver, self._timeout_s).until(
            EC.invisibility_of_element_located((By.CSS_SELECTOR, '[href^="https://takeout.google.com/"]'))
        )

    def skip_address(self):
        parsed = urlparse(self.driver.current_url)
        params = parse_qs(parsed.query)
        continue_url = params.get("continue", [TAKEOUT_BASEURL])[0]
        self.driver.get(continue_url)
        WebDriverWait(self.driver, self._timeout_s).until(lambda d: "homeaddress" not in d.current_url)

    def handle_challenge_confirm(self):
        text = self.driver.find_element(By.TAG_NAME, "body").text
        print(text)
        WebDriverWait(self.driver, self._mfa_confirm_timeout_s).until(
            lambda d: "signin/challenge/dp" not in d.current_url
        )
        self.confirm_mfa()

    @name_enricher(
        add_transitions(
            transition(source=States.address_entry, dest=States.auth_success, conditions=ref(is_takeout_url)),
        )
    )
    def leave_address(self): ...

    @name_enricher(
        add_transitions(
            transition(
                source=States.other_challenge_select,
                dest=States.challenge_confirm,
            )
        )
    )
    def select_acceptable_mfa(self): ...

    @name_enricher(
        add_transitions(
            transition(
                source=States.challenge_skotp,
                dest=States.other_challenge_select,
                after=ref(select_acceptable_mfa),
            )
        )
    )
    def skip_skotp(self): ...

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
    def skip_restoration(self): ...

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
    def submit_password(self): ...

    @name_enricher(
        add_transitions(
            transition(
                source=States.email_entry,
                dest=States.password_entry,
                after=ref(submit_password),
            )
        )
    )
    def submit_email(self): ...

    @name_enricher(
        add_transitions(
            transition(
                source=States.account_chooser,
                dest=States.password_entry,
                after=ref(submit_password),
            )
        )
    )
    def select_and_proceed(self): ...

    @name_enricher(
        add_transitions(
            transition(source=States.start, dest=States.auth_success, conditions=ref(is_takeout_url)),
            transition(
                source=States.start,
                dest=States.password_entry,
                conditions=ref(is_password_challenge),
                after=ref(submit_password),
            ),
            transition(
                source=States.start,
                dest=States.account_chooser,
                conditions=ref(is_on_account_choose_form),
                after=ref(select_and_proceed),
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
    def sign_in(self): ...

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
    def confirm_mfa(self): ...


@with_model_definitions
class GoogleLoginMachine(Machine):
    event_cls = CheckingEvent

    def __init__(self, model, **kwargs):
        self._auth_model = model
        kwargs.setdefault("prepare_event", ref(GoogleLoginModel.wait_for_navigation_settle))
        super().__init__(model, **kwargs)

    def ensure_auth(self):
        if not self.get_state(self._auth_model.state).final:
            raise RuntimeError(f"Auth stuck in state: {self._auth_model.state}")
        return self._auth_model
