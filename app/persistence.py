"""SQLAlchemy persistence and unit-of-work service for R1 source models."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Iterator
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    String,
    UniqueConstraint,
    create_engine,
    event,
    func,
    inspect as sa_inspect,
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

from app.blob_store import BlobStore, SourceStorageLimitExceededError
from ir.schema import SimulationIntent
from ir.canonical import canonical_intent_document
from ir.versioning import load_simulation_intent


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


class SetupRevisionConflictError(PersistenceConflictError):
    pass


class SetupRequestConflictError(PersistenceConflictError):
    pass


class SetupSourceSupersededError(PersistenceConflictError):
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
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
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
    is_superseded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    model: Mapped[Model] = relationship(back_populates="versions")


class SimulationSetup(Base):
    __table_args__ = (
        UniqueConstraint("project_id", "create_request_id", name="uq_project_setup_request_id"),
    )
    __tablename__ = "simulation_setups"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_string)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"), nullable=False)
    model_version_id: Mapped[str] = mapped_column(ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False)
    current_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    create_request_id: Mapped[str] = mapped_column(String(200), nullable=False)
    create_request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stale_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SetupRevision(Base):
    __tablename__ = "setup_revisions"
    __table_args__ = (
        UniqueConstraint("setup_id", "revision", name="uq_setup_revision_number"),
        UniqueConstraint("setup_id", "request_id", name="uq_setup_request_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_string)
    setup_id: Mapped[str] = mapped_column(ForeignKey("simulation_setups.id", ondelete="CASCADE"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_revision_id: Mapped[str | None] = mapped_column(ForeignKey("setup_revisions.id", ondelete="CASCADE"), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    intent_json: Mapped[str] = mapped_column(Text, nullable=False)
    intent_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mutation_type: Mapped[str] = mapped_column(String(40), nullable=False)
    request_id: Mapped[str] = mapped_column(String(200), nullable=False)
    mutation_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@event.listens_for(ModelVersion, "before_update")
def _immutable_model_version_payload(_mapper, _connection, target) -> None:
    state = sa_inspect(target)
    mutable = {"is_superseded", "superseded_at", "superseded_by_version_id"}
    if any(
        attribute.history.has_changes()
        for name, attribute in state.attrs.items()
        if name not in mutable and name != "model"
    ):
        raise ValueError("ModelVersion records are immutable")


@event.listens_for(SetupRevision, "before_update")
def _immutable_setup_revision(*_args) -> None:
    raise ValueError("SetupRevision records are immutable")


def canonical_intent(intent: SimulationIntent) -> tuple[str, str]:
    canonical = json.dumps(
        canonical_intent_document(intent),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_fingerprint(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_SETUP_LOCKS: dict[str, threading.RLock] = {}
_SETUP_LOCKS_GUARD = threading.Lock()


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
        max_source_storage_bytes: int = 1024 * 1024 * 1024,
    ):
        self.engine = engine
        self.blobs = blobs
        self._after_blob_publish = after_blob_publish
        self.max_source_storage_bytes = max_source_storage_bytes
        self.sessions = sessionmaker(engine, expire_on_commit=False)
        with _SETUP_LOCKS_GUARD:
            self._setup_lock = _SETUP_LOCKS.setdefault(str(engine.url), threading.RLock())

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
            final = self.blobs.path_for_key(self.blobs.key(digest))
            if not final.exists():
                self.cleanup_unreferenced_blobs(limit=100)
                if len(content) > self.max_source_storage_bytes - self.blobs.source_bytes():
                    raise SourceStorageLimitExceededError("source storage capacity exceeded")
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
                    is_superseded=False,
                )
                self._insert_model_version(session, version_record)
                self._supersede_current(session, model, version_record)
            return model, version_record

    def create_model_version_from_file(
        self,
        *,
        project_id: str,
        source_name: str,
        source_path: Path,
        source_sha256: str,
        size_bytes: int,
        model_kind: str,
        model_id: str | None = None,
    ) -> tuple[Model, ModelVersion]:
        with self.blobs.coordination_lock:
            final = self.blobs.path_for_key(self.blobs.key(source_sha256))
            if not final.exists():
                self.cleanup_unreferenced_blobs(limit=100)
            blob_key = self.blobs.publish_file_with_limit(
                source_path, source_sha256, size_bytes,
                self.max_source_storage_bytes,
            )
            if self._after_blob_publish is not None:
                self._after_blob_publish()
            media_type = mimetypes.guess_type(source_name)[0] or "application/octet-stream"
            model = Model(project_id=project_id) if model_id is None else None
            with self.transaction() as session:
                project = session.get(Project, project_id)
                if project is None:
                    raise PersistenceNotFoundError("project")
                if model is None:
                    model = session.get(Model, model_id)
                    if model is None:
                        raise PersistenceNotFoundError("model")
                    if model.project_id != project_id:
                        raise PersistenceConflictError("model does not belong to project")
                else:
                    session.add(model)
                    session.flush()
                next_version = session.scalar(
                    select(func.coalesce(func.max(ModelVersion.version), 0) + 1)
                    .where(ModelVersion.model_id == model.id)
                )
                version_record = ModelVersion(
                    id=uuid4_string(), model_id=model.id, version=int(next_version),
                    source_sha256=source_sha256, source_name=source_name,
                    size_bytes=size_bytes, media_type=media_type,
                    model_kind=model_kind, blob_key=blob_key,
                    created_at=datetime.now(timezone.utc), is_superseded=False,
                )
                self._insert_model_version(session, version_record)
                self._supersede_current(session, model, version_record)
            return model, version_record

    @staticmethod
    def _supersede_current(
        session: Session, model: Model, new_version: ModelVersion
    ) -> None:
        now = new_version.created_at
        if model.current_version_id is not None:
            previous = session.get(ModelVersion, model.current_version_id)
            if previous is None or previous.model_id != model.id:
                raise PersistenceConflictError("invalid current model version")
            previous.is_superseded = True
            previous.superseded_at = now
            previous.superseded_by_version_id = new_version.id
            session.query(SimulationSetup).filter(
                SimulationSetup.model_version_id == previous.id,
                SimulationSetup.is_stale.is_(False),
            ).update(
                {
                    SimulationSetup.is_stale: True,
                    SimulationSetup.stale_reason: "source_replaced",
                    SimulationSetup.stale_at: now,
                },
                synchronize_session=False,
            )
        model.current_version_id = new_version.id

    @staticmethod
    def _insert_model_version(
        session: Session, version_record: ModelVersion
    ) -> None:
        """Insert with one explicit conflict target; propagate all other failures."""
        values = {
            column.name: getattr(version_record, column.name)
            for column in ModelVersion.__table__.columns
        }
        if values["is_superseded"] is None:
            values["is_superseded"] = False
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

    def create_setup(
        self, *, project_id: str, model_id: str, model_version_id: str,
        intent: SimulationIntent, request_id: str,
    ) -> tuple[SimulationSetup, SetupRevision]:
        canonical, intent_digest = canonical_intent(intent)
        create_digest = canonical_fingerprint({
            "model_id": model_id,
            "model_version_id": model_version_id,
            "intent_sha256": intent_digest,
        })
        with self.blobs.coordination_lock, self._setup_lock, self.transaction() as session:
            replay = session.scalar(select(SimulationSetup).where(
                SimulationSetup.project_id == project_id,
                SimulationSetup.create_request_id == request_id,
            ))
            if replay is not None:
                if replay.create_request_sha256 != create_digest:
                    raise SetupRequestConflictError("request ID reused with different setup")
                revision = session.scalar(select(SetupRevision).where(
                    SetupRevision.setup_id == replay.id,
                    SetupRevision.revision == 1,
                ))
                if revision is None:
                    raise RuntimeError("setup creation revision is missing")
                return replay, revision
            project = session.get(Project, project_id)
            model = session.get(Model, model_id)
            version = session.get(ModelVersion, model_version_id)
            if project is None:
                raise PersistenceNotFoundError("project")
            if model is None:
                raise PersistenceNotFoundError("model")
            if version is None:
                raise PersistenceNotFoundError("model version")
            if model.project_id != project_id or version.model_id != model_id:
                raise PersistenceConflictError("invalid setup lineage")
            if model.current_version_id != version.id or version.is_superseded:
                raise SetupSourceSupersededError("setup source is superseded")
            now = datetime.now(timezone.utc)
            setup = SimulationSetup(
                project_id=project_id, model_id=model_id,
                model_version_id=model_version_id, current_revision=None,
                create_request_id=request_id,
                create_request_sha256=create_digest,
                created_at=now, updated_at=now,
            )
            session.add(setup)
            session.flush()
            revision = self._new_revision(
                setup, None, intent, "create", request_id,
                canonical=canonical, digest=intent_digest,
                mutation_digest=create_digest,
            )
            session.add(revision)
            session.flush()
            setup.current_revision = 1
            session.flush()
            return setup, revision

    def list_setups(self, project_id: str) -> list[SimulationSetup]:
        with self.sessions() as session:
            if session.get(Project, project_id) is None:
                raise PersistenceNotFoundError("project")
            return list(session.scalars(select(SimulationSetup).where(
                SimulationSetup.project_id == project_id
            ).order_by(SimulationSetup.created_at, SimulationSetup.id)))

    def get_setup(self, setup_id: str) -> SimulationSetup | None:
        with self.sessions() as session:
            return session.get(SimulationSetup, setup_id)

    def get_revision(self, setup_id: str, revision: int) -> SetupRevision | None:
        with self.sessions() as session:
            return session.scalar(select(SetupRevision).where(
                SetupRevision.setup_id == setup_id,
                SetupRevision.revision == revision,
            ))

    def get_revision_by_request(self, setup_id: str, request_id: str) -> SetupRevision | None:
        with self.sessions() as session:
            return session.scalar(select(SetupRevision).where(
                SetupRevision.setup_id == setup_id,
                SetupRevision.request_id == request_id,
            ))

    def current_setup_revision(self, setup_id: str) -> tuple[SimulationSetup, SetupRevision]:
        with self.sessions() as session:
            setup = session.get(SimulationSetup, setup_id)
            if setup is None:
                raise PersistenceNotFoundError("setup")
            if setup.current_revision is None:
                raise RuntimeError("setup creation is incomplete")
            revision = session.scalar(select(SetupRevision).where(
                SetupRevision.setup_id == setup_id,
                SetupRevision.revision == setup.current_revision,
            ))
            if revision is None:
                raise RuntimeError("setup current revision is invalid")
            return setup, revision

    def list_setup_revisions(self, setup_id: str) -> list[SetupRevision]:
        with self.sessions() as session:
            if session.get(SimulationSetup, setup_id) is None:
                raise PersistenceNotFoundError("setup")
            return list(session.scalars(select(SetupRevision).where(
                SetupRevision.setup_id == setup_id
            ).order_by(SetupRevision.revision)))

    def mutate_setup(
        self, *, setup_id: str, expected_revision: int, request_id: str,
        mutation_type: str, intent: SimulationIntent,
        mutation_payload: dict | None = None,
    ) -> SetupRevision:
        canonical, digest = canonical_intent(intent)
        relevant_payload = mutation_payload or {"intent_sha256": digest}
        mutation_digest = canonical_fingerprint({
            "expected_revision": expected_revision,
            "mutation_type": mutation_type,
            "payload": relevant_payload,
        })
        with self._setup_lock, self.transaction() as session:
            setup = session.get(SimulationSetup, setup_id)
            if setup is None:
                raise PersistenceNotFoundError("setup")
            replay = session.scalar(select(SetupRevision).where(
                SetupRevision.setup_id == setup_id,
                SetupRevision.request_id == request_id,
            ))
            if replay is not None:
                if replay.mutation_sha256 == mutation_digest:
                    return replay
                raise SetupRequestConflictError("request ID reused with different mutation")
            if setup.is_stale:
                raise SetupSourceSupersededError("setup source is superseded")
            if setup.current_revision != expected_revision:
                raise SetupRevisionConflictError("stale setup revision")
            parent = session.scalar(select(SetupRevision).where(
                SetupRevision.setup_id == setup_id,
                SetupRevision.revision == expected_revision,
            ))
            revision = self._new_revision(
                setup, parent, intent, mutation_type, request_id,
                canonical=canonical, digest=digest, mutation_digest=mutation_digest,
            )
            session.add(revision)
            setup.current_revision = revision.revision
            setup.updated_at = revision.created_at
            return revision

    def replay_setup_mutation(
        self, *, setup_id: str, expected_revision: int, request_id: str,
        mutation_type: str, mutation_payload: dict,
    ) -> SetupRevision | None:
        expected_digest = canonical_fingerprint({
            "expected_revision": expected_revision,
            "mutation_type": mutation_type,
            "payload": mutation_payload,
        })
        with self.sessions() as session:
            setup = session.get(SimulationSetup, setup_id)
            if setup is None:
                raise PersistenceNotFoundError("setup")
            replay = session.scalar(select(SetupRevision).where(
                SetupRevision.setup_id == setup_id,
                SetupRevision.request_id == request_id,
            ))
            if replay is None:
                if setup.is_stale:
                    raise SetupSourceSupersededError("setup source is superseded")
                return None
            if replay.mutation_sha256 != expected_digest:
                raise SetupRequestConflictError("request ID reused with different mutation")
            return replay

    @staticmethod
    def _new_revision(
        setup: SimulationSetup, parent: SetupRevision | None,
        intent: SimulationIntent, mutation_type: str, request_id: str,
        *, canonical: str | None = None, digest: str | None = None,
        mutation_digest: str | None = None,
    ) -> SetupRevision:
        canonical, digest = canonical_intent(intent) if canonical is None else (canonical, digest)
        revision = 1 if parent is None else parent.revision + 1
        if mutation_digest is None:
            material = json.dumps(
                {"expected_revision": revision - 1, "intent_sha256": digest,
                 "mutation_type": mutation_type},
                sort_keys=True, separators=(",", ":"),
            )
            mutation_digest = hashlib.sha256(material.encode()).hexdigest()
        return SetupRevision(
            setup_id=setup.id, revision=revision,
            parent_revision_id=None if parent is None else parent.id,
            schema_version=intent.schema_version, intent_json=canonical,
            intent_sha256=digest, mutation_type=mutation_type,
            request_id=request_id, mutation_sha256=mutation_digest,
            created_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def revision_intent(revision: SetupRevision) -> SimulationIntent:
        return load_simulation_intent(revision.intent_json, source="setup revision")

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
