"""CSRF protection (double-submit cookie + signed token)."""
import secrets
import hmac
from urllib.parse import parse_qs

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from itsdangerous import URLSafeSerializer, BadSignature

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
COOKIE_NAME = "csrftoken"
HEADER_NAME = "x-csrftoken"
FORM_FIELD = "csrftoken"


class CSRFMiddleware:
    def __init__(self, app: ASGIApp, secret: str, exempt_paths: set[str] | None = None):
        self.app = app
        self.serializer = URLSafeSerializer(secret, salt="csrf")
        self.exempt_paths = exempt_paths or set()

    def _generate(self) -> str:
        return self.serializer.dumps(secrets.token_urlsafe(32))

    def _valid(self, token: str) -> bool:
        try:
            self.serializer.loads(token)
            return True
        except BadSignature:
            return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        cookie_token = request.cookies.get(COOKIE_NAME)
        if not cookie_token or not self._valid(cookie_token):
            cookie_token = self._generate()
        # expose for templates
        scope.setdefault("state", {})
        scope["state"]["csrf_token"] = cookie_token

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        if method not in SAFE_METHODS and path not in self.exempt_paths:
            # read body to extract form token (cache so downstream can read)
            messages = []
            async def receive_wrapper():
                msg = await receive()
                messages.append(msg)
                return msg
            # consume body
            body = b""
            more = True
            while more:
                msg = await receive_wrapper()
                body += msg.get("body", b"")
                more = msg.get("more_body", False)
            # replay receive for app
            idx = 0
            async def replay_receive():
                nonlocal idx
                if idx < len(messages):
                    m = messages[idx]; idx += 1; return m
                return await receive()

            submitted = request.headers.get(HEADER_NAME)
            ctype = (request.headers.get("content-type") or "").lower()
            if not submitted and "application/x-www-form-urlencoded" in ctype:
                try:
                    form = parse_qs(body.decode("utf-8", errors="ignore"), keep_blank_values=True)
                    submitted = (form.get(FORM_FIELD) or [None])[0]
                except Exception:
                    submitted = None
            elif not submitted and "multipart/form-data" in ctype:
                # best-effort: look for field in raw body (boundary text)
                try:
                    text = body.decode("latin-1", errors="ignore")
                    marker = f'name="{FORM_FIELD}"'
                    if marker in text:
                        # crude extract: value after blank line
                        i = text.find(marker)
                        j = text.find("\r\n\r\n", i)
                        if j != -1:
                            k = text.find("\r\n", j+4)
                            if k != -1:
                                submitted = text[j+4:k]
                except Exception:
                    submitted = None

            cookie_sent = request.cookies.get(COOKIE_NAME)
            ok = bool(submitted and cookie_sent and self._valid(submitted) and self._valid(cookie_sent)
                      and hmac.compare_digest(submitted, cookie_sent))
            if not ok:
                resp = PlainTextResponse("CSRF token inválido ou ausente.", status_code=403)
                # still ensure cookie set
                if COOKIE_NAME not in request.cookies:
                    resp.set_cookie(COOKIE_NAME, cookie_token, httponly=False, samesite="lax", secure=request.url.scheme == "https", path="/")
                await resp(scope, replay_receive, send)
                return

            async def send_wrapper(message: Message):
                if message["type"] == "http.response.start" and COOKIE_NAME not in request.cookies:
                    headers = MutableHeaders(scope=message)
                    # set cookie if not present
                    cookie_val = f"{COOKIE_NAME}={cookie_token}; Path=/; SameSite=lax"
                    if request.url.scheme == "https":
                        cookie_val += "; Secure"
                    headers.append("set-cookie", cookie_val)
                await send(message)
            await self.app(scope, replay_receive, send_wrapper)
            return

        async def send_wrapper(message: Message):
            if message["type"] == "http.response.start" and COOKIE_NAME not in request.cookies:
                headers = MutableHeaders(scope=message)
                cookie_val = f"{COOKIE_NAME}={cookie_token}; Path=/; SameSite=lax"
                if request.url.scheme == "https":
                    cookie_val += "; Secure"
                headers.append("set-cookie", cookie_val)
            await send(message)
        await self.app(scope, receive, send_wrapper)


def get_csrf_token(request: Request) -> str:
    return getattr(request.state, "csrf_token", "") or request.cookies.get(COOKIE_NAME, "")
