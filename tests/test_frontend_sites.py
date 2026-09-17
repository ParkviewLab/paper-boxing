# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The sites list: creating a site, its public address, the backend's
messages, and deletion that needs the slug typed back."""

from __future__ import annotations

from nicegui.testing import User

from paper_boxing.common.fake_backend import FakeState
from tests import frontend_support as support


async def test_create_site_shows_it_with_its_public_url(user: User, frontend_state: FakeState) -> None:
    await support.sign_in(user, *support.ADMIN)
    await user.should_see(marker="no-sites")
    user.find(marker="site-name").type("PensaForma: Design")
    await user.should_see("/pensaforma-design/")  # the slug preview
    user.find(marker="create-site").click()
    await user.should_see("Site 'PensaForma: Design' created")
    await user.should_see(f"{support.PUBLIC_SITES_URL}/pensaforma-design/")
    assert "pensaforma-design" in frontend_state.sites
    assert frontend_state.sites["pensaforma-design"].name == "PensaForma: Design"


async def test_copy_url_reports_what_the_browser_did(user: User) -> None:
    await support.seed_site("Copied")
    await support.sign_in(user, *support.ADMIN)
    await user.should_see("Copied")
    support.browser_copies(user, success=True)
    user.find(marker="copy-url").click()
    await user.should_see("URL copied")
    support.browser_copies(user, success=False)
    user.find(marker="copy-url").click()
    await user.should_see("Copying is not available here")


async def test_backend_messages_are_shown_as_text(user: User) -> None:
    await support.seed_site("Taken")
    await support.sign_in(user, *support.ADMIN)
    user.find(marker="site-name").type("!!!")
    await user.should_see("no slug can be derived")
    user.find(marker="create-site").click()
    await user.should_see("no slug can be derived from the name '!!!'")
    user.find(marker="site-name").clear()
    user.find(marker="site-name").type("taken")
    user.find(marker="create-site").click()
    await user.should_see("a site with the slug 'taken' exists")
    assert "Traceback" not in support.page_text(user)


async def test_delete_refuses_a_wrong_slug_and_accepts_the_right_one(
    user: User, frontend_state: FakeState
) -> None:
    slug = await support.seed_site("Doomed", {"index.html": b"<p>"})
    await support.sign_in(user, *support.ADMIN)
    await user.should_see("Doomed")
    user.find(marker="delete-site").click()
    await user.should_see(marker="confirm-slug")
    user.find(marker="confirm-slug").type("doome")
    user.find(marker="confirm-delete-site").click()
    await user.should_see("pass ?confirm=doomed to delete the site")
    assert slug in frontend_state.sites
    await user.should_see("Doomed")

    user.find(marker="delete-site").click()
    await user.should_see(marker="confirm-slug")
    user.find(marker="confirm-slug").type("doomed")
    user.find(marker="confirm-delete-site").click()
    await user.should_see("Site 'Doomed' deleted")
    await user.should_see(marker="no-sites")
    assert slug not in frontend_state.sites


async def test_cancelling_the_deletion_keeps_the_site(user: User, frontend_state: FakeState) -> None:
    slug = await support.seed_site("Kept")
    await support.sign_in(user, *support.ADMIN)
    user.find(marker="delete-site").click()
    await user.should_see(marker="confirm-slug")
    user.find("Cancel").click()
    await user.should_not_see(marker="confirm-slug")
    assert slug in frontend_state.sites


async def test_a_session_ended_elsewhere_sends_the_person_to_sign_in(
    user: User, frontend_state: FakeState
) -> None:
    await support.sign_in(user, *support.ADMIN)
    (secret,) = support.session_secrets(frontend_state, support.ADMIN[0])
    frontend_state.revoke(frontend_state.secrets[secret])  # for example, a password change in another browser
    user.find(marker="site-name").type("Late")
    user.find(marker="create-site").click()
    await user.should_see("Your session has ended")
    await user.should_see(marker="sign-in")
    assert "late" not in frontend_state.sites
