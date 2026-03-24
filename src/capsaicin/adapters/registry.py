"""Adapter registry — resolves backend name strings to adapter classes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from capsaicin.adapters.base import BaseAdapter
    from capsaicin.config import AdapterConfig


_REGISTRY: dict[str, type[BaseAdapter]] = {}
_DEFAULTS_LOADED = False


def _ensure_defaults() -> None:
    """Lazily register built-in adapters on first access."""
    global _DEFAULTS_LOADED
    if _DEFAULTS_LOADED:
        return
    _DEFAULTS_LOADED = True
    from capsaicin.adapters.claude_code import ClaudeCodeAdapter

    _REGISTRY.setdefault("claude-code", ClaudeCodeAdapter)


def register_adapter(name: str, cls: type[BaseAdapter]) -> None:
    """Register an adapter class under *name*.

    Raises ``ValueError`` if *name* is already registered.
    """
    if name in _REGISTRY:
        raise ValueError(f"Adapter already registered: {name!r}")
    _REGISTRY[name] = cls


def resolve_adapter(backend_name: str) -> type[BaseAdapter]:
    """Return the BaseAdapter subclass registered for *backend_name*.

    Raises ``ValueError`` if no adapter is registered under that name.
    """
    _ensure_defaults()
    cls = _REGISTRY.get(backend_name)
    if cls is None:
        raise ValueError(
            f"Unknown adapter backend: {backend_name!r}. "
            f"Registered backends: {sorted(_REGISTRY)}"
        )
    return cls


def build_adapter_from_config(adapter_config: AdapterConfig) -> BaseAdapter:
    """Construct an adapter instance from an AdapterConfig."""
    cls = resolve_adapter(adapter_config.backend)
    return cls(command=adapter_config.command)
