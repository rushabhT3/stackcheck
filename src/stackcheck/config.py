from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="STACKCHECK_",
        extra="ignore",
        validate_by_name=True,
        validate_by_alias=True,  # lets tests pass meta_api_key="..." by field name
    )

    # Meta Model API
    meta_api_key: str = Field(default="", validation_alias="MODEL_API_KEY")
    meta_base_url: str = Field(default="https://api.meta.ai/v1", validation_alias="META_BASE_URL")
    reader_model: str = "muse-spark-1.3-contributor"  # public filings (D07)
    private_model: str = "muse-spark-1.3"  # anything a user uploads (D07)
    sam_model: str = "sam-3.1"
    sam_image_mode: Literal["data_url", "file_id"] = "data_url"  # confirmed by T03 probe
    sam_prompts: str = "window,window opening"  # set by T07 bench, comma separated
    reasoning_effort: str = "low"  # "" disables the parameter (UNVERIFIED shape)

    # Reader provider switch (D06)
    extract_provider: Literal["meta", "baseten", "fireworks"] = "meta"
    baseten_api_key: str = Field(default="", validation_alias="BASETEN_API_KEY")
    baseten_base_url: str = Field(
        default="https://inference.baseten.co/v1", validation_alias="BASETEN_BASE_URL"
    )
    baseten_model: str = Field(default="meta-models/muse-spark-open-1026", validation_alias="BASETEN_MODEL")
    fireworks_api_key: str = Field(default="", validation_alias="FIREWORKS_API_KEY")
    fireworks_base_url: str = Field(
        default="https://api.fireworks.ai/inference/v1", validation_alias="FIREWORKS_BASE_URL"
    )
    fireworks_model: str = Field(
        default="accounts/fireworks/models/muse-spark-open", validation_alias="FIREWORKS_MODEL"
    )

    # App
    data_dir: Path = Path("data")
    samples_file: Path = Path("data/samples.json")  # list of {"check_id", "project_label", "headline"}
    web_dist_dir: Path = Path("web/dist")
    redact_names: bool = False  # True on the public deploy: hides project, promoter, reg no
    budget_usd: Decimal = Decimal("150")
    alert_usd: Decimal = Decimal("100")
    live: bool = False
    max_concurrent_checks: int = 2
    max_queue: int = 10
    allowed_hosts: str = (
        "rera.bihar.gov.in,up-rera.in,www.up-rera.in,rerait.telangana.gov.in,"
        "maharera.maharashtra.gov.in,rera.karnataka.gov.in,gujrera.gujarat.gov.in,haryanarera.gov.in"
    )
    stamp_required_states: str = "Bihar"
    fetch_min_interval_s: float = 5.0
    fetch_connect_timeout_s: float = 10.0
    fetch_read_timeout_s: float = 30.0
    fetch_doc_timeout_s: float = 90.0
    check_timeout_s: float = 1500.0
    per_ip_active_max: int = 3
    per_ip_hourly_max: int = 20
    fetch_max_bytes: int = 30_000_000
    fetch_mumbai_hosts: str = ""  # comma list; these hosts fetch via the Mumbai function
    video_max_bytes: int = 50_000_000
    video_max_seconds: float = 60.0
    summary_languages: str = "en"  # comma separated; deploy uses "en,hi"
    public_base_url: str = ""  # deploy sets the Modal URL so WhatsApp replies carry links
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_checks_per_hour: int = 3
    whatsapp_media_ttl_days: int = 7


def get_settings() -> Settings:
    return Settings()


def model_for(origin: Literal["url", "upload"], settings: Settings) -> str:
    return settings.reader_model if origin == "url" else settings.private_model
