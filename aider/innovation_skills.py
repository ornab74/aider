"""Discover, rank and compress SKILL.md instructions for the active task."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from aider.innovation_context import query_terms

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    body: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillMatch:
    skill: Skill
    score: float
    summary: str


class SkillRegistry:
    def __init__(self, roots: Iterable[str | Path]) -> None:
        self.roots = tuple(Path(root) for root in roots)
        self.skills: list[Skill] = []

    def discover(self) -> list[Skill]:
        found: list[Skill] = []
        seen: set[Path] = set()
        for root in self.roots:
            if not root.exists():
                continue
            patterns = ("SKILL.md", "*.skill.md")
            for pattern in patterns:
                for path in root.rglob(pattern):
                    resolved = path.resolve()
                    if resolved in seen:
                        continue
                    seen.add(resolved)
                    found.append(self._load(path))
        self.skills = sorted(found, key=lambda skill: (skill.name.lower(), str(skill.path)))
        return list(self.skills)

    def find(self, query: str, *, limit: int = 5, summary_chars: int = 1800) -> list[SkillMatch]:
        if not self.skills:
            self.discover()
        terms = query_terms(query)
        matches: list[SkillMatch] = []
        for skill in self.skills:
            score = self._score(skill, terms, query)
            if score <= 0:
                continue
            matches.append(
                SkillMatch(
                    skill=skill,
                    score=score,
                    summary=self.summarize(skill, query, max_chars=summary_chars),
                )
            )
        matches.sort(key=lambda match: (-match.score, match.skill.name.lower()))
        return matches[:limit]

    def resolve_modifier(self, text: str) -> tuple[str | None, str]:
        """Resolve `@skill:name` or `[skill:name]` and return cleaned task text."""

        pattern = re.compile(r"(?:@skill:|\[skill:)([\w.-]+)\]?", re.IGNORECASE)
        match = pattern.search(text)
        if not match:
            return None, text
        return match.group(1), (text[: match.start()] + text[match.end() :]).strip()

    def summarize(self, skill: Skill, query: str, *, max_chars: int = 1800) -> str:
        terms = query_terms(query)
        sections = self._sections(skill.body)
        ranked: list[tuple[float, str]] = []
        for heading, content in sections:
            haystack = f"{heading}\n{content}".lower()
            score = sum(haystack.count(term) for term in terms)
            if heading.lower() in {"workflow", "safety", "commands", "usage", "rules"}:
                score += 1.5
            ranked.append((score, f"## {heading}\n{content.strip()}"))
        ranked.sort(key=lambda item: -item[0])
        chosen: list[str] = []
        length = 0
        for score, section in ranked:
            if score <= 0 and chosen:
                continue
            if length + len(section) > max_chars and chosen:
                continue
            chosen.append(section)
            length += len(section)
            if length >= max_chars:
                break
        if not chosen:
            chosen = [skill.body[:max_chars].strip()]
        return f"# {skill.name}\n{skill.description}\n\n" + "\n\n".join(chosen)

    @staticmethod
    def _load(path: Path) -> Skill:
        text = path.read_text(encoding="utf-8", errors="replace")
        metadata: dict[str, str] = {}
        body = text
        match = _FRONTMATTER_RE.match(text)
        if match:
            for line in match.group(1).splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                metadata[key.strip().lower()] = value.strip().strip('"\'')
            body = text[match.end() :]
        name = metadata.get("name") or path.parent.name or path.stem
        description = metadata.get("description", "")
        tags = tuple(
            item.strip()
            for item in metadata.get("tags", "").strip("[]").split(",")
            if item.strip()
        )
        return Skill(name=name, description=description, path=path, body=body, tags=tags)

    @staticmethod
    def _score(skill: Skill, terms: set[str], raw_query: str) -> float:
        name = skill.name.lower()
        description = skill.description.lower()
        body = skill.body.lower()
        score = 0.0
        for term in terms:
            if term == name:
                score += 25.0
            if term in name:
                score += 12.0
            if term in skill.tags:
                score += 10.0
            score += min(8.0, description.count(term) * 3.0)
            score += min(6.0, body.count(term) * 0.35)
        if raw_query.lower().strip() in name:
            score += 15.0
        return score

    @staticmethod
    def _sections(body: str) -> list[tuple[str, str]]:
        headings = list(_HEADING_RE.finditer(body))
        if not headings:
            return [("Instructions", body)]
        sections: list[tuple[str, str]] = []
        for index, match in enumerate(headings):
            start = match.end()
            end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
            sections.append((match.group(2).strip(), body[start:end].strip()))
        return sections
