from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any

from sqlalchemy import JSON
from sqlalchemy import Boolean
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint
from sqlalchemy import create_engine
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import Session
from sqlalchemy.orm import mapped_column

from aitrpg.domain.models import new_id
from aitrpg.domain.models import utc_now


class Base(DeclarativeBase):
    pass


class DocumentRow(Base):
    __tablename__ = 'documents'
    kind: Mapped[str] = mapped_column(String, primary_key=True)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    body: Mapped[dict[str, Any]] = mapped_column(JSON)


class EventRow(Base):
    __tablename__ = 'events'
    __table_args__ = (UniqueConstraint('game_id', 'sequence'),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    game_id: Mapped[str] = mapped_column(String, index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    actor_ids: Mapped[list[str]] = mapped_column(JSON)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String)


class ReceiptRow(Base):
    __tablename__ = 'receipts'
    invitation_id: Mapped[str] = mapped_column(String, primary_key=True)
    submission_id: Mapped[str] = mapped_column(String, unique=True)
    response_hash: Mapped[str] = mapped_column(String)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)


class ConflictError(ValueError):
    pass


class Transaction:
    def __init__(self, session: Session):
        self.session = session

    def get(self, kind: str, identifier: str) -> dict[str, Any] | None:
        row = self.session.get(DocumentRow, (kind, identifier))
        return deepcopy(row.body) if row else None

    def list(self, kind: str) -> list[dict[str, Any]]:
        rows = self.session.scalars(
            select(DocumentRow).where(DocumentRow.kind == kind)
        )
        return [deepcopy(row.body) for row in rows]

    def put(
        self,
        kind: str,
        identifier: str,
        body: dict[str, Any],
        expected_version: int | None = None,
    ) -> None:
        row = self.session.get(DocumentRow, (kind, identifier))
        if expected_version is not None:
            actual = row.body.get('version') if row else None
            if actual != expected_version:
                raise ConflictError('资料已变化，请刷新后重试')
        if row:
            row.body = deepcopy(body)
        else:
            self.session.add(
                DocumentRow(kind=kind, id=identifier, body=deepcopy(body))
            )
        self.session.flush()

    def delete(self, kind: str, identifier: str) -> None:
        row = self.session.get(DocumentRow, (kind, identifier))
        if row:
            self.session.delete(row)

    def append_event(
        self,
        game_id: str,
        kind: str,
        data: dict[str, Any],
        actor_ids: list[str] | None = None,
        is_private: bool = False,
    ) -> dict[str, Any]:
        sequence = self.session.scalar(
            select(func.max(EventRow.sequence)).where(
                EventRow.game_id == game_id
            )
        )
        event = EventRow(
            id=new_id(),
            game_id=game_id,
            sequence=(sequence or 0) + 1,
            kind=kind,
            data=deepcopy(data),
            actor_ids=list(actor_ids or []),
            is_private=is_private,
            created_at=utc_now().isoformat(),
        )
        self.session.add(event)
        self.session.flush()
        return event_dict(event)

    def receipt(self, invitation_id: str) -> dict[str, Any] | None:
        row = self.session.get(ReceiptRow, invitation_id)
        if not row:
            return None
        return {
            'submission_id': row.submission_id,
            'response_hash': row.response_hash,
            'result': deepcopy(row.result),
        }

    def save_receipt(
        self,
        invitation_id: str,
        submission_id: str,
        response_hash: str,
        result: dict[str, Any],
    ) -> None:
        self.session.add(
            ReceiptRow(
                invitation_id=invitation_id,
                submission_id=submission_id,
                response_hash=response_hash,
                result=result,
            )
        )


def event_dict(row: EventRow) -> dict[str, Any]:
    return {
        'id': row.id,
        'sequence': row.sequence,
        'kind': row.kind,
        'data': deepcopy(row.data),
        'actor_ids': list(row.actor_ids),
        'is_private': row.is_private,
        'created_at': row.created_at,
    }


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path.resolve()
        self.engine = create_engine(
            f'sqlite:///{self.path.as_posix()}',
            connect_args={'check_same_thread': False, 'timeout': 30},
        )
        self._lock = RLock()
        with self.engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA journal_mode=WAL')
            connection.exec_driver_sql('PRAGMA foreign_keys=ON')
        Base.metadata.create_all(self.engine)

    @contextmanager
    def transaction(self):
        with self._lock, Session(self.engine) as session:
            with session.begin():
                yield Transaction(session)

    def get(self, kind: str, identifier: str) -> dict[str, Any] | None:
        with self.transaction() as transaction:
            return transaction.get(kind, identifier)

    def list(self, kind: str) -> list[dict[str, Any]]:
        with self.transaction() as transaction:
            return transaction.list(kind)

    def put(self, kind: str, identifier: str, body: dict[str, Any]) -> None:
        with self.transaction() as transaction:
            transaction.put(kind, identifier, body)

    def events(self, game_id: str) -> list[dict[str, Any]]:
        with self._lock, Session(self.engine) as session:
            rows = session.scalars(
                select(EventRow)
                .where(EventRow.game_id == game_id)
                .order_by(EventRow.sequence)
            )
            return [event_dict(row) for row in rows]

    def close(self) -> None:
        self.engine.dispose()
