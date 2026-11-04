"""WhatsApp Cloud API webhook payloads. Only the fields StackCheck reads."""

from pydantic import BaseModel, Field


class TextBody(BaseModel):
    body: str = ""


class MediaRef(BaseModel):
    id: str = ""
    mime_type: str = ""
    filename: str | None = None


class IncomingMessage(BaseModel):
    sender: str = Field(default="", alias="from")
    msg_id: str = Field(default="", alias="id")
    timestamp: str = ""
    type: str = ""
    text: TextBody | None = None
    document: MediaRef | None = None
    image: MediaRef | None = None
    audio: MediaRef | None = None
    video: MediaRef | None = None


class Metadata(BaseModel):
    phone_number_id: str = ""


class Value(BaseModel):
    messaging_product: str = ""
    metadata: Metadata = Metadata()
    messages: list[IncomingMessage] = []


class Change(BaseModel):
    value: Value = Value()


class Entry(BaseModel):
    changes: list[Change] = []


class WhatsAppUpdate(BaseModel):
    object: str = ""
    entry: list[Entry] = []


class FlatMessage(BaseModel):
    """One actionable message, statuses already filtered out."""

    sender: str
    msg_id: str
    kind: str  # text, document, image, audio, other
    body: str = ""
    media_id: str = ""
    mime_type: str = ""
    filename: str = ""


def iter_messages(update: WhatsAppUpdate) -> list[FlatMessage]:
    """Flatten message entries. Status callbacks carry no message and are skipped."""
    flat: list[FlatMessage] = []
    for entry in update.entry:
        for change in entry.changes:
            for message in change.value.messages:
                kind = (
                    message.type
                    if message.type in ("text", "document", "image", "audio", "video")
                    else "other"
                )
                ref = {
                    "document": message.document,
                    "image": message.image,
                    "audio": message.audio,
                    "video": message.video,
                }.get(message.type)
                flat.append(
                    FlatMessage(
                        sender=message.sender,
                        msg_id=message.msg_id,
                        kind=kind,
                        body=(message.text.body if message.text else ""),
                        media_id=ref.id if ref else "",
                        mime_type=ref.mime_type if ref else "",
                        filename=ref.filename or "" if ref else "",
                    )
                )
    return flat
