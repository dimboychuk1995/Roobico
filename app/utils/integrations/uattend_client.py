"""Thin HTTP client for the uAttend (WorkWell Technologies) API.

Docs: https://uattend.zendesk.com/hc/en-us/articles/48783008798875-uAttend-API

All endpoints are POST and authenticated via the ``x-api-key`` header.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import requests


BASE_URL = "https://api.workwelltech.com"
DEFAULT_TIMEOUT = 30  # seconds


class UAttendError(RuntimeError):
    """Base error for uAttend API problems."""

    def __init__(self, message: str, *, status: Optional[int] = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class UAttendAuthError(UAttendError):
    """401 — invalid or missing API key."""


class UAttendForbiddenError(UAttendError):
    """403 — valid key but blocked (IP allowlist / permissions)."""


class UAttendRateLimitError(UAttendError):
    """429 — too many requests."""


class UAttendClient:
    """Minimal POST-only client. Retries on 429 and 5xx with exponential backoff."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = 3,
    ):
        if not api_key:
            raise UAttendError("API key is required.")
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    # ── HTTP plumbing ────────────────────────────────────────────────────────

    def _post(self, path: str, body: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        payload = body if body is not None else {}
        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(
                    url, json=payload, headers=headers, timeout=self.timeout
                )
            except requests.RequestException as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    raise UAttendError(f"Network error: {exc}") from exc
                time.sleep(min(2 ** attempt, 8))
                continue

            status = resp.status_code

            if status == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise UAttendError(
                        "uAttend returned a non-JSON 200 response.",
                        status=status,
                        body=resp.text[:500],
                    ) from exc

            # Decode body for error reporting (best-effort).
            try:
                body_json = resp.json()
                body_repr: Any = body_json
            except ValueError:
                body_repr = resp.text[:500]

            if status == 401:
                raise UAttendAuthError(
                    "Invalid uAttend API key (HTTP 401).",
                    status=status, body=body_repr,
                )
            if status == 403:
                raise UAttendForbiddenError(
                    "uAttend rejected the request (HTTP 403). "
                    "Likely an IP allowlist on the API gateway — contact uAttend "
                    "support with your server's outbound IP.",
                    status=status, body=body_repr,
                )
            if status == 404:
                raise UAttendError(
                    f"uAttend endpoint not found: {path}",
                    status=status, body=body_repr,
                )
            if status == 429 or 500 <= status < 600:
                if attempt >= self.max_retries:
                    raise (
                        UAttendRateLimitError if status == 429 else UAttendError
                    )(
                        f"uAttend returned HTTP {status} after {attempt + 1} attempts.",
                        status=status, body=body_repr,
                    )
                time.sleep(min(2 ** attempt, 8))
                continue

            raise UAttendError(
                f"Unexpected uAttend response: HTTP {status}.",
                status=status, body=body_repr,
            )

        # Should be unreachable.
        raise UAttendError(f"uAttend request failed: {last_exc}")

    # ── Public API ───────────────────────────────────────────────────────────

    def ping(self) -> dict:
        """Lightweight credential check.

        Calls ``POST /user`` with a filter that returns no rows but still
        validates the key. Filtering by an impossible name keeps the response
        small without relying on undocumented behaviour.
        """
        return self._post("/user", {"Name": "__roobico_ping__"})

    def get_users(
        self,
        *,
        user_id: Optional[int] = None,
        name: Optional[str] = None,
        payroll_number: Optional[str] = None,
    ) -> dict:
        body: dict = {}
        if user_id is not None:
            body["UserId"] = int(user_id)
        if name:
            body["Name"] = str(name)
        if payroll_number:
            body["PayrollNumber"] = str(payroll_number)
        return self._post("/user", body)

    def get_timecard(self, user_id: int, date: Optional[str] = None) -> dict:
        body: dict = {"UserId": int(user_id)}
        if date:
            body["Date"] = str(date)
        return self._post("/timecards", body)
    def get_punches(
        self,
        start_date: str,
        end_date: str,
        *,
        user_ids: Optional[list[int]] = None,
        use_paging: bool = False,
        page_size: Optional[int] = None,
        page_number: Optional[int] = None,
    ) -> dict:
        body: dict = {
            "StartDate": str(start_date),
            "EndDate": str(end_date),
            "UsePaging": bool(use_paging),
        }
        if user_ids:
            body["UserIds"] = [int(x) for x in user_ids]
        if use_paging:
            if page_size is None or page_number is None:
                raise ValueError(
                    "page_size and page_number are required when use_paging=True"
                )
            body["PageSize"] = int(page_size)
            body["PageNumber"] = int(page_number)
        return self._post("/reports/punch", body)
