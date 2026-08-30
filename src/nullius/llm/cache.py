"""The response cache.

Keyed by the content address of the *request*, so the same question always
returns the same answer. This single mechanism supplies three things the
project needs (ADR-0005):

- **Reproducibility.** A recorded research program replays byte-for-byte.
- **Affordable ablations.** The B0–B7 baseline ladder varies institutional
  structure while asking many of the same questions; repeats are free.
- **Offline CI.** Recorded fixtures let the real pipeline run with no API key.

Entries are plain JSON on disk so they can be committed as fixtures, read in a
diff, and inspected without the code that wrote them.

A cache hit is *not* an independent sample of the model. Anything measuring
variance across model runs must bypass the cache explicitly, and record that
it did.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from nullius.llm.types import LlmResponse

__all__ = ["ResponseCache"]

_FANOUT = 2


class ResponseCache:
    """A filesystem cache mapping request keys to recorded responses."""

    __slots__ = ("root",)

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        return self.root / key[:_FANOUT] / f"{key}.json"

    def get(self, key: str) -> LlmResponse | None:
        """Return the recorded response for ``key``, or ``None``."""
        path = self.path_for(key)
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return LlmResponse.from_dict(payload["response"], cache_hit=True)

    def put(self, key: str, request: dict[str, Any], response: LlmResponse) -> None:
        """Record a response.

        The request is stored alongside it — not needed to serve a hit, but
        without it a cache entry is an answer with no question, which is
        useless in review and impossible to audit.
        """
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"key": key, "request": request, "response": response.as_dict()},
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def __contains__(self, key: str) -> bool:
        return self.path_for(key).is_file()

    def __iter__(self) -> Iterator[str]:
        for shard in sorted(self.root.iterdir()):
            if shard.is_dir() and len(shard.name) == _FANOUT:
                for entry in sorted(shard.glob("*.json")):
                    yield entry.stem

    def __len__(self) -> int:
        return sum(1 for _ in self)
