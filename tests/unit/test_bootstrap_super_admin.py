from __future__ import annotations

import os
import stat

import pytest

from app.ops.bootstrap_super_admin import TokenFileError, write_activation_token


def test_write_activation_token_creates_mode_0600_file_once(tmp_path):
    token_directory = tmp_path / "bootstrap"
    token_directory.mkdir()
    token_file = token_directory / "initial-super-admin.activation"

    write_activation_token("test-token", token_file, allowed_directory=token_directory)

    assert token_file.read_text(encoding="utf-8") == "test-token\n"
    if os.name != "nt":
        assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    with pytest.raises(TokenFileError, match="refusing to overwrite"):
        write_activation_token("replacement", token_file, allowed_directory=token_directory)
    assert token_file.read_text(encoding="utf-8") == "test-token\n"


def test_write_activation_token_refuses_a_path_outside_the_approved_directory(tmp_path):
    token_directory = tmp_path / "bootstrap"
    token_directory.mkdir()

    with pytest.raises(TokenFileError, match="approved directory"):
        write_activation_token(
            "test-token",
            tmp_path / "outside.activation",
            allowed_directory=token_directory,
        )
