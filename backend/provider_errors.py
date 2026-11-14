"""
Provider-independent error classification.

No external network calls are made by this module.
"""


class RenderProviderError(RuntimeError):
    def __init__(
        self,
        message,
        *,
        retryable=False,
        error_code="provider_error",
    ):
        super().__init__(message)
        self.retryable = retryable
        self.error_code = error_code


class ProviderTimeoutError(RenderProviderError):
    def __init__(self, message="Provider request timed out"):
        super().__init__(
            message,
            retryable=True,
            error_code="provider_timeout",
        )


class ProviderRateLimitError(RenderProviderError):
    def __init__(self, message="Provider rate limit exceeded"):
        super().__init__(
            message,
            retryable=True,
            error_code="provider_rate_limit",
        )


class ProviderAuthenticationError(RenderProviderError):
    def __init__(self, message="Provider authentication failed"):
        super().__init__(
            message,
            retryable=False,
            error_code="provider_authentication",
        )


class ProviderContentError(RenderProviderError):
    def __init__(self, message="Provider rejected the content"):
        super().__init__(
            message,
            retryable=False,
            error_code="provider_content",
        )


def classify_http_status(status_code):
    if status_code in (408, 504):
        return ProviderTimeoutError()

    if status_code == 429:
        return ProviderRateLimitError()

    if status_code in (401, 403):
        return ProviderAuthenticationError()

    if status_code in (400, 422):
        return ProviderContentError()

    if status_code in (500, 502, 503):
        return RenderProviderError(
            "Provider temporarily unavailable",
            retryable=True,
            error_code="provider_unavailable",
        )

    return RenderProviderError(
        f"Unexpected provider HTTP status: {status_code}",
        retryable=False,
        error_code="provider_http_error",
    )
