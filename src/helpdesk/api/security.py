"""Security middleware for the local server (design doc section 12.1).

Binding to 127.0.0.1 is necessary but not sufficient. Any process on the
machine can reach a loopback port, and so can JavaScript on any web page
the user happens to have open -- a hostile page cannot *read* the
cross-origin response, but it can absolutely *cause* requests, and this
API records sessions and serves internal runbooks.

Three layers, cheap and independent:

1. **Token.** The launch URL carries a one-time-issued token that is
   exchanged for a ``SameSite=Strict`` cookie. Without it, no API access.
2. **Origin/Host check.** Requests that change state must come from our
   own origin, which blocks the classic cross-site request from a page in
   another tab.
3. **Strict CSP.** Runbook Markdown renders with HTML disabled, and the
   policy forbids inline script, remote script and framing, so a content
   file can never become script execution.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable, Iterable

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

__all__ = ["COOKIE_NAME", "SecurityMiddleware"]

COOKIE_NAME = "helpdesk_token"
TOKEN_QUERY_PARAM = "t"

#: No script from anywhere but ourselves, no inline script, no framing, no
#: outbound connections.  An offline tool has no reason to talk to the
#: internet, so the policy says so out loud.
CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "object-src 'none'"
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    # An offline troubleshooting tool has no business using any of these.
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), interest-cohort=()",
    "Cache-Control": "no-store",
}

#: Paths reachable before the token cookie is set.  Deliberately tiny.
PUBLIC_PATHS = frozenset({"/healthz", "/favicon.ico"})

STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class SecurityMiddleware(BaseHTTPMiddleware):
    """Token gate, origin check and security headers in one pass."""

    def __init__(self, app, *, token: str, allowed_hosts: Iterable[str]) -> None:
        super().__init__(app)
        self.token = token
        self.allowed_hosts = {h.lower() for h in allowed_hosts}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        if path in PUBLIC_PATHS or path.startswith("/static/"):
            return self._harden(await call_next(request))

        # 1 -- token, from the launch URL or from the cookie it set
        supplied = request.query_params.get(TOKEN_QUERY_PARAM) or request.cookies.get(COOKIE_NAME)
        # Constant-time compare: the token is short-lived and local, but a
        # timing oracle on a secret is never worth the two lines it saves.
        if not supplied or not hmac.compare_digest(supplied, self.token):
            return self._deny(request, "invalid or missing access token")

        # 2 -- provenance, for anything that writes
        if request.method in STATE_CHANGING_METHODS:
            denial = self._check_provenance(request)
            if denial is not None:
                return self._deny(request, denial)

        response = await call_next(request)

        # Exchange the URL token for a cookie so it stops appearing in the
        # address bar, in history, and in any Referer the browser sends.
        if request.query_params.get(TOKEN_QUERY_PARAM):
            response.set_cookie(
                COOKIE_NAME,
                self.token,
                httponly=True,
                samesite="strict",
                secure=False,  # loopback HTTP; a secure cookie would never be sent
                path="/",
            )
        return self._harden(response)

    # -- helpers --------------------------------------------------------

    def _check_provenance(self, request: Request) -> str | None:
        """Decide whether a state-changing request really came from our page.

        Returns a denial reason, or ``None`` to allow.

        ``Sec-Fetch-Site`` is checked first because it is the only header
        that answers the question directly, and the browser -- not the
        page -- sets it. ``Origin`` is the fallback for clients that do not
        send it, with one wrinkle worth spelling out: a same-origin form
        POST can legitimately carry ``Origin: null`` (an opaque origin,
        produced by sandboxed contexts and some embedded browsers).
        Rejecting a bare ``null`` on its own would make the UI unusable in
        those clients, while accepting it unconditionally would defeat the
        check -- so it is allowed only when ``Sec-Fetch-Site`` has already
        vouched for the request, and refused otherwise.
        """
        fetch_site = (request.headers.get("sec-fetch-site") or "").lower()
        if fetch_site:
            # "none" is a user-initiated navigation (typed URL, bookmark).
            if fetch_site in {"same-origin", "none"}:
                return None
            return f"cross-origin request (Sec-Fetch-Site: {fetch_site})"

        origin = request.headers.get("origin")
        if origin is not None:
            host = origin.split("//", 1)[-1].lower()
            if host not in self.allowed_hosts:
                return f"cross-origin request from {origin}"
            return None

        referer = request.headers.get("referer")
        if referer is not None:
            host = referer.split("//", 1)[-1].split("/", 1)[0].lower()
            if host not in self.allowed_hosts:
                return f"cross-origin request from {referer}"

        # Neither header present: an old browser or a script. The token has
        # already been verified, and a hostile page cannot read it, so this
        # is allowed rather than breaking non-browser clients of the API.
        return None

    @staticmethod
    def _harden(response: Response) -> Response:
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    def _deny(self, request: Request, reason: str) -> Response:
        body = (
            JSONResponse({"error": "forbidden", "detail": reason}, status_code=403)
            if request.url.path.startswith("/api/")
            else PlainTextResponse(
                "403 — access token required.\n\n"
                "Open the URL printed by 'helpdesk serve'; it carries the token.\n"
                f"({reason})",
                status_code=403,
            )
        )
        return self._harden(body)
