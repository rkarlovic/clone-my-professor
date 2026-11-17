"""Core tables needed for the retrieval spike (subset of the CLAUDE.md data model).

Row-level security on `document` and `chunk` is defined in the migration, not here.
"""

import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 1024  # BGE-M3 dense


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class KbVersionStatus(enum.StrEnum):
    draft = "draft"
    approved = "approved"
    active = "active"


class IngestStatus(enum.StrEnum):
    """Whether a version's material has been processed (separate from approval, rule 5)."""

    queued = "queued"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class ContentType(enum.StrEnum):
    definition = "definition"
    example = "example"
    exercise = "exercise"
    solution = "solution"
    other = "other"


class ForwardReason(enum.StrEnum):
    """Why the clone forwarded a question to the teacher instead of answering (rule 2)."""

    admin = "admin"  # grades, deadlines, exams, obligations
    out_of_scope = "out_of_scope"  # not about this subject
    low_score = "low_score"  # nothing retrieved above the score floor
    no_source = "no_source"  # the model found no answer in the sources, or cited none
    failed_check = "failed_check"  # the `check` role found the answer off-target or contradictory


class TeacherQuestionStatus(enum.StrEnum):
    open = "open"
    answered = "answered"
    dismissed = "dismissed"


class Teacher(Base):
    __tablename__ = "teacher"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200))
    institution_id: Mapped[str | None] = mapped_column(String(100))
    consent_voice: Mapped[bool] = mapped_column(default=False)
    consent_avatar: Mapped[bool] = mapped_column(default=False)
    # sha256 of (LMS issuer, LMS user id) for teachers who arrive by LTI; never the id itself
    external_ref: Mapped[str | None] = mapped_column(String(64), unique=True)


class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"
    id: Mapped[uuid.UUID] = _pk()
    teacher_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teacher.id"))
    subject_name: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(30), default="active")


class KbVersion(Base):
    __tablename__ = "kb_version"
    id: Mapped[uuid.UUID] = _pk()
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_base.id"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[KbVersionStatus] = mapped_column(
        Enum(KbVersionStatus, name="kb_version_status"), default=KbVersionStatus.draft
    )
    ingest_status: Mapped[IngestStatus] = mapped_column(
        Enum(IngestStatus, name="ingest_status"), default=IngestStatus.queued
    )
    ingest_error: Mapped[str | None] = mapped_column(Text)
    ingest_report: Mapped[dict | None] = mapped_column(JSONB)
    # Uploaded files still to process: [{"file_name", "file_hash", "path"}].
    pending_files: Mapped[list[dict]] = mapped_column(JSONB, default=list, server_default="[]")
    # Unchanged documents of this version are copied from here (no re-processing).
    base_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("kb_version.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teacher.id"))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin: Mapped[str] = mapped_column(String(20), default="upload")  # upload | lms
    # file names this version leaves out of its base version
    removed_files: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    auto_activate: Mapped[bool] = mapped_column(default=False)  # LMS removals only
    # Background check after processing: queued | running | done | failed (None: never run)
    check_status: Mapped[str | None] = mapped_column(String(20))
    check_report: Mapped[dict | None] = mapped_column(JSONB)


class Document(Base):
    __tablename__ = "document"
    id: Mapped[uuid.UUID] = _pk()
    kb_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("kb_version.id"), index=True)
    source_platform: Mapped[str] = mapped_column(String(30), default="upload")
    external_id: Mapped[str | None] = mapped_column(String(300))
    file_name: Mapped[str] = mapped_column(String(500))
    file_hash: Mapped[str] = mapped_column(String(64))
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chunk(Base):
    __tablename__ = "chunk"
    id: Mapped[uuid.UUID] = _pk()
    kb_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("kb_version.id"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chunk.id"))
    text: Mapped[str] = mapped_column(Text)  # clean original text, used for citations
    context: Mapped[str | None] = mapped_column(Text)  # from the `context` role
    heading_path: Mapped[str | None] = mapped_column(Text)  # "Subject › Chapter › Section"
    content_type: Mapped[ContentType] = mapped_column(
        Enum(ContentType, name="content_type"), default=ContentType.other
    )
    page_or_slide: Mapped[int | None] = mapped_column(Integer)
    # Other pages with the same content (duplicates folded at ingestion): cite them too.
    also_on_pages: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), default=list, server_default="{}"
    )
    week_topic: Mapped[str | None] = mapped_column(String(200))
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=True)


class TeacherQuestion(Base):
    """A forwarded question in the teacher's queue. Holds student text: personal data (rule 9)."""

    __tablename__ = "teacher_question"
    id: Mapped[uuid.UUID] = _pk()
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_base.id"))
    kb_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("kb_version.id"))
    student_ref: Mapped[str | None] = mapped_column(String(200))  # opaque id, never a name
    question: Mapped[str] = mapped_column(Text)
    reason: Mapped[ForwardReason] = mapped_column(Enum(ForwardReason, name="forward_reason"))
    status: Mapped[TeacherQuestionStatus] = mapped_column(
        Enum(TeacherQuestionStatus, name="teacher_question_status"),
        default=TeacherQuestionStatus.open,
    )
    answer: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teacher.id"))
    # [{"file_name", "page"}] the teacher pointed to: question + source for the collected set
    answer_sources: Mapped[list[dict]] = mapped_column(JSONB, default=list, server_default="[]")


