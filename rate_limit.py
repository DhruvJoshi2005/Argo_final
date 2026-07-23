"""
Abuse / cost protection for the ARGO API.

Two independent layers guard the OpenAI-backed endpoints so a sudden flood of
requests cannot drain the (small) OpenAI credit balance:

  LAYER 1 — Rate limiting (burst throttle)
    A fixed 60-second window caps how many requests are accepted, both
    GLOBALLY (across everyone) and PER CLIENT IP. Applied as a FastAPI
    dependency, so an over-limit request is rejected with HTTP 429 before any
    work (or any OpenAI call) happens. Proxy-aware: reads X-Forwarded-For so it
    still sees real client IPs behind Render/other proxies.

  LAYER 2 — Daily OpenAI budget (the real wallet guarantee)
    A hard cap on the number of ACTUAL OpenAI calls per day. Only cache MISSES
    reach this (cache hits are free and never counted). Once the daily cap is
    reached, further calls raise LLMBudgetExceeded instead of spending money.
    This works no matter how many IPs are involved, which is what actually
    protects the budget against a distributed flood.

All limits are configurable via environment variables (see defaults below).
State is in-memory — perfect for a single-instance deployment (e.g. Render
free tier). If you ever run multiple instances, move this to Redis.
"""

import os
import time
import threading
from datetime import date

from fastapi import Request, HTTPException

# ---- Tunable limits (override in .env) ----
GLOBAL_RATE_PER_MIN = int(os.getenv("GLOBAL_RATE_PER_MIN", "40"))  # all clients combined
IP_RATE_PER_MIN     = int(os.getenv("IP_RATE_PER_MIN", "20"))      # per single client IP
LLM_DAILY_MAX       = int(os.getenv("LLM_DAILY_MAX", "300"))       # real OpenAI calls / day


# =====================================================================
# LAYER 1 — fixed-window rate limiter (global + per-IP)
# =====================================================================

_rate_lock = threading.Lock()
_window_start = time.monotonic()
_global_count = 0
_ip_counts: dict[str, int] = {}


def _client_ip(request: Request) -> str:
    """Real client IP, honouring X-Forwarded-For (first hop) when behind a proxy."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_guard(request: Request) -> None:
    """FastAPI dependency. Raises HTTP 429 if the caller is over the limit.
    Attach with: dependencies=[Depends(rate_guard)]"""
    global _window_start, _global_count, _ip_counts

    now = time.monotonic()
    with _rate_lock:
        # Reset the counters every 60 seconds (fixed window).
        if now - _window_start >= 60:
            _window_start = now
            _global_count = 0
            _ip_counts = {}

        retry = max(1, int(60 - (now - _window_start)))

        if _global_count >= GLOBAL_RATE_PER_MIN:
            raise HTTPException(
                status_code=429,
                detail=f"Server is busy (global request limit reached). Try again in ~{retry}s.",
                headers={"Retry-After": str(retry)},
            )

        ip = _client_ip(request)
        if _ip_counts.get(ip, 0) >= IP_RATE_PER_MIN:
            raise HTTPException(
                status_code=429,
                detail=f"Too many requests from your address. Slow down and try again in ~{retry}s.",
                headers={"Retry-After": str(retry)},
            )

        _global_count += 1
        _ip_counts[ip] = _ip_counts.get(ip, 0) + 1


# =====================================================================
# LAYER 2 — daily OpenAI-call budget
# =====================================================================

class LLMBudgetExceeded(Exception):
    """Raised when today's OpenAI-call budget is used up."""


_budget_lock = threading.Lock()
_budget_day = date.today()
_budget_used = 0


def consume_llm_budget() -> None:
    """Call this immediately before every real (paid) OpenAI request.
    Raises LLMBudgetExceeded once the daily cap is hit."""
    global _budget_day, _budget_used
    with _budget_lock:
        today = date.today()
        if today != _budget_day:      # new day -> reset
            _budget_day = today
            _budget_used = 0
        if _budget_used >= LLM_DAILY_MAX:
            raise LLMBudgetExceeded(
                "Daily AI-query limit reached for this demo. "
                "Cached questions still work — please try again tomorrow."
            )
        _budget_used += 1


def budget_status() -> dict:
    """Current budget usage, for the /usage endpoint and monitoring."""
    with _budget_lock:
        return {
            "date": str(_budget_day),
            "llm_calls_used": _budget_used,
            "llm_calls_limit": LLM_DAILY_MAX,
            "llm_calls_remaining": max(0, LLM_DAILY_MAX - _budget_used),
            "rate_limit_per_min_global": GLOBAL_RATE_PER_MIN,
            "rate_limit_per_min_ip": IP_RATE_PER_MIN,
        }
