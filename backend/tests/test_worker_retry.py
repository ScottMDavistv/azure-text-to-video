import pytest

from provider_errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
)
from worker_retry import (
    RetryPolicy,
    retry_delay,
    should_retry,
)


def test_default_policy():
    policy = RetryPolicy()
    assert policy.max_attempts == 5


def test_exponential_backoff():
    policy = RetryPolicy(
        max_attempts=6,
        initial_delay=2,
        max_delay=10,
    )

    assert retry_delay(1, policy) == 2
    assert retry_delay(2, policy) == 4
    assert retry_delay(3, policy) == 8
    assert retry_delay(4, policy) == 10


def test_attempt_limit():
    policy = RetryPolicy(max_attempts=3)

    with pytest.raises(ValueError):
        retry_delay(3, policy)


def test_retryable_error():
    assert should_retry(
        ProviderRateLimitError(),
        1,
    )


def test_auth_error_not_retryable():
    assert not should_retry(
        ProviderAuthenticationError(),
        1,
    )


def test_final_attempt_not_retryable():
    assert not should_retry(
        ProviderRateLimitError(),
        5,
    )


def test_arbitrary_exception_not_retryable():
    assert not should_retry(
        RuntimeError("unknown"),
        1,
    )


def test_invalid_attempt_count():
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)


def test_invalid_max_delay():
    with pytest.raises(ValueError):
        RetryPolicy(
            initial_delay=30,
            max_delay=2,
        )


def test_invalid_infinite_delay():
    with pytest.raises(ValueError):
        RetryPolicy(
            initial_delay=float("inf"),
        )


def test_boolean_attempt_rejected():
    assert not should_retry(
        ProviderRateLimitError(),
        True,
    )
