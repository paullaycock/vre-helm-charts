"""Keep the Rucio token file of a running user server fresh.

The hub exchanges the user's IAM login for a Rucio token at spawn, and the
rucio postStart hook writes it to the token file shared by the rucio CLI and
the rucio-jupyterlab extension (oidc_auth "file"). That token expires after
about an hour. This Jupyter server extension asks the hub for a current
token shortly before the file's token expires and replaces the file.

The hub endpoint (`GET /hub/api/users/<name>/rucio-token`, defined in the
chart's hub extraConfig) renews the login with the refresh token it keeps
and returns only the Rucio token: no refresh token reaches the user pod.

Settings (environment): RUCIO_TOKEN_FILE (default /tmp/rucio_oauth.token),
RUCIO_TOKEN_RENEW_LEAD seconds before expiry to renew (default 300; keep it
below the hub's token_refresh_margin, 600 by default). Token values are
never logged.
"""

import asyncio
import base64
import json
import os
import time
from urllib.parse import quote

from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.ioloop import IOLoop

TOKEN_FILE = os.environ.get("RUCIO_TOKEN_FILE", "/tmp/rucio_oauth.token")
LEAD = int(os.environ.get("RUCIO_TOKEN_RENEW_LEAD", "300"))
RETRY = 60       # seconds between attempts after a failure
MAX_SLEEP = 600  # re-check the file at least this often


def token_expiry(token):
    """Expiry (epoch seconds) of a JWT; 0 if it cannot be read."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return 0.0


def file_expiry(path):
    try:
        with open(path) as f:
            return token_expiry(f.read().strip())
    except OSError:
        return 0.0


def write_token(path, token):
    """Replace the file atomically, readable by the user only."""
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    os.replace(tmp, path)


async def fetch_token(api_url, api_token, user):
    request = HTTPRequest(
        f"{api_url.rstrip('/')}/users/{quote(user, safe='')}/rucio-token",
        headers={"Authorization": f"token {api_token}"},
        request_timeout=30,
    )
    response = await AsyncHTTPClient().fetch(request)  # raises on non-2xx
    return json.loads(response.body)["token"]


async def renew_forever(log, api_url, api_token, user, path=TOKEN_FILE, lead=LEAD):
    while True:
        wait = file_expiry(path) - lead - time.time()
        if wait > 0:
            await asyncio.sleep(min(wait, MAX_SLEEP))
            continue
        try:
            token = await fetch_token(api_url, api_token, user)
            left = token_expiry(token) - time.time()
            if left <= lead:
                raise ValueError("the hub returned a token about to expire")
            write_token(path, token)
            log.info("rucio_token_renewal: Rucio token renewed, valid for %d s", left)
        except Exception as e:
            log.warning(
                "rucio_token_renewal: renewal failed (%s %s), retrying in %d s",
                type(e).__name__, getattr(e, "code", ""), RETRY,
            )
            await asyncio.sleep(RETRY)


def _jupyter_server_extension_points():
    return [{"module": "rucio_token_renewal"}]


def _load_jupyter_server_extension(serverapp):
    log = serverapp.log
    api_url = os.environ.get("JUPYTERHUB_API_URL")
    api_token = os.environ.get("JUPYTERHUB_API_TOKEN")
    user = os.environ.get("JUPYTERHUB_USER")
    if not (api_url and api_token and user):
        log.info("rucio_token_renewal: not running under JupyterHub, disabled")
        return
    log.info("rucio_token_renewal: keeping %s fresh", TOKEN_FILE)
    IOLoop.current().add_callback(renew_forever, log, api_url, api_token, user)
