"""The event hash chain.

Each event carries the hash of its own payload and a ``chain_hash`` binding it
to its predecessor. Editing any historical event — its payload, its actor, its
timestamp, its subject — changes that event's chain hash, which no longer
matches the ``prev_hash`` recorded by the next event, and verification fails at
a named sequence number.

This makes the ledger *tamper-evident*, which is a weaker and more honest
property than tamper-proof: someone with write access to the database can
still alter it, but they cannot do so without ``nullius ledger verify``
noticing. Combined with append-only triggers, that is a reasonable standard
for a single-tenant research instrument.

Deleting a trailing run of events is the one edit a pure chain cannot detect,
so verification also checks that sequence numbers are contiguous.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from nullius.util.canonical import sha256_of

__all__ = [
    "ChainVerification",
    "EventRow",
    "chain_hash",
    "payload_hash",
    "verify_chain",
]

GENESIS: str | None = None
"""``prev_hash`` of the first event. A chain with no predecessor."""


class EventRow(Protocol):
    """The subset of :class:`nullius.db.tables.Event` verification needs."""

    seq: int
    event_id: uuid.UUID
    occurred_at: dt.datetime
    program_id: uuid.UUID | None
    actor_role: Any
    actor_task_id: uuid.UUID | None
    event_type: str
    subject_type: str
    subject_id: uuid.UUID
    payload: dict[str, Any]
    payload_hash: str
    prev_hash: str | None
    chain_hash: str
    policy_id: uuid.UUID | None


def payload_hash(payload: dict[str, Any]) -> str:
    """Hash of an event's payload in canonical form."""
    return sha256_of(payload)


def chain_hash(
    *,
    prev: str | None,
    event_id: uuid.UUID,
    occurred_at: dt.datetime,
    program_id: uuid.UUID | None,
    actor_role: str,
    actor_task_id: uuid.UUID | None,
    event_type: str,
    subject_type: str,
    subject_id: uuid.UUID,
    payload_digest: str,
    policy_id: uuid.UUID | None,
) -> str:
    """Hash binding one event to its predecessor.

    Every field an auditor would care about is covered. Notably ``occurred_at``
    and ``actor_role``: back-dating an event, or reattributing it to another
    role, must both break the chain.
    """
    return sha256_of(
        {
            "prev": prev,
            "event_id": event_id,
            "occurred_at": occurred_at,
            "program_id": program_id,
            "actor_role": actor_role,
            "actor_task_id": actor_task_id,
            "event_type": event_type,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "payload_hash": payload_digest,
            "policy_id": policy_id,
        }
    )


def chain_hash_for(row: EventRow, prev: str | None) -> str:
    """Recompute ``row``'s chain hash from the row's own stored fields."""
    return chain_hash(
        prev=prev,
        event_id=row.event_id,
        occurred_at=row.occurred_at,
        program_id=row.program_id,
        actor_role=str(row.actor_role),
        actor_task_id=row.actor_task_id,
        event_type=row.event_type,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        payload_digest=row.payload_hash,
        policy_id=row.policy_id,
    )


@dataclass(frozen=True, slots=True)
class ChainVerification:
    """Outcome of verifying a ledger."""

    ok: bool
    events_checked: int
    first_bad_seq: int | None = None
    reason: str | None = None

    def __str__(self) -> str:
        if self.ok:
            return f"ledger intact: {self.events_checked} events verified"
        return f"ledger BROKEN at seq={self.first_bad_seq}: {self.reason}"


def verify_chain(rows: Iterable[EventRow]) -> ChainVerification:
    """Verify payload hashes, chain linkage and sequence contiguity.

    ``rows`` must be ordered by ``seq`` ascending.
    """
    ordered: Sequence[EventRow] = list(rows)
    prev_hash: str | None = GENESIS
    expected_seq: int | None = None
    checked = 0

    for row in ordered:
        if expected_seq is not None and row.seq != expected_seq:
            return ChainVerification(
                ok=False,
                events_checked=checked,
                first_bad_seq=row.seq,
                reason=(
                    f"sequence gap: expected seq={expected_seq}, found {row.seq}. "
                    "Events have been deleted."
                ),
            )

        recomputed_payload = payload_hash(row.payload)
        if recomputed_payload != row.payload_hash:
            return ChainVerification(
                ok=False,
                events_checked=checked,
                first_bad_seq=row.seq,
                reason=(
                    "payload does not match its recorded hash "
                    f"(stored {row.payload_hash[:12]}…, recomputed {recomputed_payload[:12]}…)"
                ),
            )

        if row.prev_hash != prev_hash:
            return ChainVerification(
                ok=False,
                events_checked=checked,
                first_bad_seq=row.seq,
                reason=("broken link: prev_hash does not match the previous event's chain hash"),
            )

        recomputed_chain = chain_hash_for(row, prev_hash)
        if recomputed_chain != row.chain_hash:
            return ChainVerification(
                ok=False,
                events_checked=checked,
                first_bad_seq=row.seq,
                reason=(
                    "chain hash does not match the event's own fields; a header "
                    "field (time, actor, subject) has been altered"
                ),
            )

        prev_hash = row.chain_hash
        expected_seq = row.seq + 1
        checked += 1

    return ChainVerification(ok=True, events_checked=checked)