class ChatMessage(Base):
    """One student question and the clone's reply. Holds student text: personal data (rule 9)."""

    __tablename__ = "chat_message"
    id: Mapped[uuid.UUID] = _pk()
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_base.id"))
    kb_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("kb_version.id"))
    student_ref: Mapped[str] = mapped_column(String(200))  # opaque id, never a name
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)  # what the student saw
    forwarded: Mapped[ForwardReason | None] = mapped_column(
        Enum(ForwardReason, name="forward_reason")
    )
    teacher_question_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teacher_question.id"))
    # [{"n", "file_name", "pages"}]: the sources the answer cites, by their [n] markers
    citations: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    trace: Mapped[dict] = mapped_column(JSONB, default=dict)  # versions and model ids
    seconds: Mapped[dict] = mapped_column(JSONB, default=dict)
    feedback: Mapped[int | None] = mapped_column(Integer)  # 1 helpful, -1 not helpful
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CheckQuestion(Base):
    """A student-style question the model wrote from one slide (the auto-generated check set).
    Valid for every version holding the same file content (file_name + file_hash)."""

    __tablename__ = "check_question"
    id: Mapped[uuid.UUID] = _pk()
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_base.id"))
    question: Mapped[str] = mapped_column(Text)
    style: Mapped[str] = mapped_column(String(30))
    file_name: Mapped[str] = mapped_column(String(500))
    file_hash: Mapped[str] = mapped_column(String(64))
    page: Mapped[int] = mapped_column(Integer)
    created_for_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("kb_version.id"))
    trace: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PersonaConfig(Base):
    """The teacher's settings for how the clone talks (instruction layer 2), versioned."""

    __tablename__ = "persona_config"
    id: Mapped[uuid.UUID] = _pk()
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_base.id"))
    version: Mapped[int] = mapped_column(Integer)
    # draft | checking | active | rejected | retired
    status: Mapped[str] = mapped_column(String(20), default="draft")
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    free_text: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teacher.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_report: Mapped[dict | None] = mapped_column(JSONB)


class LtiPlatform(Base):
    """A registered LMS (LTI 1.3 platform): who may launch CMP, and where its keys are."""

    __tablename__ = "lti_platform"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200))
    issuer: Mapped[str] = mapped_column(String(500))
    client_id: Mapped[str] = mapped_column(String(200))
    auth_login_url: Mapped[str] = mapped_column(String(500))
    jwks_url: Mapped[str] = mapped_column(String(500))
    deployment_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    # Web Services (material sync): the base URL and the NAME of the env var with the token
    ws_base_url: Mapped[str | None] = mapped_column(String(500))
    ws_token_env: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LtiLoginState(Base):
    """One OIDC login round trip (state + nonce); short-lived and single-use."""

    __tablename__ = "lti_login_state"
    state: Mapped[str] = mapped_column(String(100), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(100))
    platform_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lti_platform.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlatformContextMap(Base):
    """An LMS course (LTI context) linked to a CMP course; many LMS courses -> one KB."""

    __tablename__ = "platform_context_map"
    id: Mapped[uuid.UUID] = _pk()
    platform_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lti_platform.id"))
    context_id: Mapped[str] = mapped_column(String(300))
    context_title: Mapped[str | None] = mapped_column(String(500))
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_base.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sync_enabled: Mapped[bool] = mapped_column(default=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_status: Mapped[str | None] = mapped_column(String(20))
    last_sync_report: Mapped[dict | None] = mapped_column(JSONB)
    # LMS sections whose new files are published without review; empty = always ask
    auto_add_sections: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)


class LmsFile(Base):
    """A file last seen in a linked LMS course, so unchanged files are not downloaded again."""

    __tablename__ = "lms_file"
    id: Mapped[uuid.UUID] = _pk()
    map_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform_context_map.id"))
    external_id: Mapped[str] = mapped_column(String(1000))
    file_name: Mapped[str] = mapped_column(String(500))
    filesize: Mapped[int] = mapped_column(BigInteger)
    timemodified: Mapped[int] = mapped_column(BigInteger)
    file_hash: Mapped[str | None] = mapped_column(String(64))
    section: Mapped[str] = mapped_column(String(500), default="")
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CourseTeacher(Base):
    """A teacher who may manage a course (several per course)."""

    __tablename__ = "course_teacher"
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_base.id"), primary_key=True)
    teacher_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teacher.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), default="teacher")  # owner | teacher
    added_via: Mapped[str] = mapped_column(String(20), default="owner")  # owner | lti
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    """An in-app notice for one teacher or one student (opaque ref) of a course."""

    __tablename__ = "notification"
    id: Mapped[uuid.UUID] = _pk()
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_base.id"))
    teacher_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teacher.id"))
    student_ref: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
