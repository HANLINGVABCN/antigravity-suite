import json
import re

from aiohttp import web

CLIENT_IP_HEADERS = {'forwarded', 'x-forwarded-for', 'x-real-ip', 'cf-connecting-ip',
                     'true-client-ip', 'client-ip', 'x-client-ip', 'fastly-client-ip',
                     'x-original-forwarded-for', 'x-envoy-external-address'}


def content_policy(request):
    # Reject cross-origin browser traffic, including fetch, beacon, frames and fonts.
    # OAuth is a deliberate top-level navigation from the manager, not a subresource.
    host = request.host
    return ("default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
            "connect-src 'self' ws://" + host + " wss://" + host + "; "
            "frame-src 'self' blob:; worker-src 'self' blob:; media-src 'self' data: blob:; "
            "object-src 'none'; base-uri 'self'; form-action 'self'")


def privacy_headers(request):
    return {'Content-Security-Policy': content_policy(request), 'Referrer-Policy': 'no-referrer',
            'X-DNS-Prefetch-Control': 'off', 'X-Content-Type-Options': 'nosniff',
            'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), display-capture=()'}


def inject_privacy(html, profile_id):
    # CSP protects even pages and workers that do not load our JavaScript.
    html = re.sub(rb'<link\b[^>]*\brel=["\x27](?:dns-prefetch|preconnect)["\x27][^>]*>', b'', html, flags=re.I)
    setting = json.dumps(str(profile_id or ''), ensure_ascii=True).replace('<', '\\u003c')
    boot = ('<script>window.__SUITE_PROFILE_ID__=' + setting + ';</script>'
            '<script src="/__suite/privacy.js"></script>').encode()
    match = re.search(rb'<head\b[^>]*>', html, re.I)
    pos = match.end() if match else 0
    return html[:pos] + boot + html[pos:]


@web.middleware
async def privacy_guard(request, handler):
    response = await handler(request)
    if not isinstance(response, web.WebSocketResponse) and not response.prepared:
        for key, value in privacy_headers(request).items():
            response.headers[key] = value
    return response
