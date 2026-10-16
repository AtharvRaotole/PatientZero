"""Generic tool registry: a tool = named, parameterised Cypher (queries/*.cypher) + Python post-processing.

Domain-agnostic. A domain adapter registers its tools here; the agent loop (deterministic or LLM) calls them by
name with JSON arguments and gets back {"result": ..., "trace": [...]}.
"""
from __future__ import annotations

import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema (object)
    fn: Callable[..., dict]
    writes: bool = False

    def openai_schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, name: str, description: str, parameters: dict | None = None, *, writes: bool = False):
        def deco(fn: Callable[..., dict]):
            self.tools[name] = Tool(name, description, parameters or {"type": "object", "properties": {}}, fn, writes)
            return fn
        return deco

    def get(self, name: str) -> Tool:
        if name not in self.tools:
            raise KeyError(f"unknown tool '{name}'")
        return self.tools[name]

    def names(self) -> list[str]:
        return list(self.tools)

    def openai_tools(self) -> list[dict]:
        """Compact schemas for the LLM: first sentence of each description only (keeps the prompt small)."""
        out = []
        for t in self.tools.values():
            sch = t.openai_schema()
            d = sch["function"]["description"]
            sch["function"]["description"] = (d.split(". ")[0].rstrip(".") + ".")[:160]
            out.append(sch)
        return out

    def call(self, name: str, args: dict | None = None, ctx: dict | None = None) -> dict:
        """Validate required args against the schema, run the tool, time it."""
        tool = self.get(name)
        args = dict(args or {})
        props = tool.parameters.get("properties", {})
        missing = [r for r in tool.parameters.get("required", []) if r not in args]
        if missing:
            raise ValueError(f"{name}: missing required argument(s) {', '.join(missing)}")
        unknown = [k for k in args if k not in props]
        if unknown:
            raise ValueError(f"{name}: unknown argument(s) {', '.join(unknown)}")
        if "ctx" in inspect.signature(tool.fn).parameters:
            args["ctx"] = ctx or {}  # request context the model does not control (session, fresh, depth)
        t0 = time.perf_counter()
        out = tool.fn(**args)
        out.setdefault("trace", [])
        out["ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return out
