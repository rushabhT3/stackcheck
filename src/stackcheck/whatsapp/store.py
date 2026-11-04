"""Per-sender WhatsApp state on disk. Only salted hashes, never phone numbers."""

import hashlib
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def sender_hash(phone: str, app_secret: str) -> str:
    """Stable id for a sender without storing the number itself."""
    return hashlib.sha256(f"{app_secret}|{phone}".encode()).hexdigest()[:32]


class SenderStore:
    """Pending uploads, latest check, hourly counts and seen message ids.

    Seen ids expire after 7 days; media rows older than that are pruned too.
    """

    def __init__(
        self,
        path: Path,
        ttl_days: int = 7,
        checks_per_hour: int = 3,
        uploads_dir: Path | None = None,
    ) -> None:
        self._path = path
        self._ttl_s = ttl_days * 24 * 3600
        self._checks_per_hour = checks_per_hour
        self._uploads_dir = uploads_dir
        self._data: dict = {"senders": {}, "seen": {}}
        self._load()

    def _load(self) -> None:
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {"senders": {}, "seen": {}}
        self._prune(time.time())

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data), encoding="utf-8")
        except OSError as exc:
            logger.warning("WhatsApp store write failed: %s", exc)

    def _prune(self, now: float) -> None:
        cutoff = now - self._ttl_s
        self._data["seen"] = {k: v for k, v in self._data.get("seen", {}).items() if v > cutoff}
        for sender in self._data.get("senders", {}).values():
            sender["checks"] = [t for t in sender.get("checks", []) if now - t < 3600]
        self._sweep_uploads(now)

    def _sweep_uploads(self, now: float) -> int:
        """Delete upload files older than the TTL that no sender is waiting on."""
        if self._uploads_dir is None or not self._uploads_dir.is_dir():
            return 0
        live = {
            upload_id
            for sender in self._data.get("senders", {}).values()
            for upload_id in [*sender.get("pending", []), sender.get("pending_video")]
            if upload_id
        }
        removed = 0
        seen: set[str] = set()
        for pattern in ("*.pdf", "*.mp4", "*.mov", "*.webm"):
            for path in self._uploads_dir.glob(pattern):
                if path.name in seen or path.stem in live:
                    continue
                seen.add(path.name)
                try:
                    if path.stat().st_mtime < now - self._ttl_s:
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
        return removed

    def is_duplicate(self, msg_id: str, now: float | None = None) -> bool:
        """True when this message id was already handled. Records new ones."""
        if not msg_id:
            return False
        moment = time.time() if now is None else now
        self._prune(moment)
        if msg_id in self._data["seen"]:
            return True
        self._data["seen"][msg_id] = moment
        self._save()
        return False

    def _sender(self, hashed: str) -> dict:
        senders = self._data.setdefault("senders", {})
        return senders.setdefault(
            hashed, {"pending": [], "pending_video": None, "latest_check": None, "checks": [], "lang": "en"}
        )

    def note_upload(self, hashed: str, upload_id: str) -> None:
        sender = self._sender(hashed)
        if upload_id not in sender["pending"]:
            sender["pending"].append(upload_id)
        self._save()

    def note_video(self, hashed: str, upload_id: str) -> None:
        """Keep at most one pending site video per sender."""
        self._sender(hashed)["pending_video"] = upload_id
        self._save()

    def take_video(self, hashed: str) -> str | None:
        sender = self._sender(hashed)
        video = sender.get("pending_video")
        sender["pending_video"] = None
        self._save()
        return video if isinstance(video, str) else None

    def take_pending(self, hashed: str) -> list[str]:
        sender = self._sender(hashed)
        pending = list(sender["pending"])
        sender["pending"] = []
        self._save()
        return pending

    def pending_count(self, hashed: str) -> int:
        return len(self._sender(hashed)["pending"])

    def can_check(self, hashed: str, now: float | None = None) -> bool:
        moment = time.time() if now is None else now
        self._prune(moment)
        return len(self._sender(hashed)["checks"]) < self._checks_per_hour

    def note_check(self, hashed: str, check_id: str, now: float | None = None) -> None:
        moment = time.time() if now is None else now
        sender = self._sender(hashed)
        sender["checks"].append(moment)
        sender["latest_check"] = check_id
        self._save()

    def latest_check(self, hashed: str) -> str | None:
        return self._sender(hashed).get("latest_check")

    def note_lang(self, hashed: str, lang: str) -> None:
        self._sender(hashed)["lang"] = lang
        self._save()

    def lang(self, hashed: str) -> str:
        return self._sender(hashed).get("lang", "en")
