"""Bounded runner hook for exact durable private-ID readiness rechecks."""
from datetime import timedelta
from time import monotonic, sleep

from ojirap_morning_schedule import ProcessingPending, ScheduleHold, resume_processing


def poll_resume(window, jobs, reservation, *, clock, load, checkpoint,
                read_youtube, schedule_existing, sleep_fn=sleep, monotonic_fn=monotonic,
                budget_seconds=150, retry_seconds=(15, 30, 60)):
    """Poll only known, exact private IDs; writes are via guarded resume only.

    No reservation or upload function is accepted. At most four attempts by
    default; only ProcessingPending is retryable. Callbacks must set bounded
    network I/O timeouts. Never call this with unresolved external intent.
    """
    delays = tuple(retry_seconds)
    if (type(budget_seconds) is not int or not 1 <= budget_seconds <= 240
        or len(delays) > 6 or any(type(d) is not int or not 1 <= d <= 60 for d in delays)):
        raise ScheduleHold("Unbounded readiness polling parameters")
    jobs = tuple(jobs)
    stop_at = monotonic_fn() + budget_seconds

    def bounded_clock():
        now = clock()
        window.require_open(now)
        if monotonic_fn() >= stop_at:
            raise ScheduleHold("Readiness budget exhausted; leave known videos private or reconcile existing intent")
        return now

    for attempt in range(len(delays) + 1):
        bounded_clock()
        try:
            return resume_processing(window, jobs, reservation, clock=bounded_clock,
                                     load=load, checkpoint=checkpoint, read_youtube=read_youtube,
                                     schedule_existing=schedule_existing)
        except ProcessingPending:
            if attempt == len(delays):
                raise
            remaining = min(stop_at - monotonic_fn(),
                            (window.cutoff - bounded_clock()).total_seconds())
            pause = delays[attempt]
            if timedelta(seconds=pause) >= timedelta(seconds=remaining):
                raise ScheduleHold("No readiness retry fits before cutoff/budget; leave private") from None
            sleep_fn(pause)
    raise AssertionError("unreachable")
