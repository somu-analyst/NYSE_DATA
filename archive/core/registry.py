"""Tiny signal registry so the backtester can run any signal by name.

A signal is a callable `fn(df, **params) -> pd.Series` of per-row scores, where a
higher score means a stronger LONG bias (the backtester fires when score >= threshold).
"""
from __future__ import annotations

from typing import Callable, Dict

_REGISTRY: Dict[str, Callable] = {}


def register(name: str):
    def deco(fn: Callable) -> Callable:
        _REGISTRY[name] = fn
        return fn
    return deco


def get(name: str) -> Callable:
    if name not in _REGISTRY:
        raise KeyError(f"unknown signal '{name}'. Known: {', '.join(names()) or '(none)'}")
    return _REGISTRY[name]


def names() -> list[str]:
    return sorted(_REGISTRY)
