from __future__ import annotations

import pytest

from app.providers.ark_proxy_policy import ArkProxyPolicy, validate_ark_egress


def test_ark_proxy_policy_allows_only_exact_endpoint() -> None:
    policy = ArkProxyPolicy()
    assert validate_ark_egress(
        policy,
        scheme="https",
        host="ark.cn-beijing.volces.com",
        port=443,
        path="/api/v3/responses",
        method="POST",
        tls_verification=True,
        follow_redirects=False,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scheme", "http"),
        ("host", "open.volcengineapi.com"),
        ("port", 80),
        ("path", "/api/v3/chat/completions"),
        ("method", "GET"),
        ("tls_verification", False),
        ("follow_redirects", True),
    ],
)
def test_ark_proxy_policy_rejects_every_transport_drift(field: str, value) -> None:
    arguments = {
        "scheme": "https",
        "host": "ark.cn-beijing.volces.com",
        "port": 443,
        "path": "/api/v3/responses",
        "method": "POST",
        "tls_verification": True,
        "follow_redirects": False,
    }
    arguments[field] = value
    assert not validate_ark_egress(ArkProxyPolicy(), **arguments)
