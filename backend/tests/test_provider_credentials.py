from unittest.mock import patch

import pytest

from provider_credentials import (
    MissingProviderCredential,
    credential_variable_name,
    credential_is_configured,
    get_provider_credential,
    provider_credential_status,
)


def test_fal_credential_variable():
    assert credential_variable_name("fal") == "FAL_KEY"


def test_provider_name_normalization():
    assert credential_variable_name(" FAL ") == "FAL_KEY"


def test_unknown_provider_rejected():
    with pytest.raises(ValueError):
        credential_variable_name("unknown")


def test_missing_credential():
    with patch.dict("os.environ", {"FAL_KEY": ""}):
        with pytest.raises(MissingProviderCredential):
            get_provider_credential("fal")


def test_valid_credential():
    with patch.dict(
        "os.environ",
        {"FAL_KEY": "test-placeholder-secret"},
    ):
        result = get_provider_credential("fal")

    assert result == "test-placeholder-secret"


def test_credential_presence():
    with patch.dict(
        "os.environ",
        {"FAL_KEY": "test-placeholder-secret"},
    ):
        assert credential_is_configured("fal")


def test_status_does_not_expose_secret():
    secret = "test-placeholder-secret"

    with patch.dict(
        "os.environ",
        {"FAL_KEY": secret},
    ):
        result = provider_credential_status("fal")

    assert result["credential_configured"] is True
    assert secret not in str(result)
