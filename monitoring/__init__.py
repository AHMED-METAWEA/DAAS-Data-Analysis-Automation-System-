"""The autonomous analyst — the platform running itself on a schedule.

Everything else in this system waits to be asked.  A report is generated when
someone opens the page; a drill-down runs when someone clicks it.  That puts a
ceiling on how useful the platform can be, and the ceiling is not technical: it
is that the owner has to remember to look.

This package removes that.  A :class:`~db.monitoring_models.Schedule` says when
to look; :mod:`monitoring.runner` re-runs the analytics, compares them against
recorded history and stated targets, ranks what it finds by money at stake using
the same evidence engine the insights report uses, drills into the largest
finding with :mod:`agents.rootcause`, and pushes a briefing to wherever the
owner actually reads things.

Entry points:

* :func:`monitoring.runner.run_schedule` — execute one schedule, now.
* :func:`monitoring.scheduler.start_scheduler` — keep every active schedule
  firing (embedded in the API process, or standalone via ``monitoring.worker``).
"""

from monitoring.runner import preview_schedule, run_schedule

__all__ = ["preview_schedule", "run_schedule"]
