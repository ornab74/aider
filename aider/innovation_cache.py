"""Content-addressed cache for bounded context and analysis artifacts."""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from aider.innovation_context import ContextPacket, ContextSlice


@dataclass(frozen=True)
class CacheEntry:
    key: str
    query: str
    file_digests: Mapping[str, str]
    packet: ContextPacket
    created_at: float
    hits: int = 0


@dataclass(frozen=True)
class CacheLookup:
    hit: bool
    packet: ContextPacket | None
    reason: str
    key: str


class ContextArtifactCache:
    def __init__(self, *, max_entries: int = 64) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.max_entries = max_entries
        self.entries: OrderedDict[str, CacheEntry] = OrderedDict()

    def key_for(self, query: str, files: Mapping[str, str], *, namespace: str = "context-v1") -> str:
        payload = {
            "namespace": namespace,
            "query": " ".join(query.split()),
            "files": self.file_digests(files),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def put(
        self,
        query: str,
        files: Mapping[str, str],
        packet: ContextPacket,
        *,
        namespace: str = "context-v1",
    ) -> str:
        key = self.key_for(query, files, namespace=namespace)
        self.entries[key] = CacheEntry(
            key,
            query,
            self.file_digests(files),
            packet,
            time.time(),
        )
        self.entries.move_to_end(key)
        while len(self.entries) > self.max_entries:
            self.entries.popitem(last=False)
        return key

    def get(
        self,
        query: str,
        files: Mapping[str, str],
        *,
        namespace: str = "context-v1",
    ) -> CacheLookup:
        key = self.key_for(query, files, namespace=namespace)
        entry = self.entries.get(key)
        if entry is None:
            return CacheLookup(False, None, "no exact content-addressed entry", key)
        self.entries[key] = CacheEntry(
            entry.key,
            entry.query,
            entry.file_digests,
            entry.packet,
            entry.created_at,
            entry.hits + 1,
        )
        self.entries.move_to_end(key)
        return CacheLookup(True, entry.packet, "query and file digests match", key)

    def invalidate_paths(self, paths: set[str]) -> tuple[str, ...]:
        removed = []
        for key, entry in list(self.entries.items()):
            if paths & set(entry.file_digests):
                removed.append(key)
                del self.entries[key]
        return tuple(removed)

    def save(self, path: str | Path) -> None:
        payload = [self._entry_to_dict(entry) for entry in self.entries.values()]
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path, *, max_entries: int = 64) -> "ContextArtifactCache":
        cache = cls(max_entries=max_entries)
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        for raw in payload[-max_entries:]:
            packet_raw = raw["packet"]
            packet = ContextPacket(
                query=packet_raw["query"],
                slices=tuple(
                    ContextSlice(**{**item, "reasons": tuple(item.get("reasons", ()))})
                    for item in packet_raw["slices"]
                ),
                token_estimate=packet_raw["token_estimate"],
                budget=packet_raw["budget"],
                omitted_candidates=packet_raw.get("omitted_candidates", 0),
            )
            entry = CacheEntry(
                raw["key"],
                raw["query"],
                raw["file_digests"],
                packet,
                raw["created_at"],
                raw.get("hits", 0),
            )
            cache.entries[entry.key] = entry
        return cache

    @staticmethod
    def file_digests(files: Mapping[str, str]) -> dict[str, str]:
        return {
            path: hashlib.sha256(text.encode()).hexdigest()
            for path, text in sorted(files.items())
        }

    @staticmethod
    def _entry_to_dict(entry: CacheEntry) -> dict[str, object]:
        return {
            "key": entry.key,
            "query": entry.query,
            "file_digests": dict(entry.file_digests),
            "packet": asdict(entry.packet),
            "created_at": entry.created_at,
            "hits": entry.hits,
        }
