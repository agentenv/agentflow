from __future__ import annotations

from collections.abc import Mapping


def stringify_env(env: Mapping[object, object] | None) -> dict[str, str]:
    if not isinstance(env, Mapping):
        return {}
    return {
        str(key): str(value)
        for key, value in env.items()
        if value is not None
    }


def merge_env_layers(*layers: Mapping[object, object] | None) -> dict[str, str]:
    merged: dict[str, str] = {}
    for layer in layers:
        merged.update(stringify_env(layer))
    return merged
