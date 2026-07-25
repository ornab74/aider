"""Semantic SED plans built from verified symbol anchors."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aider.innovation_anchors import AnchorResolution, SymbolAnchor, SymbolAnchorIndex
from aider.innovation_large_files import SearchFirstEditor, SurgicalEdit


@dataclass(frozen=True)
class SemanticEditPlan:
    anchor: SymbolAnchor
    resolution: AnchorResolution
    edit: SurgicalEdit
    sed_script: str


class SemanticSedPlanner:
    def __init__(self) -> None:
        self.anchors = SymbolAnchorIndex()
        self.editor = SearchFirstEditor()

    def plan(
        self, path: str | Path, text: str, symbol: str, replacement: str
    ) -> SemanticEditPlan:
        anchor = self.anchors.create(text, symbol, path=str(path))
        resolution = self.anchors.resolve(text, anchor)
        return self._from_resolution(anchor, resolution, text, replacement)

    def replan(
        self,
        anchor: SymbolAnchor,
        current_text: str,
        replacement: str,
        *,
        minimum_confidence: float = 0.8,
    ) -> SemanticEditPlan:
        resolution = self.anchors.resolve(current_text, anchor)
        if resolution.matched is None or resolution.confidence < minimum_confidence:
            raise RuntimeError(
                f"symbol anchor could not be resolved safely: {resolution.reason} "
                f"({resolution.confidence:.2f})"
            )
        return self._from_resolution(anchor, resolution, current_text, replacement)

    def _from_resolution(
        self,
        anchor: SymbolAnchor,
        resolution: AnchorResolution,
        text: str,
        replacement: str,
    ) -> SemanticEditPlan:
        matched = resolution.matched
        if matched is None:
            raise RuntimeError(f"symbol anchor could not be resolved: {resolution.reason}")
        edit = self.editor.make_edit(
            matched.path,
            text,
            start_line=matched.start_line,
            end_line=matched.end_line,
            replacement=replacement,
        )
        return SemanticEditPlan(
            anchor,
            resolution,
            edit,
            self.editor.build_verified_sed_script(edit),
        )
