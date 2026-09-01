from __future__ import annotations

import re
from pathlib import Path

CSS_PATH = Path(__file__).resolve().parents[2] / "app" / "static" / "css" / "base.css"


def _rule_body(stylesheet: str, selector: str) -> str:
    match = re.search(
        rf"{re.escape(selector)}\s*\{{(?P<body>.*?)\n\}}",
        stylesheet,
        flags=re.DOTALL,
    )
    assert match, f"Expected CSS rule for {selector}"
    return match.group("body")


def test_login_layout_overrides_shared_main_gutters():
    stylesheet = CSS_PATH.read_text(encoding="utf-8")

    login_wrap = _rule_body(stylesheet, ".login-page .login-wrap")

    assert "display: block;" in login_wrap
    assert "width: 100%;" in login_wrap
    assert "max-width: none;" in login_wrap
    assert "margin: 0;" in login_wrap
    assert "padding: 0;" in login_wrap


def test_login_scene_uses_the_available_viewport_height():
    stylesheet = CSS_PATH.read_text(encoding="utf-8")

    app_shell = _rule_body(stylesheet, ".login-page .app-shell,\n.login-page .main-col")
    login_wrap = _rule_body(stylesheet, ".login-page .login-wrap")
    login_scene = _rule_body(stylesheet, ".login-scene")

    assert "min-height: 100dvh;" in app_shell
    assert "min-height: 100dvh;" in login_wrap
    assert "min-height: 100dvh;" in login_scene