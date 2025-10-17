"""Per-project runtime scope exports."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "burn",
    "cards",
    "component_planner",
    "config",
    "discriminator",
    "doctor",
    "events",
    "generator",
    "generator_ui",
    "hermetic",
    "hud",
    "init",
    "logs",
    "loop",
    "monitoring",
    "playbook",
    "self_update",
    "status",
    "utils",
]


def __getattr__(name: str):
    if name in __all__:
        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(name)
