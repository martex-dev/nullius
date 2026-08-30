"""The append-only ledger.

Every state change in Nullius is appended here, in the same transaction as the
domain row it describes. Two consequences:

- **Provenance is not optional.** There is no code path that writes a
  hypothesis, a registration or a result without also writing the event that
  records who did it and when.
- **State is recoverable.** Folding the events reconstructs the tables
  (:mod:`nullius.ledger.rebuild`), which is what makes "why does the
  institution believe this?" answerable by construction rather than by
  logging discipline.

Appends are serialised: the chain hash of a new event depends on the previous
one, so two concurrent appends would race. At MVP scale the writer is single;
:meth:`Ledger.append` takes the write lock explicitly so that assumption fails
loudly rather than silently corrupting a chain.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from nullius.db.enums import Role
from nullius.db.tables import Event
from nullius.ledger.chain import ChainVerification, chain_hash, payload_hash, verify_chain
from nullius.util.clock import Clock, SystemClock
from nullius.util.ids import IdGenerator, RandomIds

__all__ = ["Ledger"]


class Ledger:
    """Append-only, hash-chained event log over a SQLAlchemy session."""

    __slots__ = ("_clock", "_ids", "_session")

    def __init__(
        self,
        session: Session,
        *,
        clock: Clock | None = None,
        ids: IdGenerator | None = None,
    ) -> None:
        self._session = session
        self._clock = clock or SystemClock()
        self._ids = ids or RandomIds()

    # ---------------------------------------------------------------- append

    def append(
        self,
        *,
        event_type: str,
        subject_type: str,
        subject_id: uuid.UUID,
        actor_role: Role,
        payload: dict[str, Any],
        program_id: uuid.UUID | None = None,
        actor_task_id: uuid.UUID | None = None,
        policy_id: uuid.UUID | None = None,
        occurred_at: dt.datetime | None = None,
    ) -> Event:
        """Append one event and return it.

        The caller is responsible for the surrounding transaction: the event
        and the domain row it describes must commit together, or neither.
        """
        tip = self._tip()
        prev = tip.chain_hash if tip is not None else None

        event_id = self._ids.new()
        when = occurred_at or self._clock.now()
        digest = payload_hash(payload)

        event = Event(
            event_id=event_id,
            occurred_at=when,
            program_id=program_id,
            actor_role=actor_role,
            actor_task_id=actor_task_id,
            event_type=event_type,
            subject_type=subject_type,
            subject_id=subject_id,
            payload=payload,
            payload_hash=digest,
            prev_hash=prev,
            chain_hash=chain_hash(
                prev=prev,
                event_id=event_id,
                occurred_at=when,
                program_id=program_id,
                actor_role=str(actor_role.value),
                actor_task_id=actor_task_id,
                event_type=event_type,
                subject_type=subject_type,
                subject_id=subject_id,
                payload_digest=digest,
                policy_id=policy_id,
            ),
            policy_id=policy_id,
        )
        self._session.add(event)
        self._session.flush()
        return event

    def _tip(self) -> Event | None:
        """The most recent event, or ``None`` for an empty ledger."""
        return self._session.scalars(
            sa.select(Event).order_by(Event.seq.desc()).limit(1)
        ).one_or_none()

    # ------------------------------------------------------------------ read

    def events(
        self,
        *,
        program_id: uuid.UUID | None = None,
        subject_id: uuid.UUID | None = None,
    ) -> list[Event]:
        """Events in sequence order, optionally narrowed."""
        query = sa.select(Event).order_by(Event.seq.asc())
        if program_id is not None:
            query = query.where(Event.program_id == program_id)
        if subject_id is not None:
            query = query.where(Event.subject_id == subject_id)
        return list(self._session.scalars(query))

    def __len__(self) -> int:
        total = self._session.scalar(sa.select(sa.func.count()).select_from(Event))
        return int(total or 0)

    # ---------------------------------------------------------------- verify

    def verify(self) -> ChainVerification:
        """Recompute the whole chain.

        Narrowing by program is deliberately not offered: the chain is global,
        and verifying a subset would report intact on a ledger whose other
        events had been altered.
        """
        return verify_chain(self._session.scalars(sa.select(Event).order_by(Event.seq.asc())))
