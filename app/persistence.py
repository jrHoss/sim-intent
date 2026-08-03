"""SQLAlchemy persistence and unit-of-work service for R1 source models."""

from __future__ import annotations

import hashlib
import hmac
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
    LargeBinary,
    Text,
    String,
    UniqueConstraint,
    create_engine,
    event,
    func,
    inspect as sa_inspect,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from app.blob_store import BlobIntegrityError, BlobStore, SourceStorageLimitExceededError
from ir.schema import (
    AmbiguousCadFaceTarget,
    CAD_ENTITY_IDS_FORBIDDEN_CODE,
    EngineeringConsistencyError,
    Region,
    ResolvedCadFaceTarget,
    SimulationIntent,
    UnresolvedCadFaceTarget,
    enforce_cad_region_entity_ids_invariant,
    region_entity_membership,
)
from ir.canonical import canonical_intent_document
from ir.versioning import load_simulation_intent
from geom.identity import (
    GEOMETRY_IDENTITY_SCHEMA_VERSION,
    HASH_DOMAIN as GEOMETRY_IDENTITY_HASH_DOMAIN,
    deserialize_geometry_identity,
    GeometryIdentityError,
)
from mesh.artifacts import (
    MESH_ARTIFACT_SCHEMA_VERSION,
    MESH_MEDIA_TYPE,
    MeshArtifactError,
    artifact_sha256,
    canonical_quality_bytes,
    canonical_topology_bytes,
    load_quality_artifact,
    load_topology_artifact,
    validate_mesh_artifact_pair,
)


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


class GeometryIdentityArtifactError(RuntimeError):
    """Stable safe failure while writing or reading a durable identity artifact."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class CadRegionReferenceError(RuntimeError):
    """Sanitized durable CAD-region validation failure.

    ``region_id`` is the region whose own evidence failed, and ``region_codes``
    carries every region-scoped failure found in the same pass. Both stay empty
    for an artifact-scoped failure — a missing, corrupt, unsupported or
    mis-bound geometry-identity artifact invalidates every region bound to it,
    so there is no single offending region to name. Keeping the distinction
    lets the projection attribute a region-specific code to the region that
    actually earned it instead of to its healthy neighbours.
    """

    def __init__(
        self,
        code: str,
        *,
        region_id: str | None = None,
        region_codes: dict[str, str] | None = None,
    ):
        self.code = code
        self.region_id = region_id
        self.region_codes = dict(region_codes or {})
        super().__init__(code)


class MeshPersistenceError(RuntimeError):
    """Typed failure suitable for later RFC 9457 translation."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class MeshOwnershipMismatchError(MeshPersistenceError):
    pass


class MeshLineageConflictError(MeshPersistenceError):
    pass


class MeshRequestConflictError(MeshPersistenceError):
    pass


class PersistenceDatabaseError(RuntimeError):
    """Sanitized retryable failure at the model-version write boundary."""


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
    geometry_identity: Mapped["GeometryIdentityArtifactRecord | None"] = relationship(
        back_populates="model_version",
        cascade="all, delete-orphan",
        uselist=False,
    )


