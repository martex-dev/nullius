"""Role contracts: what an agent is, mechanically.

An agent in Nullius is not a personality with a name. It is a contract:

- a **model**, so adversarial roles can run on a different one from the roles
  they check (`docs/01-critique.md` F8);
- an **input view**, which is the *only* institutional state it sees — this is
  where information asymmetry lives, and it is a registered function rather
  than an instruction not to peek;
- an **output schema**, validated before anything is written;
- **validators**, code that checks the output beyond its shape;
- **limits** on calls and tokens.

There is no method for an agent to message another agent, because there is no
such thing here. An agent reads a view and emits an artifact
(`docs/02-architecture.md` §1).

The design document specifies the input view as a parameterised SQL view. This
implements it as a registered Python function of ``(Repository, AgentTask)``,
which preserves the property that matters — an agent sees exactly what the
named view returns, and the view is a testable object — while remaining
portable across both backends. Registering a view is the only way to widen
what a role can see.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from nullius.db.enums import Role
from nullius.errors import NulliusError
from nullius.llm.types import ModelRef

if TYPE_CHECKING:  # pragma: no cover
    from nullius.repository import Repository

__all__ = [
    "AgentResult",
    "AgentTask",
    "InputView",
    "RoleContract",
    "TaskStatus",
    "ValidationFailure",
    "Validator",
    "register_validator",
    "register_view",
    "resolve_validator",
    "resolve_view",
]


class ValidationFailure(NulliusError):
    """An agent's output failed a validator.

    Distinct from a schema error: the shape was right and the content was not.
    Both are recorded; neither is retried into success.
    """


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUSED_BUDGET = "refused_budget"
    """Not an error. Budget exhaustion is a legitimate research outcome."""


type InputView = Callable[[Repository, AgentTask], dict[str, Any]]
type Validator = Callable[[BaseModel, dict[str, Any]], None]

_VIEWS: dict[str, InputView] = {}
_VALIDATORS: dict[str, Validator] = {}


def register_view(name: str) -> Callable[[InputView], InputView]:
    """Register an input view under ``name``.

    Views are named rather than passed inline so that "what can this role see?"
    is answerable by reading a registry instead of by tracing call sites.
    """

    def decorate(view: InputView) -> InputView:
        if name in _VIEWS:
            raise ValueError(f"input view {name!r} is already registered")
        _VIEWS[name] = view
        return view

    return decorate


def register_validator(name: str) -> Callable[[Validator], Validator]:
    """Register an output validator under ``name``."""

    def decorate(validator: Validator) -> Validator:
        if name in _VALIDATORS:
            raise ValueError(f"validator {name!r} is already registered")
        _VALIDATORS[name] = validator
        return validator

    return decorate


def resolve_view(name: str) -> InputView:
    try:
        return _VIEWS[name]
    except KeyError:
        raise KeyError(
            f"no input view named {name!r}; a role cannot run without a declared view"
        ) from None


def resolve_validator(name: str) -> Validator:
    try:
        return _VALIDATORS[name]
    except KeyError:
        raise KeyError(f"no validator named {name!r}") from None


@dataclass(frozen=True, slots=True)
class RoleContract:
    """Everything that defines one role's behaviour."""

    role: Role
    version: str
    model: ModelRef
    system_prompt: str
    input_view: str
    output_schema: type[BaseModel]
    validators: tuple[str, ...] = ()
    max_calls_per_task: int = 2
    """Including the single repair attempt. Bounded so a malformed-output loop
    cannot burn a program's budget."""

    def __post_init__(self) -> None:
        if self.max_calls_per_task < 1:
            raise ValueError("a role must be allowed at least one call")
        resolve_view(self.input_view)
        for name in self.validators:
            resolve_validator(name)

    def json_schema(self) -> dict[str, Any]:
        """The output schema, as JSON Schema for the provider."""
        return self.output_schema.model_json_schema()


@dataclass(frozen=True, slots=True)
class AgentTask:
    """One unit of work for one role."""

    task_id: uuid.UUID
    program_id: uuid.UUID
    role: Role
    contract_version: str
    subject_type: str
    subject_id: uuid.UUID
    allowance_usd: Decimal
    view: dict[str, Any] = field(default_factory=dict)
    """The materialised input view — exactly what the agent was shown.

    Stored, not recomputed, so an audit can answer "what did it see?" even
    after the institution's state has moved on.
    """


@dataclass(frozen=True, slots=True)
class AgentResult:
    """The outcome of executing a task."""

    task_id: uuid.UUID
    status: TaskStatus
    contract_version: str
    payload: BaseModel | None = None
    usd: Decimal = Decimal(0)
    calls: int = 0
    cache_hits: int = 0
    failure_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is TaskStatus.SUCCEEDED
