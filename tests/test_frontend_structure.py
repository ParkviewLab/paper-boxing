# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The multi-session rules of docs/design.md section 5, checked structurally:
no UI element and no per-user value lives at module level in the frontend,
the pages are registered by `install()` and built per client, the auth
middleware only ever redirects page requests, and the return path after
sign-in never leaves the site."""

from __future__ import annotations

import importlib
import inspect
import pkgutil

from nicegui import Client, ui
from nicegui.observables import ObservableDict
from nicegui.persistence import PersistentDict

import paper_boxing.frontend as frontend_package
from paper_boxing.frontend import auth


def _frontend_modules() -> list[str]:
    names = [frontend_package.__name__]
    for info in pkgutil.walk_packages(frontend_package.__path__, prefix=f"{frontend_package.__name__}."):
        names.append(info.name)
    return names


def test_no_ui_element_lives_at_module_level() -> None:
    for name in _frontend_modules():
        module = importlib.import_module(name)
        offenders = [attr for attr, value in vars(module).items() if isinstance(value, ui.element)]
        assert not offenders, f"{name} holds UI elements at module level: {offenders}"


def test_nothing_per_user_is_kept_at_module_level() -> None:
    """The only module state is the configuration and the one shared, stateless client; elsewhere no module
    holds a UI element, a client, a NiceGUI storage object, or a mutable list or set."""
    from paper_boxing.frontend import backend

    def is_named_function(value: object) -> bool:
        return inspect.isfunction(value) and value.__name__ != "<lambda>"

    module_state = {
        k
        for k, v in vars(backend).items()
        if k.startswith("_") and not k.startswith("__") and not is_named_function(v)
    }
    assert module_state == {"_config", "_factory", "_client"}

    forbidden = (ui.element, Client, ObservableDict, PersistentDict, list, set)
    for name in _frontend_modules():
        if name == backend.__name__:
            continue
        module = importlib.import_module(name)
        state = [k for k, v in vars(module).items() if not k.startswith("__") and isinstance(v, forbidden)]
        assert not state, f"{name} keeps state at module level: {state}"


def test_public_paths_are_exactly_the_ones_needing_no_session() -> None:
    assert auth.is_public("/login")
    assert auth.is_public("/health")
    assert auth.is_public("/admin/version")
    assert auth.is_public("/_nicegui/3.17.0/static/nicegui.css")
    assert auth.is_public("/_nicegui_ws/socket.io/")
    for path in ("/", "/sites/x", "/tokens", "/users", "/account", "/download/x/a.txt"):
        assert not auth.is_public(path), path


def test_return_path_after_sign_in_stays_on_site() -> None:
    assert auth.safe_next(None) == "/"
    assert auth.safe_next("") == "/"
    assert auth.safe_next("/sites/x?path=docs") == "/sites/x?path=docs"
    assert auth.safe_next("https://elsewhere.test/") == "/"
    assert auth.safe_next("//elsewhere.test/") == "/"
    assert auth.safe_next("sites/x") == "/"
    assert auth.login_url("/") == "/login"
    assert auth.login_url("/sites/x?path=a b") == "/login?next=%2Fsites%2Fx%3Fpath%3Da%20b"