class GeometryIdentityArtifactRecord(Base):
    """Immutable one-to-one durable ownership record for a STEP ModelVersion."""

    __tablename__ = "geometry_identity_artifacts"
    model_version_id: Mapped[str] = mapped_column(
        ForeignKey("model_versions.id", ondelete="CASCADE"), primary_key=True
    )
    model_id: Mapped[str] = mapped_column(
        ForeignKey("models.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_version: Mapped[int] = mapped_column(Integer, nullable=False)
    hash_domain: Mapped[str] = mapped_column(String(100), nullable=False)
    canonical_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    integrity_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_version: Mapped[ModelVersion] = relationship(
        back_populates="geometry_identity"
    )


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


class MeshRevision(Base):
    """Immutable mesh artifact pair bound to exact source and setup revisions."""

    __tablename__ = "mesh_revisions"
    __table_args__ = (
        UniqueConstraint("project_id", "request_id", name="uq_project_mesh_request_id"),
        UniqueConstraint(
            "predecessor_mesh_revision_id",
            name="uq_mesh_revision_predecessor_successor",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_string)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"), nullable=False, index=True)
    model_version_id: Mapped[str] = mapped_column(ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    setup_id: Mapped[str] = mapped_column(ForeignKey("simulation_setups.id", ondelete="CASCADE"), nullable=False, index=True)
    setup_revision_id: Mapped[str] = mapped_column(ForeignKey("setup_revisions.id", ondelete="CASCADE"), nullable=False, index=True)
    predecessor_mesh_revision_id: Mapped[str | None] = mapped_column(ForeignKey("mesh_revisions.id", ondelete="RESTRICT"), nullable=True)
    topology_artifact_key: Mapped[str] = mapped_column(String(160), nullable=False)
    topology_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    topology_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    topology_media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    topology_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_artifact_key: Mapped[str] = mapped_column(String(160), nullable=False)
    quality_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    quality_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_model_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mesh_settings_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mesher_profile_id: Mapped[str] = mapped_column(String(120), nullable=False)
    mesher_profile_version: Mapped[str] = mapped_column(String(80), nullable=False)
    request_id: Mapped[str] = mapped_column(String(200), nullable=False)
    canonical_request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
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


@event.listens_for(GeometryIdentityArtifactRecord, "before_update")
def _immutable_geometry_identity_artifact(*_args) -> None:
    raise ValueError("GeometryIdentityArtifact records are immutable")


@event.listens_for(MeshRevision, "before_update")
def _immutable_mesh_revision(*_args) -> None:
    raise ValueError("MeshRevision records are immutable")


def canonical_intent(intent: SimulationIntent) -> tuple[str, str]:
    enforce_cad_region_entity_ids_invariant(intent)
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

    @contextmanager
    def _database_write(self) -> Iterator[None]:
        try:
            yield
        except SQLAlchemyError as exc:
            raise PersistenceDatabaseError("database write failed") from exc

    @contextmanager
    def _database_read(self) -> Iterator[None]:
        """Sanitize a read failure exactly as the write boundary does.

        Route-layer CAD resolution reads the setup, its exact ModelVersion and
        the persisted geometry-identity artifact *before* anything is written.
        The setup routes recognize ``PersistenceDatabaseError`` alone, so a raw
        ``SQLAlchemyError`` escaping these reads would answer an unsanitized
        ``text/plain`` 500 instead of the established RFC 9457 problem.
        """

        try:
            yield
        except SQLAlchemyError as exc:
            raise PersistenceDatabaseError("database read failed") from exc

    def _cleanup_failed_publication(
        self, *, blob_key: str, final_path: Path, created_by_operation: bool
    ) -> None:
        """Remove only this operation's unreferenced CAS publication."""

        if not created_by_operation:
            return
        with self.sessions() as session:
            referenced = session.scalar(
                select(ModelVersion.id)
                .where(ModelVersion.blob_key == blob_key)
                .limit(1)
            )
        if (
            referenced is None
            and final_path.is_file()
            and not final_path.is_symlink()
        ):
            final_path.unlink()

    def _best_effort_cleanup_failed_publication(
        self, *, blob_key: str, final_path: Path, created_by_operation: bool
    ) -> None:
        try:
            self._cleanup_failed_publication(
                blob_key=blob_key,
                final_path=final_path,
                created_by_operation=created_by_operation,
            )
        except Exception:
            # Cleanup is deliberately isolated from the authoritative failure.
            # Do not log exception text because it may contain SQL or paths.
            return

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
        version_id: str | None = None,
        geometry_identity_bytes: bytes | None = None,
    ) -> tuple[Model, ModelVersion]:
        # One process-shared lock is reused by every BlobStore for this canonical
        # root. It serializes publication + commit with version allocation and
        # orphan cleanup across application/engine instances and OS processes.
        with self.blobs.coordination_lock:
            digest = self.blobs.digest(content)
            allocated_version_id = version_id or uuid4_string()
            identity_payload = self._prepare_geometry_identity(
                model_kind=model_kind,
                canonical_bytes=geometry_identity_bytes,
                model_version_id=allocated_version_id,
                source_sha256=digest,
            )
            final = self.blobs.path_for_key(self.blobs.key(digest))
            if not final.exists():
                self.cleanup_unreferenced_blobs(limit=100)
                if len(content) > self.max_source_storage_bytes - self.blobs.source_bytes():
                    raise SourceStorageLimitExceededError("source storage capacity exceeded")
            existed_before = final.exists()
            blob_key = self.blobs.key(digest)
            try:
                with self._database_write():
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
                            id=allocated_version_id,
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
                        if identity_payload is not None:
                            session.add(
                                self._geometry_identity_record(
                                    model=model,
                                    version=version_record,
                                    canonical_bytes=identity_payload,
                                )
                            )
                        self._supersede_current(session, model, version_record)
            except Exception:
                self._best_effort_cleanup_failed_publication(
                    blob_key=blob_key,
                    final_path=final,
                    created_by_operation=not existed_before,
                )
                raise
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
        version_id: str | None = None,
        geometry_identity_bytes: bytes | None = None,
    ) -> tuple[Model, ModelVersion]:
        with self.blobs.coordination_lock:
            allocated_version_id = version_id or uuid4_string()
            identity_payload = self._prepare_geometry_identity(
                model_kind=model_kind,
                canonical_bytes=geometry_identity_bytes,
                model_version_id=allocated_version_id,
                source_sha256=source_sha256,
            )
            final = self.blobs.path_for_key(self.blobs.key(source_sha256))
            if not final.exists():
                self.cleanup_unreferenced_blobs(limit=100)
            existed_before = final.exists()
            blob_key = self.blobs.key(source_sha256)
            try:
                with self._database_write():
                    blob_key = self.blobs.publish_file_with_limit(
                        source_path, source_sha256, size_bytes,
                        self.max_source_storage_bytes,
                    )
                    if self._after_blob_publish is not None:
                        self._after_blob_publish()
                    media_type = (
                        mimetypes.guess_type(source_name)[0]
                        or "application/octet-stream"
                    )
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
                            id=allocated_version_id, model_id=model.id, version=int(next_version),
                            source_sha256=source_sha256, source_name=source_name,
                            size_bytes=size_bytes, media_type=media_type,
                            model_kind=model_kind, blob_key=blob_key,
                            created_at=datetime.now(timezone.utc), is_superseded=False,
                        )
                        self._insert_model_version(session, version_record)
                        if identity_payload is not None:
                            session.add(
                                self._geometry_identity_record(
                                    model=model,
                                    version=version_record,
                                    canonical_bytes=identity_payload,
                                )
                            )
                        self._supersede_current(session, model, version_record)
            except Exception:
                self._best_effort_cleanup_failed_publication(
                    blob_key=blob_key,
                    final_path=final,
                    created_by_operation=not existed_before,
                )
                raise
            return model, version_record

    @staticmethod
    def _prepare_geometry_identity(
        *,
        model_kind: str,
        canonical_bytes: bytes | None,
        model_version_id: str,
        source_sha256: str,
    ) -> bytes | None:
        if model_kind != "step":
            if canonical_bytes is not None:
                raise GeometryIdentityArtifactError(
                    "geometry_identity_not_applicable"
                )
            return None
        if canonical_bytes is None:
            raise GeometryIdentityArtifactError("geometry_identity_missing")
        try:
            raw = bytes(canonical_bytes)
        except (TypeError, ValueError) as exc:
            raise GeometryIdentityArtifactError(
                "geometry_identity_integrity_failed"
            ) from exc
        artifact = Persistence._deserialize_geometry_identity(raw)
        if {
            "model_version_id": artifact.model_version_id,
            "source_sha256": artifact.source_sha256,
        } != {
            "model_version_id": model_version_id,
            "source_sha256": source_sha256.lower(),
        }:
            raise GeometryIdentityArtifactError(
                "geometry_identity_binding_mismatch"
            )
        return raw

    @staticmethod
    def _deserialize_geometry_identity(canonical_bytes: bytes):
        try:
            return deserialize_geometry_identity(canonical_bytes)
        except GeometryIdentityError as exc:
            code = {
                "geometry.artifact_integrity_failed": (
                    "geometry_identity_integrity_failed"
                ),
                "geometry.artifact_version_unsupported": (
                    "geometry_identity_version_unsupported"
                ),
                "geometry.artifact_binding_invalid": (
                    "geometry_identity_binding_mismatch"
                ),
                "geometry.artifact_schema_invalid": (
                    "geometry_identity_schema_invalid"
                ),
            }.get(exc.code, "geometry_identity_schema_invalid")
            raise GeometryIdentityArtifactError(code) from exc

    @staticmethod
    def _geometry_identity_record(
        *,
        model: Model,
        version: ModelVersion,
        canonical_bytes: bytes,
    ) -> GeometryIdentityArtifactRecord:
        return GeometryIdentityArtifactRecord(
            model_version_id=version.id,
            model_id=model.id,
            source_sha256=version.source_sha256,
            artifact_version=GEOMETRY_IDENTITY_SCHEMA_VERSION,
            hash_domain=GEOMETRY_IDENTITY_HASH_DOMAIN,
            canonical_bytes=canonical_bytes,
            integrity_sha256=hashlib.sha256(canonical_bytes).hexdigest(),
            created_at=version.created_at,
        )

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
        with self._database_read(), self.sessions() as session:
            return session.get(ModelVersion, version_id)

    def read_geometry_identity(
        self, version_id: str
    ) -> tuple[GeometryIdentityArtifactRecord, bytes, dict]:
        """Read and fully verify a durable artifact without regenerating it."""

        with self._database_read(), self.sessions() as session:
            version = session.get(ModelVersion, version_id)
            if version is None:
                raise PersistenceNotFoundError("model version")
            if version.model_kind != "step":
                raise GeometryIdentityArtifactError(
                    "geometry_identity_not_applicable"
                )
            record = session.get(GeometryIdentityArtifactRecord, version_id)
            if record is None:
                raise GeometryIdentityArtifactError("geometry_identity_missing")
            if (
                record.artifact_version != GEOMETRY_IDENTITY_SCHEMA_VERSION
                or record.hash_domain != GEOMETRY_IDENTITY_HASH_DOMAIN
            ):
                raise GeometryIdentityArtifactError(
                    "geometry_identity_version_unsupported"
                )
            try:
                raw = bytes(record.canonical_bytes)
            except (TypeError, ValueError) as exc:
                raise GeometryIdentityArtifactError(
                    "geometry_identity_integrity_failed"
                ) from exc
            actual_digest = hashlib.sha256(raw).hexdigest()
            if not (
                isinstance(record.integrity_sha256, str)
                and len(record.integrity_sha256) == 64
                and hmac.compare_digest(actual_digest, record.integrity_sha256)
            ):
                raise GeometryIdentityArtifactError(
                    "geometry_identity_integrity_failed"
                )
            artifact = self._deserialize_geometry_identity(raw)
            payload = artifact.to_dict()
            if (
                payload["schema_version"] != record.artifact_version
                or payload["hash_domain"] != record.hash_domain
            ):
                raise GeometryIdentityArtifactError(
                    "geometry_identity_version_unsupported"
                )
            binding = payload["model_binding"]
            if (
                record.model_version_id != version.id
                or record.model_id != version.model_id
                or record.source_sha256 != version.source_sha256
                or not isinstance(binding, dict)
                or binding.get("model_version_id") != version.id
                or binding.get("source_sha256") != version.source_sha256
            ):
                raise GeometryIdentityArtifactError(
                    "geometry_identity_binding_mismatch"
                )
            return record, raw, payload

    def create_setup(
        self, *, project_id: str, model_id: str, model_version_id: str,
        intent: SimulationIntent, request_id: str,
    ) -> tuple[SimulationSetup, SetupRevision]:
        self._enforce_cad_entity_ids(intent)
        try:
            return self._create_setup_once(
                project_id=project_id,
                model_id=model_id,
                model_version_id=model_version_id,
                intent=intent,
                request_id=request_id,
            )
        except IntegrityError as exc:
            canonical, intent_digest = canonical_intent(intent)
            del canonical
            expected_digest = canonical_fingerprint({
                "model_id": model_id,
                "model_version_id": model_version_id,
                "intent_sha256": intent_digest,
            })
            with self.sessions() as session:
                replay = session.scalar(select(SimulationSetup).where(
                    SimulationSetup.project_id == project_id,
                    SimulationSetup.create_request_id == request_id,
                ))
                if replay is not None:
                    if replay.create_request_sha256 != expected_digest:
                        raise SetupRequestConflictError(
                            "request ID reused with different setup"
                        ) from exc
                    revision = session.scalar(select(SetupRevision).where(
                        SetupRevision.setup_id == replay.id,
                        SetupRevision.revision == 1,
                    ))
                    if revision is not None:
                        return replay, revision
            raise PersistenceDatabaseError("setup database write failed") from exc
        except SQLAlchemyError as exc:
            raise PersistenceDatabaseError("setup database write failed") from exc

    def _create_setup_once(
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
            self._validate_cad_region_references(
                session=session, version=version, intent=intent
            )
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
        with self._database_read(), self.sessions() as session:
            return session.get(SimulationSetup, setup_id)

    def get_revision(self, setup_id: str, revision: int) -> SetupRevision | None:
        with self.sessions() as session:
            return session.scalar(select(SetupRevision).where(
                SetupRevision.setup_id == setup_id,
                SetupRevision.revision == revision,
            ))

    def get_setup_revision_by_id(self, revision_id: str) -> SetupRevision | None:
        with self.sessions() as session:
            return session.get(SetupRevision, revision_id)

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
        self._enforce_cad_entity_ids(intent)
        canonical, digest = canonical_intent(intent)
        del canonical
        relevant_payload = mutation_payload or {"intent_sha256": digest}
        expected_digest = canonical_fingerprint({
            "expected_revision": expected_revision,
            "mutation_type": mutation_type,
            "payload": relevant_payload,
        })
        try:
            return self._mutate_setup_once(
                setup_id=setup_id,
                expected_revision=expected_revision,
                request_id=request_id,
                mutation_type=mutation_type,
                intent=intent,
                mutation_payload=mutation_payload,
            )
        except IntegrityError as exc:
            with self.sessions() as session:
                replay = session.scalar(select(SetupRevision).where(
                    SetupRevision.setup_id == setup_id,
                    SetupRevision.request_id == request_id,
                ))
                if replay is not None:
                    if replay.mutation_sha256 == expected_digest:
                        return replay
                    raise SetupRequestConflictError(
                        "request ID reused with different mutation"
                    ) from exc
                setup = session.get(SimulationSetup, setup_id)
                if setup is None:
                    raise PersistenceNotFoundError("setup") from exc
                if setup.current_revision != expected_revision:
                    raise SetupRevisionConflictError(
                        "stale setup revision"
                    ) from exc
            raise PersistenceDatabaseError("setup database write failed") from exc
        except SQLAlchemyError as exc:
            raise PersistenceDatabaseError("setup database write failed") from exc

    def _mutate_setup_once(
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
            version = session.get(ModelVersion, setup.model_version_id)
            if version is None or version.model_id != setup.model_id:
                raise PersistenceConflictError("invalid setup lineage")
            self._validate_cad_region_references(
                session=session, version=version, intent=intent
            )
            parent = session.scalar(select(SetupRevision).where(
                SetupRevision.setup_id == setup_id,
                SetupRevision.revision == expected_revision,
            ))
            revision = self._new_revision(
                setup, parent, intent, mutation_type, request_id,
                canonical=canonical, digest=digest, mutation_digest=mutation_digest,
            )
            session.add(revision)
            session.flush()
            advanced = session.execute(
                update(SimulationSetup)
                .where(
                    SimulationSetup.id == setup_id,
                    SimulationSetup.current_revision == expected_revision,
                )
                .values(
                    current_revision=revision.revision,
                    updated_at=revision.created_at,
                )
                .execution_options(synchronize_session=False)
            )
            if advanced.rowcount != 1:
                raise SetupRevisionConflictError("stale setup revision")
            return revision

    def validate_setup_region_references(
        self, setup_id: str, intent: SimulationIntent, *,
        allow_legacy: bool = False,
    ) -> None:
        """Revalidate against the exact historical artifact without writes."""

        with self._database_read(), self.sessions() as session:
            setup = session.get(SimulationSetup, setup_id)
            if setup is None:
                raise PersistenceNotFoundError("setup")
            version = session.get(ModelVersion, setup.model_version_id)
            if version is None or version.model_id != setup.model_id:
                raise PersistenceConflictError("invalid setup lineage")
            self._validate_cad_region_references(
                session=session, version=version, intent=intent,
                allow_legacy=allow_legacy,
            )

    def _validate_cad_region_references(
        self, *, session: Session, version: ModelVersion,
        intent: SimulationIntent, allow_legacy: bool = False,
    ) -> None:
        self._enforce_cad_entity_ids(intent)
        cad_regions = [
            region for region in intent.regions if region.entity_type == "cad_face"
        ]
        if not cad_regions:
            return
        if version.model_kind != "step":
            raise CadRegionReferenceError("cad_region_not_applicable")
        record = session.get(GeometryIdentityArtifactRecord, version.id)
        if record is None:
            raise CadRegionReferenceError("cad_region_artifact_missing")
        if (
            record.artifact_version != GEOMETRY_IDENTITY_SCHEMA_VERSION
            or record.hash_domain != GEOMETRY_IDENTITY_HASH_DOMAIN
        ):
            raise CadRegionReferenceError(
                "cad_region_artifact_version_unsupported"
            )
        try:
            raw = bytes(record.canonical_bytes)
        except (TypeError, ValueError) as exc:
            raise CadRegionReferenceError(
                "cad_region_artifact_integrity_failed"
            ) from exc
        digest = hashlib.sha256(raw).hexdigest()
        if not (
            isinstance(record.integrity_sha256, str)
            and hmac.compare_digest(digest, record.integrity_sha256)
        ):
            raise CadRegionReferenceError(
                "cad_region_artifact_integrity_failed"
            )
        try:
            artifact = self._deserialize_geometry_identity(raw)
        except GeometryIdentityArtifactError as exc:
            raise CadRegionReferenceError(
                "cad_region_artifact_invalid"
            ) from exc
        if (
            artifact.model_version_id != version.id
            or artifact.source_sha256 != version.source_sha256
            or record.model_version_id != version.id
            or record.model_id != version.model_id
            or record.source_sha256 != version.source_sha256
        ):
            raise CadRegionReferenceError("cad_region_artifact_binding_mismatch")
        faces_by_tag = {
            face.source_ref: face
            for face in artifact.faces
            if isinstance(face.source_ref, int)
            and not isinstance(face.source_ref, bool)
        }
        stable_ids = {
            face.stable_identity
            for face in artifact.faces
            if face.stable_identity is not None
        }
        collision_ids = {
            str(group["collision_group_id"])
            for group in artifact.collision_groups
        }
        def check_region(region: Region) -> None:
            """Validate one region against the already-verified artifact.

            Every failure raised here is region-scoped: it describes this
            region's own evidence and says nothing about its neighbours.
            """

            target = region.cad_face_target
            if target is None:
                raise CadRegionReferenceError("cad_region_stable_target_required")
            evidence = list(target.source_face_tags)
            if (
                getattr(target, "model_version_id", None) is not None
                and getattr(target, "model_version_id") != version.id
            ):
                raise CadRegionReferenceError("cad_region_model_version_mismatch")
            if target.resolution in {
                "legacy_local_only",
                "invalid_legacy_evidence",
            }:
                if not allow_legacy:
                    raise CadRegionReferenceError(
                        "cad_region_legacy_client_forbidden"
                    )
                if region.status == "confirmed":
                    raise CadRegionReferenceError("cad_region_unresolved")
                return
            if target.resolution == "unresolved":
                if region.status == "confirmed":
                    raise CadRegionReferenceError("cad_region_unresolved")
                return
            if not hmac.compare_digest(
                target.artifact_sha256 or "", record.integrity_sha256
            ):
                raise CadRegionReferenceError("cad_region_artifact_mismatch")
            evidence_faces = []
            for tag in evidence:
                face = faces_by_tag.get(tag)
                if face is None:
                    raise CadRegionReferenceError("cad_region_evidence_unknown")
                evidence_faces.append(face)
            if target.resolution == "resolved":
                if not set(target.stable_identities).issubset(stable_ids):
                    raise CadRegionReferenceError("cad_region_identity_unknown")
                evidence_ids = {
                    face.stable_identity
                    for face in evidence_faces
                    if face.stable_identity is not None
                }
                if (
                    any(face.ambiguous for face in evidence_faces)
                    or evidence_ids != set(target.stable_identities)
                ):
                    raise CadRegionReferenceError(
                        "cad_region_identity_evidence_inconsistent"
                    )
            else:
                if not set(target.collision_group_ids).issubset(collision_ids):
                    raise CadRegionReferenceError(
                        "cad_region_collision_group_unknown"
                    )
                evidence_groups = {
                    face.collision_group_id
                    for face in evidence_faces
                    if face.collision_group_id is not None
                }
                if (
                    any(not face.ambiguous for face in evidence_faces)
                    or evidence_groups != set(target.collision_group_ids)
                ):
                    raise CadRegionReferenceError(
                        "cad_region_collision_evidence_inconsistent"
                    )

        # Every region is checked so a healthy neighbour is known to be
        # healthy rather than merely unreached. The raised code and its order
        # are unchanged — the first failing region still decides the problem
        # response — but the full per-region map travels with it.
        region_codes: dict[str, str] = {}
        for region in cad_regions:
            try:
                check_region(region)
            except CadRegionReferenceError as exc:
                region_codes.setdefault(region.id, exc.code)
        if region_codes:
            first = next(
                region.id for region in cad_regions if region.id in region_codes
            )
            raise CadRegionReferenceError(
                region_codes[first],
                region_id=first,
                region_codes=region_codes,
            )

    @staticmethod
    def _enforce_cad_entity_ids(intent: SimulationIntent) -> None:
        try:
            enforce_cad_region_entity_ids_invariant(intent)
        except EngineeringConsistencyError as exc:
            raise CadRegionReferenceError(
                CAD_ENTITY_IDS_FORBIDDEN_CODE
            ) from exc

    def resolve_cad_regions_for_version(
        self, intent: SimulationIntent, model_version_id: str
    ) -> SimulationIntent:
        """Resolve local CAD evidence against one exact persisted artifact.

        This is a proposal-time resolver, not a migration or rebinding path.
        It reads the artifact owned by the caller-supplied historical
        ModelVersion and never consults a model's latest version.
        """

        self._enforce_cad_entity_ids(intent)
        record, _raw, payload = self.read_geometry_identity(model_version_id)
        by_tag = {
            face["source_ref"]: face
            for face in payload["faces"]
            if isinstance(face["source_ref"], int)
            and not isinstance(face["source_ref"], bool)
        }
        regions = []
        for region in intent.regions:
            if region.entity_type != "cad_face":
                regions.append(region.model_copy(deep=True))
                continue
            existing_target = region.cad_face_target
            if existing_target is None:
                raise CadRegionReferenceError(
                    "cad_region_stable_target_required"
                )
            if existing_target.resolution != "unresolved":
                # Never overwrite a pre-existing stable, ambiguous, or legacy
                # claim. Persistence validation remains authoritative for an
                # already-resolved exact target.
                regions.append(region.model_copy(deep=True))
                continue
            if (
                existing_target.model_version_id is not None
                and existing_target.model_version_id != model_version_id
            ):
                raise CadRegionReferenceError(
                    "cad_region_model_version_mismatch"
                )
            tags = [
                value
                for value in region_entity_membership(region)
                if isinstance(value, int) and not isinstance(value, bool)
            ]
            faces = [by_tag.get(tag) for tag in tags]
            stable = {
                face["stable_identity"]
                for face in faces
                if face is not None and face["stable_identity"] is not None
            }
            collisions = {
                face["collision_group_id"]
                for face in faces
                if face is not None and face["collision_group_id"] is not None
            }
            if tags and len(faces) == len(tags) and all(
                face is not None and not face["ambiguous"] for face in faces
            ):
                target = ResolvedCadFaceTarget(
                    model_version_id=model_version_id,
                    artifact_sha256=record.integrity_sha256,
                    resolution="resolved",
                    stable_identities=sorted(stable),
                    source_face_tags=tags,
                )
            elif tags and len(faces) == len(tags) and all(
                face is not None and face["ambiguous"] for face in faces
            ):
                target = AmbiguousCadFaceTarget(
                    model_version_id=model_version_id,
                    artifact_sha256=record.integrity_sha256,
                    resolution="ambiguous",
                    collision_group_ids=sorted(collisions),
                    source_face_tags=tags,
                )
            else:
                target = UnresolvedCadFaceTarget(
                    model_version_id=model_version_id,
                    resolution="unresolved",
                    source_face_tags=tags,
                )
            regions.append(
                region.model_copy(
                    update={"cad_face_target": target, "entity_ids": None},
                    deep=True,
                )
            )
        resolved = intent.model_copy(update={"regions": regions}, deep=True)
        enforce_cad_region_entity_ids_invariant(resolved)
        return resolved

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

    @staticmethod
    def _raise_mesh_artifact_error(error: MeshArtifactError) -> None:
        if error.code == "mesh_ownership_mismatch":
            raise MeshOwnershipMismatchError(error.code) from error
        raise MeshPersistenceError(error.code) from error

    def _mesh_request_replay(
        self, *, project_id: str, request_id: str, request_hash: str
    ) -> MeshRevision | None:
        with self.sessions() as session:
            replay = session.scalar(
                select(MeshRevision).where(
                    MeshRevision.project_id == project_id,
                    MeshRevision.request_id == request_id,
                )
            )
        if replay is None:
            return None
        if replay.canonical_request_hash != request_hash:
            raise MeshRequestConflictError("request_id_conflict")
        return replay

    def get_mesh_revision_by_request(
        self, *, project_id: str, request_id: str
    ) -> MeshRevision | None:
        """Read an existing service request without weakening R5.1 replay."""

        with self.sessions() as session:
            return session.scalar(select(MeshRevision).where(
                MeshRevision.project_id == project_id,
                MeshRevision.request_id == request_id,
            ))

    def _resolve_mesh_integrity_failure(
        self,
        error: IntegrityError,
        *,
        project_id: str,
        request_id: str,
        request_hash: str,
    ) -> MeshRevision:
        """Translate only recognized mesh insertion constraints after rollback."""

        message = str(error.orig)
        request_unique = (
            "UNIQUE constraint failed: "
            "mesh_revisions.project_id, mesh_revisions.request_id"
        )
        primary_key_unique = "UNIQUE constraint failed: mesh_revisions.id"
        if request_unique in message or primary_key_unique in message:
            replay = self._mesh_request_replay(
                project_id=project_id,
                request_id=request_id,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
            if primary_key_unique in message:
                raise MeshLineageConflictError(
                    "mesh_revision_id_conflict"
                ) from error
        if (
            "UNIQUE constraint failed: "
            "mesh_revisions.predecessor_mesh_revision_id"
        ) in message:
            raise MeshLineageConflictError("mesh_lineage_conflict") from error
        if "mesh source is stale" in message:
            raise SetupSourceSupersededError("mesh source is superseded") from error
        if "invalid mesh revision ownership" in message:
            raise MeshOwnershipMismatchError("mesh_ownership_mismatch") from error
        if "invalid mesh revision lineage" in message:
            raise MeshLineageConflictError("mesh_lineage_conflict") from error
        raise PersistenceDatabaseError("database write failed") from error

    def _cleanup_failed_mesh_publication(
        self, *, blob_key: str, created_by_operation: bool
    ) -> None:
        """Remove one exact newly-created mesh blob only when still unreferenced.

        Re-acquiring the re-entrant process-shared CAS lock makes the final
        reference check and unlink indivisible with every mesh publication and
        commit path, even if this private helper is called outside creation.
        """

        if not created_by_operation:
            return
        with self.blobs.coordination_lock:
            final_path = self.blobs.path_for_key(blob_key)
            with self.sessions() as session:
                referenced = session.scalar(
                    select(ModelVersion.id)
                    .where(ModelVersion.blob_key == blob_key)
                    .limit(1)
                )
                if referenced is None:
                    referenced = session.scalar(
                        select(MeshRevision.id)
                        .where(
                            (MeshRevision.topology_artifact_key == blob_key)
                            | (MeshRevision.quality_artifact_key == blob_key)
                        )
                        .limit(1)
                    )
            if (
                referenced is None
                and final_path.is_file()
                and not final_path.is_symlink()
            ):
                final_path.unlink()

    def _best_effort_cleanup_failed_mesh_publications(
        self, publications: list[tuple[str, bool, bool]]
    ) -> None:
        for blob_key, _existed_before, created_by_operation in reversed(
            publications
        ):
            try:
                self._cleanup_failed_mesh_publication(
                    blob_key=blob_key,
                    created_by_operation=created_by_operation,
                )
            except Exception:
                # Cleanup must never replace the authoritative creation failure.
                continue

    def create_mesh_revision(
        self, *, project_id: str, model_id: str, model_version_id: str,
        setup_id: str, setup_revision_id: str,
        predecessor_mesh_revision_id: str | None, request_id: str,
        topology: dict, quality: dict,
    ) -> MeshRevision:
        """Validate and atomically persist an exact immutable artifact pair."""

        mesh_revision_id = topology.get("mesh_revision_id")
        if not isinstance(mesh_revision_id, str):
            raise MeshPersistenceError("malformed_mesh_artifact")
        try:
            if str(uuid.UUID(mesh_revision_id)) != mesh_revision_id:
                raise ValueError
        except (ValueError, AttributeError) as exc:
            raise MeshPersistenceError("malformed_mesh_artifact") from exc
        try:
            topology_bytes = canonical_topology_bytes(topology)
            topology_document = load_topology_artifact(topology_bytes)
            quality_bytes = canonical_quality_bytes(quality)
            quality_document = load_quality_artifact(quality_bytes)
            topology_digest = artifact_sha256(topology_bytes)
            quality_digest = artifact_sha256(quality_bytes)
            binding = {
                "mesh_revision_id": mesh_revision_id,
                "project_id": project_id,
                "model_id": model_id,
                "model_version_id": model_version_id,
                "setup_id": setup_id,
                "setup_revision_id": setup_revision_id,
            }
            validate_mesh_artifact_pair(
                topology_document,
                quality_document,
                topology_sha256=topology_digest,
                expected_binding=binding,
            )
        except MeshArtifactError as exc:
            self._raise_mesh_artifact_error(exc)
            raise AssertionError("unreachable")

        request_hash = canonical_fingerprint({
            **binding,
            "predecessor_mesh_revision_id": predecessor_mesh_revision_id,
            "topology_sha256": topology_digest,
            "quality_sha256": quality_digest,
            "source_model_sha256": topology_document.source_model_sha256,
            "mesh_settings_hash": topology_document.mesh_settings_hash,
            "mesher_profile_id": topology_document.mesher_profile_id,
            "mesher_profile_version": topology_document.mesher_profile_version,
        })
        publications: list[tuple[str, bool, bool]] = []
        # Global ordering: process-shared CAS -> process-local setup -> SQLite.
        # The CAS lock spans publication, ownership/currentness validation,
        # commit, and operation-scoped failure cleanup.  Consequently another
        # process cannot commit either digest between cleanup's final reference
        # check and unlink.
        with self.blobs.coordination_lock, self._setup_lock:
            replay = self._mesh_request_replay(
                project_id=project_id,
                request_id=request_id,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
            try:
                (
                    topology_key,
                    topology_existed_before,
                    topology_created,
                ) = self.blobs.publish_with_status(
                    topology_bytes, topology_digest
                )
                publications.append(
                    (
                        topology_key,
                        topology_existed_before,
                        topology_created,
                    )
                )
                (
                    quality_key,
                    quality_existed_before,
                    quality_created,
                ) = self.blobs.publish_with_status(
                    quality_bytes, quality_digest
                )
                publications.append(
                    (
                        quality_key,
                        quality_existed_before,
                        quality_created,
                    )
                )
                try:
                    with self.transaction() as session:
                        project = session.get(Project, project_id)
                        model = session.get(Model, model_id)
                        version = session.get(ModelVersion, model_version_id)
                        setup = session.get(SimulationSetup, setup_id)
                        setup_revision = session.get(
                            SetupRevision, setup_revision_id
                        )
                        if project is None:
                            raise PersistenceNotFoundError("project")
                        if model is None:
                            raise PersistenceNotFoundError("model")
                        if version is None:
                            raise PersistenceNotFoundError("model version")
                        if setup is None:
                            raise PersistenceNotFoundError("setup")
                        if setup_revision is None:
                            raise PersistenceNotFoundError("setup revision")
                        if not (
                            model.project_id == project_id
                            and version.model_id == model_id
                            and setup.project_id == project_id
                            and setup.model_id == model_id
                            and setup.model_version_id == model_version_id
                            and setup_revision.setup_id == setup_id
                        ):
                            raise MeshOwnershipMismatchError(
                                "mesh_ownership_mismatch"
                            )
                        if setup.current_revision != setup_revision.revision:
                            raise SetupRevisionConflictError(
                                "stale setup revision"
                            )
                        if (
                            model.current_version_id != model_version_id
                            or version.is_superseded
                            or setup.is_stale
                        ):
                            raise SetupSourceSupersededError(
                                "mesh source is superseded"
                            )
                        if (
                            version.source_sha256
                            != topology_document.source_model_sha256
                        ):
                            raise MeshPersistenceError("source_hash_mismatch")
                        # The outer process-shared CAS lock serializes this
                        # root/leaf check with every publication, including
                        # across backend processes. No migration is required.
                        existing_mesh_identity = session.get(
                            MeshRevision, mesh_revision_id
                        )
                        lineage = list(session.scalars(
                            select(MeshRevision).where(
                                MeshRevision.project_id == project_id,
                                MeshRevision.model_id == model_id,
                                MeshRevision.model_version_id
                                == model_version_id,
                                MeshRevision.source_model_sha256
                                == version.source_sha256,
                            )
                        ))
                        if existing_mesh_identity is not None:
                            pass
                        elif not lineage:
                            if predecessor_mesh_revision_id is not None:
                                raise MeshLineageConflictError(
                                    "mesh_lineage_conflict"
                                )
                        else:
                            lineage_ids = {item.id for item in lineage}
                            roots = [
                                item for item in lineage
                                if item.predecessor_mesh_revision_id is None
                            ]
                            successor_counts: dict[str, int] = {}
                            successor_by_parent: dict[str, str] = {}
                            for item in lineage:
                                parent_id = item.predecessor_mesh_revision_id
                                if parent_id is None:
                                    continue
                                if parent_id not in lineage_ids:
                                    raise MeshLineageConflictError(
                                        "mesh_lineage_conflict"
                                    )
                                successor_counts[parent_id] = (
                                    successor_counts.get(parent_id, 0) + 1
                                )
                                successor_by_parent[parent_id] = item.id
                            leaves = [
                                item for item in lineage
                                if item.id not in successor_counts
                            ]
                            if (
                                len(roots) != 1
                                or len(leaves) != 1
                                or any(
                                    count != 1
                                    for count in successor_counts.values()
                                )
                            ):
                                raise MeshLineageConflictError(
                                    "mesh_lineage_conflict"
                                )
                            visited: set[str] = set()
                            cursor: str | None = roots[0].id
                            while cursor is not None:
                                if cursor in visited:
                                    raise MeshLineageConflictError(
                                        "mesh_lineage_conflict"
                                    )
                                visited.add(cursor)
                                cursor = successor_by_parent.get(cursor)
                            if visited != lineage_ids:
                                raise MeshLineageConflictError(
                                    "mesh_lineage_conflict"
                                )

                            if predecessor_mesh_revision_id != leaves[0].id:
                                raise MeshLineageConflictError(
                                    "mesh_lineage_conflict"
                                )
                        record = MeshRevision(
                            id=mesh_revision_id,
                            project_id=project_id,
                            model_id=model_id,
                            model_version_id=model_version_id,
                            setup_id=setup_id,
                            setup_revision_id=setup_revision_id,
                            predecessor_mesh_revision_id=(
                                predecessor_mesh_revision_id
                            ),
                            topology_artifact_key=topology_key,
                            topology_sha256=topology_digest,
                            topology_size_bytes=len(topology_bytes),
                            topology_media_type=MESH_MEDIA_TYPE,
                            topology_schema_version=(
                                MESH_ARTIFACT_SCHEMA_VERSION
                            ),
                            quality_artifact_key=quality_key,
                            quality_sha256=quality_digest,
                            quality_size_bytes=len(quality_bytes),
                            quality_media_type=MESH_MEDIA_TYPE,
                            quality_schema_version=MESH_ARTIFACT_SCHEMA_VERSION,
                            source_model_sha256=(
                                topology_document.source_model_sha256
                            ),
                            mesh_settings_hash=(
                                topology_document.mesh_settings_hash
                            ),
                            mesher_profile_id=(
                                topology_document.mesher_profile_id
                            ),
                            mesher_profile_version=(
                                topology_document.mesher_profile_version
                            ),
                            request_id=request_id,
                            canonical_request_hash=request_hash,
                            created_at=datetime.now(timezone.utc),
                        )
                        session.add(record)
                        session.flush()
                except IntegrityError as exc:
                    return self._resolve_mesh_integrity_failure(
                        exc,
                        project_id=project_id,
                        request_id=request_id,
                        request_hash=request_hash,
                    )
                except SQLAlchemyError as exc:
                    raise PersistenceDatabaseError(
                        "database write failed"
                    ) from exc
                return record
            except Exception:
                self._best_effort_cleanup_failed_mesh_publications(publications)
                raise

    def read_mesh_revision(
        self, mesh_revision_id: str, *, project_id: str, model_id: str,
        model_version_id: str, setup_id: str, setup_revision_id: str,
    ) -> tuple[MeshRevision, bytes, bytes]:
        """Fail-closed exact read; never resolves a current or latest parent."""

        binding = {
            "mesh_revision_id": mesh_revision_id,
            "project_id": project_id,
            "model_id": model_id,
            "model_version_id": model_version_id,
            "setup_id": setup_id,
            "setup_revision_id": setup_revision_id,
        }
        with self.sessions() as session:
            record = session.get(MeshRevision, mesh_revision_id)
            if record is None:
                raise PersistenceNotFoundError("mesh revision")
            record_binding = {
                "mesh_revision_id": record.id,
                "project_id": record.project_id,
                "model_id": record.model_id,
                "model_version_id": record.model_version_id,
                "setup_id": record.setup_id,
                "setup_revision_id": record.setup_revision_id,
            }
            if record_binding != binding:
                raise MeshOwnershipMismatchError("mesh_ownership_mismatch")
            if (
                record.topology_media_type != MESH_MEDIA_TYPE
                or record.quality_media_type != MESH_MEDIA_TYPE
                or record.topology_schema_version
                != MESH_ARTIFACT_SCHEMA_VERSION
                or record.quality_schema_version
                != MESH_ARTIFACT_SCHEMA_VERSION
            ):
                raise MeshPersistenceError("unsupported_artifact_version")
            project = session.get(Project, project_id)
            model = session.get(Model, model_id)
            version = session.get(ModelVersion, model_version_id)
            setup = session.get(SimulationSetup, setup_id)
            setup_revision = session.get(SetupRevision, setup_revision_id)
            if (
                project is None
                or model is None
                or version is None
                or setup is None
                or setup_revision is None
                or not (
                    model.project_id == project_id
                    and version.model_id == model_id
                    and setup.project_id == project_id
                    and setup.model_id == model_id
                    and setup.model_version_id == model_version_id
                    and setup_revision.setup_id == setup_id
                    and version.source_sha256 == record.source_model_sha256
                )
            ):
                raise MeshOwnershipMismatchError("mesh_ownership_mismatch")
            if record.predecessor_mesh_revision_id is not None:
                predecessor = session.get(
                    MeshRevision, record.predecessor_mesh_revision_id
                )
                if predecessor is None or not (
                    predecessor.project_id == project_id
                    and predecessor.model_id == model_id
                    and predecessor.model_version_id == model_version_id
                    and predecessor.source_model_sha256
                    == record.source_model_sha256
                ):
                    raise MeshLineageConflictError("mesh_lineage_conflict")
        try:
            topology_bytes = self.blobs.read(
                record.topology_artifact_key,
                record.topology_sha256,
                record.topology_size_bytes,
            )
            quality_bytes = self.blobs.read(
                record.quality_artifact_key,
                record.quality_sha256,
                record.quality_size_bytes,
            )
            topology_document = load_topology_artifact(topology_bytes)
            quality_document = load_quality_artifact(quality_bytes)
            validate_mesh_artifact_pair(
                topology_document,
                quality_document,
                topology_sha256=record.topology_sha256,
                expected_binding=binding,
                expected_source_model_sha256=record.source_model_sha256,
                expected_mesh_settings_hash=record.mesh_settings_hash,
                expected_mesher_profile_id=record.mesher_profile_id,
                expected_mesher_profile_version=(
                    record.mesher_profile_version
                ),
            )
        except (BlobIntegrityError, OSError) as exc:
            raise MeshPersistenceError(
                "mesh_artifact_integrity_failure"
            ) from exc
        except MeshArtifactError as exc:
            self._raise_mesh_artifact_error(exc)
            raise AssertionError("unreachable")
        return record, topology_bytes, quality_bytes

    def read_version_bytes(self, version: ModelVersion) -> bytes:
        return self.blobs.read(version.blob_key, version.source_sha256, version.size_bytes)

    def cleanup_unreferenced_blobs(self, *, limit: int = 100) -> int:
        """Bound cleanup for final blobs left when publication precedes rollback."""
        if limit <= 0:
            return 0
        with self.blobs.coordination_lock:
            with self.sessions() as session:
                referenced = set(session.scalars(select(ModelVersion.blob_key)))
                referenced.update(session.scalars(select(MeshRevision.topology_artifact_key)))
                referenced.update(session.scalars(select(MeshRevision.quality_artifact_key)))
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
                    if now_referenced is None:
                        now_referenced = session.scalar(
                            select(MeshRevision.id).where(
                                (MeshRevision.topology_artifact_key == key)
                                | (MeshRevision.quality_artifact_key == key)
                            ).limit(1)
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
