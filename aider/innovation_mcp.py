"""Task-scoped MCP schema slimming for small-context models."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from aider.innovation_context import estimate_tokens, query_terms


@dataclass(frozen=True)
class SlimmedSchema:
    schema: dict[str, Any]
    selected_tools: tuple[str, ...]
    omitted_tools: tuple[str, ...]
    original_token_estimate: int
    slim_token_estimate: int

    @property
    def reduction_ratio(self) -> float:
        if self.original_token_estimate == 0:
            return 0.0
        return 1.0 - self.slim_token_estimate / self.original_token_estimate


class MCPSchemaSlimmer:
    def slim(
        self,
        schema: Mapping[str, Any],
        query: str,
        *,
        max_tools: int = 6,
        max_properties: int = 10,
    ) -> SlimmedSchema:
        copied = copy.deepcopy(dict(schema))
        tools = list(copied.get("tools", []))
        terms = query_terms(query)
        scored = sorted(
            tools,
            key=lambda tool: (-self._tool_score(tool, terms), str(tool.get("name", ""))),
        )
        selected = [
            tool for tool in scored if self._tool_score(tool, terms) > 0
        ][:max_tools]
        if not selected and scored:
            selected = scored[: min(max_tools, 1)]
        slim_tools = [self._slim_tool(tool, terms, max_properties) for tool in selected]
        selected_names = tuple(str(tool.get("name", "")) for tool in selected)
        omitted_names = tuple(
            str(tool.get("name", ""))
            for tool in tools
            if str(tool.get("name", "")) not in selected_names
        )
        copied["tools"] = slim_tools
        original_tokens = estimate_tokens(self._stable_repr(schema))
        slim_tokens = estimate_tokens(self._stable_repr(copied))
        return SlimmedSchema(
            copied, selected_names, omitted_names, original_tokens, slim_tokens
        )

    def _slim_tool(
        self, tool: Mapping[str, Any], terms: set[str], max_properties: int
    ) -> dict[str, Any]:
        output = {
            key: copy.deepcopy(value)
            for key, value in tool.items()
            if key in {"name", "description", "inputSchema", "input_schema"}
        }
        schema_key = "inputSchema" if "inputSchema" in output else "input_schema"
        input_schema = output.get(schema_key)
        if not isinstance(input_schema, dict):
            return output
        properties = input_schema.get("properties", {})
        required = tuple(input_schema.get("required", ()))
        if not isinstance(properties, dict):
            return output
        ranked = sorted(
            properties,
            key=lambda name: (
                0 if name in required else 1,
                -self._property_score(name, properties[name], terms),
                name,
            ),
        )
        keep = set(required)
        for name in ranked:
            if len(keep) >= max_properties:
                break
            if name in required or self._property_score(name, properties[name], terms) > 0:
                keep.add(name)
        if not keep:
            keep.update(ranked[:max_properties])
        compact = {
            key: copy.deepcopy(value)
            for key, value in input_schema.items()
            if key in {"type", "description", "required", "additionalProperties"}
        }
        compact["properties"] = {
            name: self._compact_property(properties[name])
            for name in ranked
            if name in keep
        }
        compact["required"] = [name for name in required if name in keep]
        output[schema_key] = compact
        return output

    @staticmethod
    def _tool_score(tool: Mapping[str, Any], terms: set[str]) -> float:
        name = str(tool.get("name", ""))
        description = str(tool.get("description", ""))
        schema = tool.get("inputSchema", tool.get("input_schema", {}))
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        property_names = " ".join(properties.keys())
        name_terms = query_terms(name)
        body = f"{description} {property_names}".lower()
        return (
            len(terms & name_terms) * 20.0
            + sum(body.count(term) for term in terms) * 3.0
        )

    @staticmethod
    def _property_score(name: str, value: Any, terms: set[str]) -> float:
        description = value.get("description", "") if isinstance(value, dict) else ""
        body = f"{name} {description}".lower()
        return (
            len(terms & query_terms(name)) * 8.0
            + sum(body.count(term) for term in terms)
        )

    @staticmethod
    def _compact_property(value: Any) -> Any:
        if not isinstance(value, dict):
            return copy.deepcopy(value)
        allowed = {
            "type",
            "description",
            "enum",
            "items",
            "properties",
            "required",
            "format",
        }
        return {key: copy.deepcopy(item) for key, item in value.items() if key in allowed}

    @staticmethod
    def _stable_repr(value: Any) -> str:
        import json

        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
