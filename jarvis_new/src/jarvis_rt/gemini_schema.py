# ruff: noqa: SIM102, RUF012
# Portions derived from livekit-plugins-google (Copyright 2023 LiveKit, Inc.),
# licensed under the Apache License, Version 2.0.
"""Tool schemas in the simplified form the Gemini Live API accepts."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from google.genai import types

from .tools import FunctionTool, RawFunctionTool, tool_schema


class _GeminiJsonSchema:
    """
    Transforms the JSON Schema from Pydantic to be suitable for Gemini.
    based on pydantic-ai implementation
    https://github.com/pydantic/pydantic-ai/blob/085a9542a7360b7e388ce575323ce189b397d7ad/pydantic_ai_slim/pydantic_ai/models/gemini.py#L809
    """

    # Type mapping from JSON Schema to Gemini Schema
    TYPE_MAPPING: dict[str, types.Type] = {
        "string": types.Type.STRING,
        "number": types.Type.NUMBER,
        "integer": types.Type.INTEGER,
        "boolean": types.Type.BOOLEAN,
        "array": types.Type.ARRAY,
        "object": types.Type.OBJECT,
    }

    def __init__(self, schema: dict[str, Any]):
        self.schema = deepcopy(schema)
        self.defs = self.schema.pop("$defs", {})

    def simplify(self) -> dict[str, Any] | None:
        self._simplify(self.schema, refs_stack=())
        # If the schema is an OBJECT with no properties, return None.
        if self.schema.get("type") == types.Type.OBJECT and not self.schema.get(
            "properties"
        ):
            return None
        return self.schema

    def _simplify(self, schema: dict[str, Any], refs_stack: tuple[str, ...]) -> None:
        schema.pop("title", None)
        schema.pop("default", None)
        schema.pop("additionalProperties", None)
        schema.pop("$schema", None)

        if (const := schema.pop("const", None)) is not None:
            # Gemini doesn't support const, but it does support enum with a single value
            schema["enum"] = [const]

        schema.pop("discriminator", None)
        schema.pop("examples", None)

        if ref := schema.pop("$ref", None):
            key = re.sub(r"^#/\$defs/", "", ref)
            if key in refs_stack:
                raise ValueError(
                    "Recursive `$ref`s in JSON Schema are not supported by Gemini"
                )
            refs_stack += (key,)
            schema_def = self.defs[key]
            self._simplify(schema_def, refs_stack)
            schema.update(schema_def)
            return

        if "enum" in schema and "type" not in schema:
            schema["type"] = self._infer_type(schema["enum"][0])

        # Convert type value to Gemini format
        if "type" in schema and schema["type"] != "null":
            json_type = schema["type"]
            if json_type in self.TYPE_MAPPING:
                schema["type"] = self.TYPE_MAPPING[json_type]
            elif isinstance(json_type, types.Type):
                schema["type"] = json_type
            else:
                raise ValueError(f"Unsupported type in JSON Schema: {json_type}")

        # Map field names that differ between JSON Schema and Gemini
        self._map_field_names(schema)

        # Handle anyOf - map to any_of
        if any_of := schema.pop("anyOf", None):
            if any_of:
                mapped_any_of = []
                has_null = False
                non_null_schema = None

                for item_schema in any_of:
                    self._simplify(item_schema, refs_stack)
                    if item_schema == {"type": "null"}:
                        has_null = True
                    else:
                        non_null_schema = item_schema
                        mapped_any_of.append(item_schema)

                if has_null and len(any_of) == 2 and non_null_schema:
                    schema.update(non_null_schema)
                    schema["nullable"] = True
                else:
                    schema["any_of"] = mapped_any_of

        type_ = schema.get("type")

        if type_ == types.Type.OBJECT:
            self._object(schema, refs_stack)
        elif type_ == types.Type.ARRAY:
            self._array(schema, refs_stack)

    def _infer_type(self, value: Any) -> str:
        if isinstance(value, int):
            return "integer"
        elif isinstance(value, float):
            return "number"
        elif isinstance(value, str):
            return "string"
        elif isinstance(value, bool):
            return "boolean"
        else:
            raise ValueError(f"Unsupported type in Schema: {type(value)}")

    def _map_field_names(self, schema: dict[str, Any]) -> None:
        """Map JSON Schema field names to Gemini Schema field names."""
        mappings = {
            "minLength": "min_length",
            "maxLength": "max_length",
            "minItems": "min_items",
            "maxItems": "max_items",
            "minProperties": "min_properties",
            "maxProperties": "max_properties",
        }

        for json_name, gemini_name in mappings.items():
            if json_name in schema:
                schema[gemini_name] = schema.pop(json_name)

    def _object(self, schema: dict[str, Any], refs_stack: tuple[str, ...]) -> None:
        if properties := schema.get("properties"):
            for value in properties.values():
                self._simplify(value, refs_stack)

    def _array(self, schema: dict[str, Any], refs_stack: tuple[str, ...]) -> None:
        if prefix_items := schema.get("prefixItems"):
            for prefix_item in prefix_items:
                self._simplify(prefix_item, refs_stack)

        if items_schema := schema.get("items"):
            self._simplify(items_schema, refs_stack)


def function_declarations(
    tools: list[Any], *, behavior: str | None = None
) -> list[types.Tool]:
    """One ``types.Tool`` holding every function declaration (empty when no tools)."""
    decls: list[types.FunctionDeclaration] = []
    for tool in tools:
        if isinstance(tool, RawFunctionTool):
            raw = tool.info.raw_schema
            schema: dict[str, Any] = {
                "name": tool.info.name,
                "description": raw.get("description", ""),
                "parameters": _GeminiJsonSchema(raw.get("parameters", {})).simplify()
                or None,
            }
        elif isinstance(tool, FunctionTool):
            fnc = tool_schema(tool)
            schema = {
                "name": fnc["name"],
                "description": fnc["description"],
                "parameters": _GeminiJsonSchema(fnc["parameters"]).simplify() or None,
            }
        else:
            continue
        if behavior is not None:
            schema["behavior"] = behavior
        decls.append(types.FunctionDeclaration.model_validate(schema))
    return [types.Tool(function_declarations=decls)] if decls else []
