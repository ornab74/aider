"""Expiring context leases, provenance records, and resumable capsules."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from aider.innovation_context import ContextPacket, FloatingContextState


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slice_id(path: str, start_line: int, end_line: int, text: str) -> str:
    payload = f"{path}\0{start_line}\0{end_line}\0{text}".encode()
    return hashlib.sha256(payload).hexdigest()[:24]


@dataclass
class ContextLease:
    slice_id: str
    path: str
    remaining_turns: int
    renewals: int = 0


class ContextLeaseBook:
    def __init__(self, default_turns: int = 3) -> None:
        if default_turns < 1:
            raise ValueError("default_turns must be positive")
        self.default_turns = default_turns
        self.leases: dict[str, ContextLease] = {}

    def reconcile(self, packet: ContextPacket) -> tuple[str, ...]:
        active = []
        for item in packet.slices:
            identifier = slice_id(item.path, item.start_line, item.end_line, item.text)
            active.append(identifier)
            lease = self.leases.get(identifier)
            if lease:
                lease.remaining_turns = self.default_turns
                lease.renewals += 1
            else:
                self.leases[identifier] = ContextLease(
                    identifier, item.path, self.default_turns
                )
        return tuple(active)

    def mark_used(self, identifier: str) -> None:
        lease = self.leases[identifier]
        lease.remaining_turns = self.default_turns
        lease.renewals += 1

    def advance(self) -> tuple[str, ...]:
        expired = []
        for identifier, lease in list(self.leases.items()):
            lease.remaining_turns -= 1
            if lease.remaining_turns <= 0:
                expired.append(identifier)
                del self.leases[identifier]
        return tuple(expired)

    def is_active(self, identifier: str) -> bool:
        return identifier in self.leases


@dataclass(frozen=True)
class ProvenanceRecord:
    event: str
    slice_id: str
    path: str
    start_line: int
    end_line: int
    query: str
    content_sha256: str
    reasons: tuple[str, ...]
    timestamp: str


@dataclass
class ContextProvenanceLedger:
    records: list[ProvenanceRecord] = field(default_factory=list)

    def record_packet(self, packet: ContextPacket) -> tuple[str, ...]:
        identifiers = []
        for item in packet.slices:
            identifier = slice_id(item.path, item.start_line, item.end_line, item.text)
            identifiers.append(identifier)
            self.records.append(
                ProvenanceRecord(
                    "selected",
                    identifier,
                    item.path,
                    item.start_line,
                    item.end_line,
                    packet.query,
                    hashlib.sha256(item.text.encode()).hexdigest(),
                    item.reasons,
                    _utc_now(),
                )
            )
        return tuple(identifiers)

    def record_use(self, identifier: str, *, query: str = "") -> None:
        selected = next(
            (item for item in reversed(self.records) if item.slice_id == identifier),
            None,
        )
        if selected is None:
            raise KeyError(identifier)
        self.records.append(
            ProvenanceRecord(
                "used",
                selected.slice_id,
                selected.path,
                selected.start_line,
                selected.end_line,
                query or selected.query,
                selected.content_sha256,
                selected.reasons,
                _utc_now(),
            )
        )


@dataclass(frozen=True)
class CapsuleSlice:
    slice_id: str
    path: str
    start_line: int
    end_line: int
    content_sha256: str
    text: str


@dataclass(frozen=True)
class CapsuleVerification:
    valid: bool
    stale_slice_ids: tuple[str, ...]
    missing_paths: tuple[str, ...]


@dataclass
class ContextCapsule:
    version: int
    created_at: str
    query: str
    packet_budget: int
    slices: list[CapsuleSlice]
    state: dict[str, object]
    leases: list[dict[str, object]]
    provenance: list[dict[str, object]]

    @classmethod
    def capture(
        cls,
        packet: ContextPacket,
        state: FloatingContextState,
        leases: ContextLeaseBook,
        provenance: ContextProvenanceLedger,
    ) -> "ContextCapsule":
        slices = [
            CapsuleSlice(
                slice_id(item.path, item.start_line, item.end_line, item.text),
                item.path,
                item.start_line,
                item.end_line,
                hashlib.sha256(item.text.encode()).hexdigest(),
                item.text,
            )
            for item in packet.slices
        ]
        return cls(
            1,
            _utc_now(),
            packet.query,
            packet.budget,
            slices,
            {
                "pinned_paths": sorted(state.pinned_paths),
                "touched_paths": list(state.touched_paths),
                "facts": dict(state.facts),
            },
            [asdict(item) for item in leases.leases.values()],
            [asdict(item) for item in provenance.records],
        )

    def save(self, path: str | Path) -> None:
        payload = asdict(self)
        Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "ContextCapsule":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        payload["slices"] = [CapsuleSlice(**item) for item in payload["slices"]]
        return cls(**payload)

    def verify(self, files: Mapping[str, str]) -> CapsuleVerification:
        stale = []
        missing = []
        for item in self.slices:
            text = files.get(item.path)
            if text is None:
                missing.append(item.path)
                continue
            lines = text.splitlines()
            current = "\n".join(lines[item.start_line - 1 : item.end_line])
            numbered = "\n".join(
                f"{line_no:>6} | {line}"
                for line_no, line in enumerate(
                    lines[item.start_line - 1 : item.end_line],
                    start=item.start_line,
                )
            )
            if hashlib.sha256(numbered.encode()).hexdigest() != item.content_sha256:
                if hashlib.sha256(current.encode()).hexdigest() != item.content_sha256:
                    stale.append(item.slice_id)
        return CapsuleVerification(not stale and not missing, tuple(stale), tuple(missing))
