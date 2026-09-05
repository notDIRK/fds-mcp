"""Optional: pressing "send" in a real browser. Off unless ``FDS_MCP_BROWSER_SEND=1``.

Replying to an authority is not possible through the API — ``POST /api/v1/message/``
refuses ``kind: "email"`` and ``/anfrage/<slug>/send/message/`` ignores OAuth bearer
tokens entirely (both measured 2026-09-05, see ``tests/test_api_contract.py``). The only
remaining channel is the browser form, driven with the user's own logged-in session.

**This module removes the human from the last step, and that is a real cost.** The
reasoning, the alternatives and the mitigations are in the README under "Sending
replies"; they are not repeated here. What this file guarantees is narrower:

  * it never composes text — it is handed a subject and a body and types exactly those;
  * it reads the form back after filling it and refuses to click if what it reads is not
    what it typed;
  * it refuses to click while the "Adresse mitsenden" checkbox is ticked, while the text
    contains the placeholder U+2026, or while the salutation or the closing formula is
    missing or doubled;
  * anything it cannot find, it treats as a failure. Every check fails closed.

Selectors are accessible names, taken from the logged-in reply form of a real request on
2026-09-05:

    radio    "Standardadresse von <authority> (<e-mail>)"   [checked]
    textbox  "Betreff *"
    textbox  "Ihre Nachricht *"
    checkbox "Adresse mitsenden"
    textbox  "Postadresse"                                  [prefilled]
    button   "Nachricht absenden"
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config
from .errors import FdsMcpError
from .rules import PLACEHOLDER_MARKER, count_closings, count_salutations

# Accessible names of the reply form, verified 2026-09-05.
SUBJECT_FIELD = "Betreff"
MESSAGE_FIELD = "Ihre Nachricht"
ADDRESS_CHECKBOX = "Adresse mitsenden"
SEND_BUTTON = "Nachricht absenden"
RECIPIENT_RADIO = "Standardadresse"

DEFAULT_TIMEOUT_MS = 30_000

PLAYWRIGHT_MISSING = (
    "playwright is not installed. It is an optional dependency, deliberately: this "
    "server does not need a browser for anything else.\n"
    "  pip install 'fds-mcp[browser]'\n"
    "  python -m playwright install chromium"
)


class BrowserSendError(FdsMcpError):
    """The browser path refused, or the form did not look the way it must."""


@dataclass
class FormReport:
    """What was found in the form, and what it was checked against.

    Everything here is reported back to the user verbatim. A send that cannot say who it
    went to is not a send anybody should trust.
    """

    url: str
    recipient: str = ""
    subject_in_form: str = ""
    address_checkbox_checked: bool | None = None
    salutations: int = 0
    closings: int = 0
    placeholder_present: bool = True
    checks: list[str] = field(default_factory=list)
    clicked: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "recipient": self.recipient,
            "subject_in_form": self.subject_in_form,
            "address_checkbox_checked": self.address_checkbox_checked,
            "salutations": self.salutations,
            "closings": self.closings,
            "placeholder_present": self.placeholder_present,
            "checks": list(self.checks),
            "clicked": self.clicked,
        }


def profile_dir() -> Path:
    """Where the browser profile lives.

    ``FDS_MCP_BROWSER_PROFILE`` should point at a profile that is logged in to
    fragdenstaat.de **and to nothing else**. A browser this tool drives acts with every
    right the session in that profile carries.
    """
    override = os.environ.get("FDS_MCP_BROWSER_PROFILE")
    if override:
        return Path(override).expanduser()
    return config.home() / "browser-profile"


def check_text(body: str) -> list[str]:
    """The text checks, separated out so tests can run them without a browser."""
    problems: list[str] = []
    if PLACEHOLDER_MARKER in body:
        problems.append(
            f"the message still contains the placeholder {PLACEHOLDER_MARKER!r} (U+2026) "
            "that the form is prefilled with")
    salutations = count_salutations(body)
    if salutations != 1:
        problems.append(f"the message contains {salutations} salutations, expected "
                        "exactly one")
    closings = count_closings(body)
    if closings != 1:
        problems.append(f"the message contains {closings} closing formulae, expected "
                        "exactly one")
    return problems


def drive_form(page, *, send_url: str, subject: str, body: str, dry_run: bool,
               locator_error: type[BaseException] = Exception) -> dict:
    """Everything that happens on the page, separated from launching a browser.

    Split out so the gate can be tested against a fake page rather than asserted about in
    prose. ``locator_error`` is playwright's ``Error`` in production; a test passes its
    own. Raises :class:`BrowserSendError` on every problem, including "the field was not
    found" — a form that does not look the way this function expects is a form this
    function must not press buttons in.
    """
    report = FormReport(url=send_url)
    page.goto(send_url)

    try:
        subject_field = page.get_by_role("textbox", name=SUBJECT_FIELD)
        message_field = page.get_by_role("textbox", name=MESSAGE_FIELD)
        address_box = page.get_by_role("checkbox", name=ADDRESS_CHECKBOX)
        send_button = page.get_by_role("button", name=SEND_BUTTON)
        subject_field.wait_for(state="visible")
    except locator_error as exc:
        raise BrowserSendError(
            f"The reply form at {send_url} does not look the way it did when this code "
            "was written — a field could not be located. Refusing to click anything. "
            f"Underlying error: {exc}"
        ) from exc

    # --- who is this going to? --------------------------------------------
    try:
        recipient = page.get_by_role("radio", name=RECIPIENT_RADIO)
        report.recipient = (recipient.get_attribute("aria-label")
                            or recipient.first.inner_text()
                            or "").strip()
    except locator_error:
        report.recipient = ""
    if not report.recipient:
        raise BrowserSendError(
            "Could not read the recipient from the form. A send that cannot name its "
            "recipient does not happen.")
    report.checks.append(f"recipient: {report.recipient}")

    # --- the postal address checkbox --------------------------------------
    report.address_checkbox_checked = bool(address_box.is_checked())
    if report.address_checkbox_checked:
        raise BrowserSendError(
            f"The {ADDRESS_CHECKBOX!r} checkbox is ticked. This tool never sends your "
            "postal address; on a public request that would publish where you live, "
            "under CC0 and irreversibly. Untick it in the browser, or send this message "
            "by hand if the authority really needs the address."
        )
    report.checks.append(f"{ADDRESS_CHECKBOX!r} checkbox: off")

    # --- fill, then read back ---------------------------------------------
    subject_field.fill(subject)
    message_field.fill(body)
    report.subject_in_form = subject_field.input_value()
    typed = message_field.input_value()
    if report.subject_in_form != subject or typed != body:
        raise BrowserSendError(
            "The form does not contain what was typed into it. Refusing to send. Some "
            "other script may be rewriting the field.")
    report.checks.append("subject and message read back byte for byte")

    report.placeholder_present = PLACEHOLDER_MARKER in typed
    report.salutations = count_salutations(typed)
    report.closings = count_closings(typed)
    remaining = check_text(typed)
    if remaining:
        raise BrowserSendError(
            "The text in the form fails its checks:\n  - " + "\n  - ".join(remaining))
    report.checks.append(
        f"no U+2026, {report.salutations} salutation, {report.closings} closing")

    # --- the checkbox again, immediately before the click -----------------
    if address_box.is_checked():
        raise BrowserSendError(
            f"The {ADDRESS_CHECKBOX!r} checkbox became ticked while the form was being "
            "filled. Refusing to send.")

    if dry_run:
        report.checks.append("dry_run: the send button was NOT pressed")
        return report.as_dict()

    send_button.click()
    page.wait_for_load_state("networkidle")
    report.clicked = True
    report.checks.append("send button pressed")
    return report.as_dict()


def send_reply(*, send_url: str, subject: str, body: str, dry_run: bool = True,
               headless: bool = True, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> dict:
    """Open the reply form in a real browser and hand the page to :func:`drive_form`."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise BrowserSendError(PLAYWRIGHT_MISSING) from exc

    problems = check_text(body)
    if problems:
        raise BrowserSendError(
            "Refusing to open the browser — the text fails its own checks:\n  - "
            + "\n  - ".join(problems))

    user_dir = profile_dir()
    user_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:  # pragma: no cover - needs a real browser
        context = pw.chromium.launch_persistent_context(
            str(user_dir), headless=headless, timeout=timeout_ms)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(timeout_ms)
            return drive_form(page, send_url=send_url, subject=subject, body=body,
                              dry_run=dry_run, locator_error=PlaywrightError)
        finally:
            context.close()
