import time
import logging
import threading
import os
from functools import wraps
from typing import Callable, Any, Optional
from tenacity import Retrying, stop_after_attempt, wait_exponential, wait_random

logger = logging.getLogger(__name__)


class CircuitBreakerOpenException(Exception):
    """Exception raised when a circuit breaker is in OPEN state and fails fast."""
    pass


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 5, recovery_timeout: float = 60.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.failure_count = 0
        self.last_state_change = time.time()
        self._lock = threading.RLock()

    def check_state(self):
        with self._lock:
            now = time.time()
            if self.state == "OPEN":
                if now - self.last_state_change > self.recovery_timeout:
                    self.state = "HALF_OPEN"
                    self.last_state_change = now
                    logger.info(f"CircuitBreaker '{self.name}' transitioned to HALF_OPEN. Allowing trial call.")
                else:
                    logger.warning(f"CircuitBreaker '{self.name}' is OPEN. Failing fast.")
                    raise CircuitBreakerOpenException(f"Circuit breaker '{self.name}' is OPEN")

    def record_success(self):
        with self._lock:
            if self.state != "CLOSED":
                logger.info(f"CircuitBreaker '{self.name}' transitioned from {self.state} to CLOSED.")
            self.state = "CLOSED"
            self.failure_count = 0
            self.last_state_change = time.time()

    def record_failure(self):
        with self._lock:
            self.failure_count += 1
            logger.warning(f"CircuitBreaker '{self.name}' failure recorded ({self.failure_count}/{self.failure_threshold}).")
            if self.failure_count >= self.failure_threshold:
                self.state = "OPEN"
                self.last_state_change = time.time()
                logger.error(f"CircuitBreaker '{self.name}' transitioned to OPEN. Timeout: {self.recovery_timeout}s.")


# ── Core Circuit Breaker Instances ────────────────────────────────────────────

# Database breakers — unchanged, provider-agnostic
neo4j_breaker    = CircuitBreaker("Neo4j",      failure_threshold=3, recovery_timeout=15.0)
postgres_breaker = CircuitBreaker("PostgreSQL",  failure_threshold=3, recovery_timeout=15.0)
qdrant_breaker   = CircuitBreaker("Qdrant",      failure_threshold=3, recovery_timeout=15.0)

# Cloud LLM breakers — tuned for cloud API failure patterns
# Higher threshold (5) and longer recovery (60s) vs. local Ollama (3/15s).
# HTTP 429 (rate limit) does NOT increment failure count — handled separately.
cloud_llm_breaker     = CircuitBreaker("CloudLLM",         failure_threshold=5, recovery_timeout=60.0)
failover_llm_breaker  = CircuitBreaker("CloudLLM-Failover", failure_threshold=5, recovery_timeout=60.0)


def _is_rate_limit_error(exc: Exception) -> bool:
    """Detect HTTP 429 from httpx, requests, or openai SDK exceptions."""
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return status == 429


def _get_retry_after(exc: Exception) -> float:
    """Extract Retry-After header value in seconds, default 5.0."""
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", {})
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        if retry_after:
            try:
                return float(retry_after)
            except (ValueError, TypeError):
                pass
    return 5.0


def resilient_call(breaker: CircuitBreaker, func: Callable, *args, **kwargs) -> Any:
    """Execute a function with circuit breaker protection and cloud-aware retry logic.

    HTTP 429 (rate limit): sleeps for Retry-After + jitter, does NOT trip breaker.
    HTTP 5xx (server errors): increments breaker failure count, uses exponential backoff.
    """
    breaker.check_state()

    # In eval context, delegate retry ownership to the outer harness
    in_eval = os.environ.get("LOCAL_RAG_EVAL_MODE") == "1"
    max_attempts = 1 if in_eval else 3

    retrier = Retrying(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=1, max=8) + wait_random(0, 0.5),
        reraise=True,
    )

    try:
        def _call():
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                if _is_rate_limit_error(exc):
                    # HTTP 429 — rate limit: sleep and retry WITHOUT tripping breaker
                    retry_after = _get_retry_after(exc)
                    logger.warning(
                        f"CircuitBreaker '{breaker.name}': HTTP 429 received. "
                        f"Sleeping {retry_after:.1f}s (Retry-After) before retry."
                    )
                    time.sleep(retry_after)
                    raise  # Let tenacity retry
                raise  # Non-429: propagate to tenacity, will hit except below

        result = retrier(_call)
        breaker.record_success()
        return result
    except Exception as e:
        if not _is_rate_limit_error(e):
            # Only increment breaker on hard failures (5xx, connection errors)
            breaker.record_failure()
        raise e


def resilient_call_with_fallback(breaker: CircuitBreaker, fallback_value: Any, func: Callable, *args, **kwargs) -> Any:
    """Execute a resilient call, returning fallback_value if the circuit is open or execution fails."""
    try:
        return resilient_call(breaker, func, *args, **kwargs)
    except Exception as e:
        logger.error(f"Resilient call to '{breaker.name}' failed. Utilizing fallback. Error: {e}")
        return fallback_value
