"""Core tables needed for the retrieval spike (subset of the CLAUDE.md data model).

Row-level security on `document` and `chunk` is defined in the migration, not here.
"""

import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import SPARSEVEC, Vector
from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 1024  # BGE-M3 dense
SPARSE_DIM = 250002  # BGE-M3 vocabulary size


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class KbVersionStatus(enum.StrEnum):
    draft = "draft"
    approved = "approved"
    active = "active"


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
    sparse_embedding = mapped_column(SPARSEVEC(SPARSE_DIM), nullable=True)


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
