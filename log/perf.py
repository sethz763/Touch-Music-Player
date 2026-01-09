from __future__ import annotations

import os
from functools import lru_cache


def env_truthy(name: str, *, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=None)
def perf_enabled() -> bool:
    """Opt-in console perf printing.

    Enable by setting `STEPD_PERF=1` in the environment.
    """

    return env_truthy("STEPD_PERF", default=False)


def perf_print(msg: str) -> None:
    if perf_enabled():
        print(msg)
