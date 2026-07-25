"""Expiring context leases, provenance ledgers, and resumable capsules."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from aider.innovation_context import ContextPacket, ContextSlice, FloatingContextState

CAPSULE_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slice_fingerprint(item: ContextSlice) -> str:
    payload = f"{item.path}\0{item.start_line}\0{item.end_line}\0{item.text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class ContextLease:
    slice_id: str
    path: str
    start_line: int
    end_line: int
    remaining_turns: int
    pinned: bool = False
    last_query: str = ""


class ContextLeaseBook:
    def __init__(self, *, default_turns: int = 2) -> None:
        if default_turns < 1:
            raise ValueError("default_turns must be positive")
        self.default_turns = default_turns
        self.leases: dict[str, ContextLease] = {}

    def reconcile(self, packet: ContextPacket) -> tuple[str, ...]:
        ids = []
        for item in packet.slices:
            identifier = slice_fingerprint(item)
            ids.append(identifier)
            if identifier not in self.leases:
                self.leases[identifier] = ContextLease(
                    identifier,
                    item.path,
                    item.start_line,
                    item.end_line,
                    self.default_turns,
                    False,
                    packet.query,
                )
            else:
                self.leases[identifier].last_query = packet.query
        return tuple(ids)

    def advance(self, *, used_slice_ids: Iterable[str] = ()) -> tuple[str, ...]:
        used = set(used_slice_ids)
        expired = []
        for identifier, lease in list(self.leases.items()):
            if identifier in used:
                lease.remaining_turns = self.default_turns
            elif not lease.pinned:
                lease.remaining_turns -= 1
            if lease.remaining_turns <= 0 and not lease.pinned:
                expired.append(identifier)
                del self.leases[identifier]
        return tuple(expired)

    def pin(self, slice_id: str) -> None:
        self.leases[slice_id].pinned = True

    def unpin(self, slice_id: str) -> None:
        self.leases[slice_id].pinned = False

    def is_active(self, slice_id: str) -> bool:
        return slice_id in self.leases

    def snapshot(self) -> dict[str, object]:
        return {
            "default_turns": self.default_turns,
            "leases": [asdict(item) for item in self.leases.values()],
        }

    @classmethod
    def from_snapshot(cls, value: Mapping[str, object]) -> "ContextLeaseBook":
        book = cls(default_turns=int(value.get("default_turns", 2)))
        for raw in value.get("leases", []):
            lease = ContextLease(**raw)
            book.leases[lease.slice_id] = lease
        return book


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
    observed_at: str


@dataclass
class ContextProvenanceLedger:
    records: list[ProvenanceRecord] = field(default_factory=list)

    def record_packet(self, packet: ContextPacket) -> tuple[str, ...]:
        ids = []
        for item in packet.slices:
            identifier = slice_fingerprint(item)
            ids.append(identifier)
            self.records.append(
                ProvenanceRecord(
                    "selected",
                    identifier,
                    item.path,
                    item.start_line,
                    item.end_line,
                    packet.query,
                    hashlib.sha256(item.text.encode("utf-8")).hexdigest(),
                    item.reasons,
                    _utc_now(),
                )
            )
        return tuple(ids)

    def record_use(self, slice_id: str, *, query: str = "") -> None:
        selected = next(
            (item for item in reversed(self.records) if item.slice_id == slice_id),
            None,
        )
        if selected is None:
            raise KeyError(slice_id)
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

    def to_list(self) -> list[dict[str, object]]:
        return [asdict(record) for record in self.records]

    @classmethod
    def from_list(cls, values: Iterable[Mapping[str, object]]) -> "ContextProvenanceLedger":
        records = []
        for raw in values:
            converted = dict(raw)
            converted["reasons"] = tuple(converted.get("reasons", ()))
            records.append(ProvenanceRecord(**converted))
        return cls(records)


@dataclass(frozen=True)
class CapsuleVerification:
    valid_slice_ids: tuple[str, ...]
    changed_slice_ids: tuple[str, ...]
    missing_paths: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.changed_slice_ids and not self.missing_paths


@dataclass
class ContextCapsule:
    query: str
    packet: ContextPacket
    state: FloatingContextState
    leases: ContextLeaseBook
    provenance: ContextProvenanceLedger
    created_at: str = field(default_factory=_utc_now)
    version: int = CAPSULE_VERSION

    @classmethod
    def capture(
        cls,
        packet: ContextPacket,
        state: FloatingContextState,
        leases: ContextLeaseBook,
        provenance: ContextProvenanceLedger,
    ) -> "ContextCapsule":
        return cls(packet.query, packet, state, leases, provenance)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "ContextCapsule":
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if value.get("version") != CAPSULE_VERSION:
            raise ValueError(f"unsupported capsule version: {value.get('version')}")
        return cls.from_dict(value)

    def verify(self, files: Mapping[str, str]) -> CapsuleVerification:
        valid = []
        changed = []
        missing = set()
        for item in self.packet.slices:
            if item.path not in files:
                missing.add(item.path)
                continue
            reconstructed = self._numbered_window(files[item.path], item.start_line, item.end_line)
            identifier = slice_fingerprint(item)
            if reconstructed == item.text:
                valid.append(identifier)
            else:
                changed.append(identifier)
        return CapsuleVerification(tuple(valid), tuple(changed), tuple(sorted(missing)))

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "query": self.query,
            "packet": {
                "query": self.packet.query,
                "slices": [asdict(item) for item in self.packet.slices],
                "token_estimate": self.packet.token_estimate,
                "budget": self.packet.budget,
                "omitted_candidates": self.packet.omitted_candidates,
            },
            "state": {
                "pinned_paths": sorted(self.state.pinned_paths),
                "touched_paths": list(self.state.touched_paths),
                "facts": dict(self.state.facts),
            },
            "leases": self.leases.snapshot(),
            "provenance": self.provenance.to_list(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ContextCapsule":
        packet_value = value["packet"]
        slices = []
        for raw in packet_value["slices"]:
            converted = dict(raw)
            converted["reasons"] = tuple(converted.get("reasons", ()))
            slices.append(ContextSlice(**converted))
        packet = ContextPacket(
            query=packet_value["query"],
            slices=tuple(slices),
            token_estimate=packet_value["token_estimate"],
            budget=packet_value["budget"],
            omitted_candidates=packet_value.get("omitted_candidates", 0),
        )
        state_value = value["state"]
        state = FloatingContextState(
            pinned_paths=set(state_value.get("pinned_paths", [])),
            touched_paths=list(state_value.get("touched_paths", [])),
            facts=dict(state_value.get("facts", {})),
        )
        return cls(
            query=value["query"],
            packet=packet,
            state=state,
            leases=ContextLeaseBook.from_snapshot(value["leases"]),
            provenance=ContextProvenanceLedger.from_list(value.get("provenance", [])),
            created_at=value["created_at"],
            version=value["version"],
        )

    @staticmethod
    def _numbered_window(text: str, start_line: int, end_line: int) -> str:
        lines = text.splitlines()
        return "\n".join(
            f"{line_no:>6} | {line}"
            for line_no, line in enumerate(lines[start_line - 1 : end_line], start=start_line)
        )
