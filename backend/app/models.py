"""Database models for Z CLIPS.

Stage 1 models (User, AuditLog) are preserved and extended. New tables cover
subscriptions, credit ledger, promotion seats, NayaPay payment requests,
clip jobs/clips, support threads, activity feed, YouTube monitoring and
app settings / model registry. Nothing here stores secrets.
"""
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Float, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="customer", index=True)
    plan: Mapped[str] = mapped_column(String(16), default="free")
    # Available clip credits (balance). Spent when a clip job is created.
    credits: Mapped[int] = mapped_column(Integer, default=5)
    # Clips used in the current subscription period (display only).
    clips_used: Mapped[int] = mapped_column(Integer, default=0)
    plan_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    plan_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    display_name: Mapped[str] = mapped_column(String(80), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    # Owner-granted complimentary premium (lifetime Pro-equivalent, no payment).
    complimentary_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    @property
    def effective_plan(self) -> str:
        """Owner and complimentary-premium users are treated as Pro."""
        if self.role == "owner" or self.complimentary_premium:
            return "pro"
        return self.plan


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Subscription(Base):
    """One subscription period per row. Renewal creates a new row."""
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    credits_granted: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active|expired|cancelled
    is_promotional: Mapped[bool] = mapped_column(Boolean, default=False)
    promo_price_pkr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payment_request_id: Mapped[int | None] = mapped_column(ForeignKey("payment_requests.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreditLedger(Base):
    """Append-only record of every credit change (auditability)."""
    __tablename__ = "credit_ledger"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(120))
    ref_type: Mapped[str] = mapped_column(String(40), default="")
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Promotion(Base):
    """Singleton row (id=1) controlling the Pro launch promotion."""
    __tablename__ = "promotions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="Pro launch promotion")
    discount_pct: Mapped[int] = mapped_column(Integer, default=25)
    promo_price_pkr: Mapped[int] = mapped_column(Integer, default=5250)
    normal_price_pkr: Mapped[int] = mapped_column(Integer, default=7000)
    max_seats: Mapped[int] = mapped_column(Integer, default=20)
    seats_reserved: Mapped[int] = mapped_column(Integer, default=0)
    seats_used: Mapped[int] = mapped_column(Integer, default=0)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def seats_remaining(self) -> int:
        return max(0, self.max_seats - self.seats_reserved - self.seats_used)


class PaymentRequest(Base):
    """A manual NayaPay payment submitted by a customer."""
    __tablename__ = "payment_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan: Mapped[str] = mapped_column(String(16))
    amount_pkr: Mapped[int] = mapped_column(Integer)
    is_promotional: Mapped[bool] = mapped_column(Boolean, default=False)
    txn_id: Mapped[str] = mapped_column(String(120), index=True)
    screenshot_path: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|approved|rejected
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    review_note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceVideo(Base):
    """An uploaded source video (or an authorised source URL)."""
    __tablename__ = "source_videos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    source_type: Mapped[str] = mapped_column(String(16), default="upload")  # upload|url
    original_filename: Mapped[str] = mapped_column(String(255), default="")
    stored_path: Mapped[str] = mapped_column(String(500), default="")
    source_url: Mapped[str] = mapped_column(String(1000), default="")
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="uploaded", index=True)  # uploaded|ready|failed
    error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ClipJob(Base):
    """One clip-generation job (background worker processes it)."""
    __tablename__ = "clip_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source_videos.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(
        String(16), default="queued", index=True
    )  # queued|analyzing|rendering|qc|done|failed|needs_model
    progress: Mapped[int] = mapped_column(Integer, default=0)
    log: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(String(1000), default="")
    caption_style: Mapped[str] = mapped_column(String(40), default="simple")
    export_height: Mapped[int] = mapped_column(Integer, default=1280)
    # Optional trim range for re-render jobs (from the clip editor).
    # None means "use the full source".
    trim_start_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    trim_end_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    facecam_mode: Mapped[str] = mapped_column(String(16), default="auto")  # auto|manual|off
    facecam_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    facecam_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    facecam_w: Mapped[float | None] = mapped_column(Float, nullable=True)
    facecam_h: Mapped[float | None] = mapped_column(Float, nullable=True)
    # YIZERGE-style editor fields (Lite + Pro via manual_editing).
    aspect_ratio: Mapped[str] = mapped_column(String(8), default="9:16")  # 9:16|1:1|4:3|16:9
    layout: Mapped[str] = mapped_column(String(16), default="standard")  # standard|facecam_top|facecam_side
    caption_size: Mapped[str] = mapped_column(String(8), default="medium")  # small|medium|large
    caption_position: Mapped[str] = mapped_column(String(8), default="bottom")  # bottom|middle|top
    caption_bg: Mapped[str] = mapped_column(String(8), default="outline")  # none|box|outline
    sticker_text: Mapped[str] = mapped_column(String(40), default="")
    post_author: Mapped[str] = mapped_column(String(40), default="")
    post_text: Mapped[str] = mapped_column(String(140), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    worker_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Clip(Base):
    """One generated vertical clip (a candidate until the user approves it)."""
    __tablename__ = "clips"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("clip_jobs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source_videos.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    start_s: Mapped[float] = mapped_column(Float, default=0.0)
    end_s: Mapped[float] = mapped_column(Float, default=0.0)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    hook_score: Mapped[float] = mapped_column(Float, default=0.0)
    file_path: Mapped[str] = mapped_column(String(500), default="")
    caption_path: Mapped[str] = mapped_column(String(500), default="")
    thumb_path: Mapped[str] = mapped_column(String(500), default="")
    width: Mapped[int] = mapped_column(Integer, default=720)
    height: Mapped[int] = mapped_column(Integer, default=1280)
    status: Mapped[str] = mapped_column(String(16), default="candidate", index=True)  # candidate|approved|rejected
    qc_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    qc_report: Mapped[str] = mapped_column(Text, default="{}")
    downloads: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SupportThread(Base):
    __tablename__ = "support_threads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    subject: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open|closed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SupportMessage(Base):
    __tablename__ = "support_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("support_threads.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    author_role: Mapped[str] = mapped_column(String(16), default="customer")
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Activity(Base):
    """User-visible activity feed entries."""
    __tablename__ = "activities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    detail: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class YTChannel(Base):
    """A YouTube channel a Pro user monitors. Live status is polled via the
    YouTube Data API when a key is configured; ingestion only ever happens
    from an authorised source the user provides (never arbitrary streams)."""
    __tablename__ = "yt_channels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    channel_url: Mapped[str] = mapped_column(String(500), default="")
    channel_handle: Mapped[str] = mapped_column(String(120), default="")
    channel_id: Mapped[str] = mapped_column(String(120), default="")
    display_name: Mapped[str] = mapped_column(String(200), default="")
    is_live: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_status: Mapped[str] = mapped_column(String(40), default="not_checked")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    live_video_id: Mapped[str] = mapped_column(String(40), default="")
    live_title: Mapped[str] = mapped_column(String(300), default="")
    # waiting_authorized | authorized | not_configured
    ingestion_state: Mapped[str] = mapped_column(String(24), default="waiting_authorized")
    authorized_source_note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    """Owner-only global settings (key/value)."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelInfo(Base):
    """Registry for local AI models managed by the model manager."""
    __tablename__ = "models"
    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    version: Mapped[str] = mapped_column(String(40), default="")
    size_mb: Mapped[int] = mapped_column(Integer, default=0)
    license: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(20), default="not_installed")  # not_installed|installing|installed|failed
    path: Mapped[str] = mapped_column(String(500), default="")
    error: Mapped[str] = mapped_column(String(500), default="")
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
