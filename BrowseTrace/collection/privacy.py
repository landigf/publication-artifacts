"""Credential-field filtering shared by collection exports and release checks."""
CREDENTIAL_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie", "set-cookie", "api-key", "apikey", "x-api-key", "x-goog-api-key", "x-amz-security-token", "x-auth-token", "x-access-token", "x-authorization"})


def safe_headers(headers: dict | None) -> dict:
    return {key: value for key, value in (headers or {}).items() if key.lower() not in CREDENTIAL_HEADERS}
