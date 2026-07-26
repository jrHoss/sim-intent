"""SQLAlchemy persistence and unit-of-work service for R1 source models."""

from __future__ import annotations

import mimetypes
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Iterator

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    event,
    func,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from app.blob_store import BlobStore


def uuid4_string() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class PersistenceNotFoundError(LookupError):
    def __init__(self, resource: str):
        super().__init__(f"{resource} not found")
        self.resource = resource


class PersistenceConflictError(RuntimeError):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_string)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    models: Mapped[list["Model"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Model(Base):
    __tablename__ = "models"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_string)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    project: Mapped[Project] = relationship(back_populates="models")
    versions: Mapped[list["ModelVersion"]] = relationship(
        back_populates="model",
        cascade="all, delete-orphan",
        order_by="ModelVersion.version",
    )


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("model_id", "version", name="uq_model_version_number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_string)
    model_id: Mapped[str] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    model_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    blob_key: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    model: Mapped[Model] = relationship(back_populates="versions")


@event.listens_for(ModelVersion, "before_update")
def _immutable_model_version(*_args) -> None:
    raise ValueError("ModelVersion records are immutable")


def create_sqlite_engine(database_url: str) -> Engine:
    engine = create_engine(database_url)

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


class Persistence:
    def __init__(
        self,
        engine: Engine,
        blobs: BlobStore,
        *,
        after_blob_publish: Callable[[], None] | None = None,
    ):
        self.engine = engine
        self.blobs = blobs
        self._after_blob_publish = after_blob_publish
        self.sessions = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        with self.sessions() as session, session.begin():
            yield session

    def create_project(self, name: str) -> Project:
        project = Project(name=name)
        with self.transaction() as session:
            session.add(project)
        return project

    def list_projects(self) -> list[Project]:
        with self.sessions() as session:
            return list(session.scalars(select(Project).order_by(Project.created_at, Project.id)))

    def get_project(self, project_id: str) -> Project | None:
        with self.sessions() as session:
            return session.get(Project, project_id)

    def get_model(self, model_id: str) -> Model | None:
        with self.sessions() as session:
            return session.get(Model, model_id)

    def create_model_version(
        self,
        *,
        project_id: str,
        source_name: str,
        content: bytes,
        model_kind: str,
        model_id: str | None = None,
    ) -> tuple[Model, ModelVersion]:
        # One process-wide lock is shared by every BlobStore for this canonical
        # root. It serializes publication + commit with version allocation and
        # orphan cleanup, including across separate application/engine instances.
        with self.blobs.coordination_lock:
            digest = self.blobs.digest(content)
            blob_key = self.blobs.publish(content, digest)
            if self._after_blob_publish is not None:
                self._after_blob_publish()
            media_type = (
                mimetypes.guess_type(source_name)[0]
                or "application/octet-stream"
            )
            model = Model(project_id=project_id) if model_id is None else None
            version_record: ModelVersion
            with self.transaction() as session:
                project = session.get(Project, project_id)
                if project is None:
                    raise PersistenceNotFoundError("project")
                if model is None:
                    model = session.get(Model, model_id)
                    if model is None:
                        raise PersistenceNotFoundError("model")
                    if model.project_id != project_id:
                        raise PersistenceConflictError(
                            "model does not belong to project"
                        )
                else:
                    session.add(model)
                    session.flush()
                next_version = session.scalar(
                    select(
                        func.coalesce(func.max(ModelVersion.version), 0) + 1
                    ).where(ModelVersion.model_id == model.id)
                )
                version_record = ModelVersion(
                    id=uuid4_string(),
                    model_id=model.id,
                    version=int(next_version),
                    source_sha256=digest,
                    source_name=source_name,
                    size_bytes=len(content),
                    media_type=media_type,
                    model_kind=model_kind,
                    blob_key=blob_key,
                    created_at=datetime.now(timezone.utc),
                )
                self._insert_model_version(session, version_record)
            return model, version_record

    @staticmethod
    def _insert_model_version(
        session: Session, version_record: ModelVersion
    ) -> None:
        """Insert with one explicit conflict target; propagate all other failures."""
        values = {
            column.name: getattr(version_record, column.name)
            for column in ModelVersion.__table__.columns
        }
        result = session.execute(
            sqlite_insert(ModelVersion)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["model_id", "version"])
        )
        if result.rowcount == 0:
            raise PersistenceConflictError("model version number conflict")

    def list_versions(self, model_id: str) -> list[ModelVersion]:
        with self.sessions() as session:
            return list(session.scalars(select(ModelVersion).where(ModelVersion.model_id == model_id).order_by(ModelVersion.version)))

    def get_version(self, version_id: str) -> ModelVersion | None:
        with self.sessions() as session:
            return session.get(ModelVersion, version_id)

    def read_version_bytes(self, version: ModelVersion) -> bytes:
        return self.blobs.read(version.blob_key, version.source_sha256, version.size_bytes)

    def cleanup_unreferenced_blobs(self, *, limit: int = 100) -> int:
        """Bound cleanup for final blobs left when publication precedes rollback."""
        if limit <= 0:
            return 0
        with self.blobs.coordination_lock:
            with self.sessions() as session:
                referenced = set(session.scalars(select(ModelVersion.blob_key)))
            candidates = [
                path
                for path in self.blobs.iter_final_blobs()
                if path.relative_to(self.blobs.root).as_posix() not in referenced
            ][:limit]
            removed = 0
            for path in candidates:
                key = path.relative_to(self.blobs.root).as_posix()
                # Recheck immediately before deletion while publication and
                # model-version commits remain excluded by the same lock.
                with self.sessions() as session:
                    now_referenced = session.scalar(
                        select(ModelVersion.id)
                        .where(ModelVersion.blob_key == key)
                        .limit(1)
                    )
                try:
                    if (
                        now_referenced is None
                        and path.is_file()
                        and not path.is_symlink()
                    ):
                        path.unlink()
                        removed += 1
                except (
                    FileNotFoundError,
                    NotADirectoryError,
                    IsADirectoryError,
                    PermissionError,
                ):
                    continue
            return removed

    def dispose(self) -> None:
        self.engine.dispose()
