"""Small retry/backoff helper used across the LLM and discovery layers.

Transient provider failures (HTTP 429 rate limits, flaky transports) are
retried a bounded number of times on the SAME provider with exponential
backoff before the pipeline consults its failover chain. Non-retryable
errors always propagate immediately; when attempts run out, the last error
is re-raised untouched.
"""

from __future__ import annotations

import time
from typing import Callable


def retry_call(
    fn: Callable[[], object],
    *,
    attempts: int,
    base_delay: float,
    should_retry: Callable[[Exception], bool],
    sleep: Callable[[float], None] = time.sleep,
) -> object:
    """Call ``fn`` up to ``attempts`` times (at least once).

    Between failures, sleep ``base_delay * 2**attempt``. ``should_retry``
    decides whether an exception is transient; a non-transient error is
    re-raised immediately with its original traceback. ``sleep`` is
    injectable so tests run instantly with a zero delay.
    """
    attempts = max(1, int(attempts))
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - re-raised below
            last_exc = exc
            if not should_retry(exc) or attempt + 1 >= attempts:
                raise
            sleep(base_delay * (2**attempt))
    raise last_exc  # pragma: no cover - attempts >= 1 always