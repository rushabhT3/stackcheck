"""WhatsApp Cloud API client. Holds the connection; plain methods around it."""

import hashlib
import hmac
import logging

import httpx

from stackcheck.config import Settings
from stackcheck.errors import StackCheckError

logger = logging.getLogger(__name__)

GRAPH_VERSION = "v26.0"  # current per Meta docs, checked Nov 2026
GRAPH_ROOT = f"https://graph.facebook.com/{GRAPH_VERSION}"


class WhatsAppError(StackCheckError):
    """The WhatsApp API refused or failed the call. Details stay in the log."""


class WhatsAppClient:
    """Send and fetch WhatsApp media with the system-user token."""

    def __init__(self, settings: Settings) -> None:
        self._token = settings.whatsapp_token
        self._phone_id = settings.whatsapp_phone_number_id

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def send_text(self, to: str, body: str) -> None:
        """Send a text message. Link previews stay on so report links unfurl."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{GRAPH_ROOT}/{self._phone_id}/messages",
                headers=self._headers(),
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "text",
                    "text": {"body": body, "preview_url": True},
                },
            )
        if response.status_code != 200:
            logger.error("WhatsApp send failed: HTTP %s: %s", response.status_code, response.text[:200])
            raise WhatsAppError("The reply could not be sent.")

    async def upload_media(self, data: bytes, mime_type: str) -> str:
        """Upload bytes, returning a media id for a later image message."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{GRAPH_ROOT}/{self._phone_id}/media",
                headers=self._headers(),
                files={"file": ("evidence.jpg", data, mime_type)},
                data={"messaging_product": "whatsapp"},
            )
        if response.status_code != 200:
            logger.error("WhatsApp upload failed: HTTP %s: %s", response.status_code, response.text[:200])
            raise WhatsAppError("The evidence photo could not be sent.")
        return str(response.json().get("id", ""))

    async def send_image(self, to: str, media_id: str, caption: str) -> None:
        """Send an uploaded image by media id."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{GRAPH_ROOT}/{self._phone_id}/messages",
                headers=self._headers(),
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "image",
                    "image": {"id": media_id, "caption": caption[:1024]},
                },
            )
        if response.status_code != 200:
            logger.error("WhatsApp image failed: HTTP %s: %s", response.status_code, response.text[:200])
            raise WhatsAppError("The evidence photo could not be sent.")

    async def media_url(self, media_id: str) -> str:
        """Direct download URL for an incoming media id."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{GRAPH_ROOT}/{media_id}", headers=self._headers())
        if response.status_code != 200:
            logger.error("WhatsApp media lookup failed: HTTP %s", response.status_code)
            raise WhatsAppError("The attachment could not be downloaded.")
        return str(response.json().get("url", ""))

    async def download_media(self, url: str, cap_bytes: int) -> bytes:
        """Download incoming bytes with a size cap. Never logs the body."""
        chunks: list[bytes] = []
        size = 0
        async with (
            httpx.AsyncClient(timeout=60.0) as client,
            client.stream("GET", url, headers=self._headers()) as response,
        ):
            if response.status_code != 200:
                raise WhatsAppError("The attachment could not be downloaded.")
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size > cap_bytes:
                    raise WhatsAppError("That file is too large.")
        return b"".join(chunks)


def valid_signature(raw: bytes, header: str, app_secret: str) -> bool:
    """Constant-time check of X-Hub-Signature-256 against the raw body."""
    if not header.startswith("sha256=") or not app_secret:
        return False
    expected = hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256=") :])
