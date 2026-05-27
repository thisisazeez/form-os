"""
Pure-Python results/analytics aggregation over real survey responses.

This module is the dependency-free aggregation layer behind the Results / Summary page
(``apps.surveys.views_htmx.survey_results``). It is deliberately a SEPARATE module from the
view so the folds are unit-testable WITHOUT the HTTP/RBAC machinery (the view stays a thin
``can()`` gate + ``render``), mirroring how ``apps.surveys.logic`` keeps the navigation engine
pure. No chart libraries — every distribution is a count/percentage the template paints with
CSS bars or an inline SVG sparkline.

What it computes (all from ``Response.data`` + ``Response.started_at/finished_at`` — NO extra
per-question DB queries; the view loads responses ONCE and folds in Python):

  * ``completion_rate``            — finished / total responses (%).
  * ``time_to_complete``           — avg + median seconds from ``started_at`` → ``finished_at``
                                     over finished responses (median resists outliers).
  * ``completion_funnel``          — per question IN PATH ORDER: how many respondents REACHED
                                     it vs ANSWERED it vs dropped. "Reached" is computed by
                                     REPLAYING each response's navigation path through the real
                                     logic engine (``apps.surveys.logic.next_step``) using that
                                     response's final answer map — so a question bypassed by a
                                     ``skip`` / ``jumpTo`` is correctly NEITHER reached nor a
                                     drop-off (it was branched past, not abandoned).
  * ``question_results``           — per-question summary for EVERY question type:
        nps           → promoter/passive/detractor breakdown + score
        rating        → average + per-step distribution
        single/multi/pictureChoice → per-choice counts + % bars
        matrix        → per-row column distribution
        ranking       → average rank (1-based) + #1-vote count per choice
        number        → avg / min / max + count
        date          → earliest / latest + count
        fileUpload    → total files + responses-with-files
        consent       → agreed / declined counts
        cta           → clicked count
        address / contactInfo → count + per-sub-field fill rate
        scheduleMeeting → bookings count
        openText / other → recent text samples + count
  * ``responses_per_day``          — dense day-count series for the activity sparkline.

The two helpers ``i18n`` / ``is_answered`` are re-exported (under their legacy
underscore-prefixed names) from ``apps.surveys.views_htmx`` because ``apps.responses`` imports
them from there; their signatures MUST NOT drift.
"""
from __future__ import annotations

import statistics
from collections import Counter
from datetime import date as _date
from typing import Any

from django.utils import timezone

from apps.surveys.logic import next_step
from apps.surveys.models import Survey

# Question types whose answers are object-shaped composites (per-sub-field fill-rate summary).
_COMPOSITE_FIELDS = {
    "contactInfo": ["firstName", "lastName", "email", "phone", "company"],
    "address": ["addressLine1", "addressLine2", "city", "state", "zip", "country"],
}


# --------------------------------------------------------------------------- #
#  Answer primitives                                                          #
# --------------------------------------------------------------------------- #
def i18n(text: Any, lang: str = "default") -> str:
    """Resolve an i18nString map ({langCode: text}) to a display string (default fallback)."""
    if isinstance(text, dict):
        return text.get(lang) or text.get("default") or next(iter(text.values()), "")
    return text or ""


def is_answered(value: Any) -> bool:
    """True if a stored answer value is non-empty (mirrors the runtime's _is_submitted rule)."""
    if value is None or value == "":
        return False
    if isinstance(value, list | tuple | dict) and len(value) == 0:
        return False
    return True


def _ints(values: list) -> list[int]:
    """Integers in ``values`` (bools excluded — ``bool`` is an ``int`` subclass in Python)."""
    return [v for v in values if isinstance(v, int) and not isinstance(v, bool)]


def _nums(values: list) -> list[float]:
    """Real numbers in ``values`` (ints + floats; bools excluded)."""
    return [v for v in values if isinstance(v, int | float) and not isinstance(v, bool)]


# --------------------------------------------------------------------------- #
#  NPS                                                                        #
# --------------------------------------------------------------------------- #
def nps_breakdown(scores: list[int]) -> dict:
    """Net Promoter Score breakdown from a list of 0..10 integers.

    promoters 9-10, passives 7-8, detractors 0-6. NPS = %promoters - %detractors (rounded).
    Returns counts + percentages + the score; ``total`` is 0 when there are no NPS answers.
    """
    total = len(scores)
    if total == 0:
        return {"total": 0, "promoters": 0, "passives": 0, "detractors": 0, "nps": None,
                "promoter_pct": 0, "passive_pct": 0, "detractor_pct": 0}
    promoters = sum(1 for s in scores if s >= 9)
    passives = sum(1 for s in scores if 7 <= s <= 8)
    detractors = sum(1 for s in scores if s <= 6)
    promoter_pct = round(promoters / total * 100)
    passive_pct = round(passives / total * 100)
    detractor_pct = round(detractors / total * 100)
    nps = round(promoters / total * 100 - detractors / total * 100)
    return {
        "total": total,
        "promoters": promoters,
        "passives": passives,
        "detractors": detractors,
        "nps": nps,
        "promoter_pct": promoter_pct,
        "passive_pct": passive_pct,
        "detractor_pct": detractor_pct,
    }


def collect_nps_scores(responses, question_ids: set[str]) -> list[int]:
    """All integer NPS answers (0..10) for the given nps question ids across ``responses``."""
    scores: list[int] = []
    for resp in responses:
        data = resp.data or {}
        for qid in question_ids:
            val = data.get(qid)
            if isinstance(val, bool):  # guard: bools are ints in Python
                continue
            if isinstance(val, int) and 0 <= val <= 10:
                scores.append(val)
    return scores


# --------------------------------------------------------------------------- #
#  Completion rate + time-to-complete                                         #
# --------------------------------------------------------------------------- #
def completion_rate(total: int, finished: int) -> int | None:
    """Completion % = finished / total responses (None when there are no responses)."""
    if not total:
        return None
    return round(finished / total * 100)


def _fmt_duration(seconds: float | None) -> str | None:
    """A compact human duration ("45s", "2m 30s", "1h 5m") from a seconds count (None → None)."""
    if seconds is None:
        return None
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s" if sec else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def time_to_complete(responses) -> dict:
    """Average + median time-to-complete over FINISHED responses (started_at → finished_at).

    Only finished responses with a non-null ``finished_at`` contribute (partials never have
    one). ``started_at`` is ``auto_now_add`` so always present. Median is reported alongside the
    mean because a single very-long abandoned-then-resumed response can skew the average; the
    KPI strip shows the median ("typical" time) and exposes the average for context.

    Returns ``{count, avg_seconds, median_seconds, avg_display, median_display}``; all-None when
    no finished response has both timestamps.
    """
    durations: list[float] = []
    for r in responses:
        if not getattr(r, "finished", False):
            continue
        started = getattr(r, "started_at", None)
        finished = getattr(r, "finished_at", None)
        if started is None or finished is None:
            continue
        delta = (finished - started).total_seconds()
        if delta >= 0:
            durations.append(delta)
    if not durations:
        return {"count": 0, "avg_seconds": None, "median_seconds": None,
                "avg_display": None, "median_display": None}
    avg = sum(durations) / len(durations)
    median = statistics.median(durations)
    return {
        "count": len(durations),
        "avg_seconds": avg,
        "median_seconds": median,
        "avg_display": _fmt_duration(avg),
        "median_display": _fmt_duration(median),
    }


# --------------------------------------------------------------------------- #
#  Completion funnel / drop-off (branch-aware via the real logic engine)      #
# --------------------------------------------------------------------------- #
def _visited_question_ids(survey_dict: dict, answers: dict) -> list[str]:
    """Replay one response's navigation path → the ordered list of question ids it REACHED.

    Walks the real logic engine (``next_step``) from the start using the response's FINAL answer
    map. A question only appears if the navigation actually advanced to it — so a ``skip`` /
    ``jumpTo`` that branches past a question correctly omits it. Bounded by the question count
    (+ a small margin) so a malformed/loopy definition can't spin forever; ``next_step`` itself
    forbids backward jumps on a validated survey, so the loop terminates at an ending/end.
    """
    visited: list[str] = []
    current: str | None = None
    seen: set[str] = set()
    cap = len(survey_dict.get("questions") or []) + 2
    for _ in range(cap):
        step = next_step(survey_dict, answers, current)
        if step.get("type") != "question":
            break
        qid = step["id"]
        if qid in seen:  # defensive: never revisit (validated surveys don't loop)
            break
        visited.append(qid)
        seen.add(qid)
        current = qid
    return visited


def _reached_set(survey_dict: dict, response) -> tuple[list[str], set[str]]:
    """``(path, reached_set)`` for ONE response.

    ``path`` is the full POTENTIAL navigation path (``next_step`` walks the whole navigable
    sequence regardless of how far the respondent actually got). ``reached_set`` is how far they
    ACTUALLY progressed:
      * finished  → the entire path (they saw every step on their route).
      * partial   → up to the question AFTER their last ANSWERED one (the step they bailed on).
                    With NO answers, only the first question (they opened it and left).
    """
    answers = response.data or {}
    path = _visited_question_ids(survey_dict, answers)
    if not path:
        return path, set()
    if getattr(response, "finished", False):
        return path, set(path)
    last = -1
    for i, qid in enumerate(path):
        if is_answered(answers.get(qid)):
            last = i
    # They reached everything up to + including the one after their last answer (bail point).
    return path, set(path[: last + 2])


def completion_funnel(survey: Survey, responses: list) -> list[dict]:
    """Per-question funnel rows (survey/definition order): reached vs answered vs drop-off.

    For each response we replay its answer map through the real logic engine (``next_step``,
    branch-aware) to get its navigation PATH, then bound that path by how far the respondent
    actually progressed (see ``_reached_set``). Per question:
      * reached  — the respondent's actual progress reached this question.
      * answered — they gave a non-empty value for it.
      * drop_off — they ABANDONED here: this question is on their path but NOT in their reached
                   set, and it is the FIRST such question (the single bail point per respondent).
    A question branched PAST (skip / jumpTo) is on nobody's path → reached=answered=drop_off=0;
    it still appears (in definition order) so the funnel mirrors the survey structure and the
    "this step gets skipped" signal is visible.

    Percentages are of everyone who STARTED (``len(responses)``). Empty when no questions.
    """
    questions = [q for q in (survey.questions or []) if isinstance(q, dict) and "id" in q]
    if not questions:
        return []
    survey_dict = {"questions": survey.questions or [], "endings": survey.endings or []}

    reached: Counter = Counter()
    answered: Counter = Counter()
    drop_off: Counter = Counter()

    started = len(responses)
    for r in responses:
        answers = r.data or {}
        path, reached_set = _reached_set(survey_dict, r)
        for qid in reached_set:
            reached[qid] += 1
            if is_answered(answers.get(qid)):
                answered[qid] += 1
        # First question on their path they did NOT reach = where they dropped (one per resp).
        for qid in path:
            if qid not in reached_set:
                drop_off[qid] += 1
                break

    rows: list[dict] = []
    for q in questions:
        qid = q["id"]
        r_count = reached.get(qid, 0)
        a_count = answered.get(qid, 0)
        rows.append({
            "id": qid,
            "type": q.get("type", ""),
            "headline": i18n(q.get("headline")) or qid,
            "reached": r_count,
            "answered": a_count,
            "drop_off": drop_off.get(qid, 0),
            "reached_pct": round(r_count / started * 100) if started else 0,
            "answered_pct": round(a_count / started * 100) if started else 0,
        })
    return rows


# --------------------------------------------------------------------------- #
#  Per-question summaries (every type)                                        #
# --------------------------------------------------------------------------- #
def _choice_rows(choices: list, answers: list, answered_count: int) -> list[dict]:
    """Per-choice count/% rows for single/multi/picture/ranking choice lists."""
    labels = {c.get("id"): i18n(c.get("label")) for c in choices if isinstance(c, dict)}
    counter: Counter = Counter()
    for a in answers:
        if isinstance(a, list):  # multiSelect / multi-pictureChoice
            for cid in a:
                counter[cid] += 1
        elif isinstance(a, dict):  # singleSelect with {choiceId, other}
            cid = a.get("choiceId")
            if cid is not None:
                counter[cid] += 1
        else:
            counter[a] += 1
    rows = []
    for c in choices:
        if not isinstance(c, dict):
            continue
        cid = c.get("id")
        count = counter.get(cid, 0)
        pct = round(count / answered_count * 100) if answered_count else 0
        rows.append({"label": labels.get(cid, cid), "count": count, "pct": pct})
    # Any answered choice ids not in the declared choices (e.g. legacy) appended last.
    for cid, count in counter.items():
        if cid not in labels:
            pct = round(count / answered_count * 100) if answered_count else 0
            rows.append({"label": str(cid), "count": count, "pct": pct})
    return rows


def _summarize_question(q: dict, answers: list, answered_count: int) -> dict:
    """Build the ``kind``-tagged summary block for ONE question from its non-empty answers."""
    qtype = q.get("type", "")
    config = q.get("config") or {}
    entry: dict[str, Any] = {}

    if qtype == "nps":
        entry["kind"] = "nps"
        entry["nps"] = nps_breakdown(_ints(answers))

    elif qtype == "rating":
        rng = config.get("range", 5)
        nums = _ints(answers)
        entry["kind"] = "rating"
        entry["average"] = round(sum(nums) / len(nums), 2) if nums else None
        entry["scale"] = config.get("scale", "star")
        counter = Counter(nums)
        entry["steps"] = [
            {"step": s, "count": counter.get(s, 0),
             "pct": round(counter.get(s, 0) / answered_count * 100) if answered_count else 0}
            for s in range(1, int(rng) + 1)
        ]

    elif qtype in ("singleSelect", "multiSelect"):
        entry["kind"] = "choice"
        entry["rows"] = _choice_rows(config.get("choices") or [], answers, answered_count)

    elif qtype == "pictureChoice":
        # options carry imageUrl; reuse choice-row counting but key the label off the option.
        options = config.get("options") or []
        labels = {o.get("id"): (i18n(o.get("label")) or "Option") for o in options
                  if isinstance(o, dict)}
        images = {o.get("id"): o.get("imageUrl") for o in options if isinstance(o, dict)}
        counter: Counter = Counter()
        for a in answers:
            for cid in (a if isinstance(a, list) else [a]):
                counter[cid] += 1
        rows = []
        for o in options:
            if not isinstance(o, dict):
                continue
            oid = o.get("id")
            count = counter.get(oid, 0)
            rows.append({
                "label": labels.get(oid, oid),
                "image_url": images.get(oid),
                "count": count,
                "pct": round(count / answered_count * 100) if answered_count else 0,
            })
        entry["kind"] = "picture"
        entry["rows"] = rows

    elif qtype == "ranking":
        # answer = [choiceId] ordered best→worst; average rank (1-based) + #1 votes per choice.
        choices = config.get("choices") or []
        labels = {c.get("id"): i18n(c.get("label")) for c in choices if isinstance(c, dict)}
        rank_sums: Counter = Counter()
        rank_n: Counter = Counter()
        firsts: Counter = Counter()
        for a in answers:
            if not isinstance(a, list):
                continue
            for pos, cid in enumerate(a):
                rank_sums[cid] += pos + 1
                rank_n[cid] += 1
                if pos == 0:
                    firsts[cid] += 1
        rows = []
        for c in choices:
            if not isinstance(c, dict):
                continue
            cid = c.get("id")
            n = rank_n.get(cid, 0)
            avg_rank = round(rank_sums[cid] / n, 2) if n else None
            rows.append({"label": labels.get(cid, cid), "avg_rank": avg_rank,
                         "first_count": firsts.get(cid, 0)})
        # Best average rank first (lower = better); unranked (None) sink to the bottom.
        rows.sort(key=lambda r: (r["avg_rank"] is None, r["avg_rank"] or 0))
        entry["kind"] = "ranking"
        entry["rows"] = rows

    elif qtype == "matrix":
        # answer = {rowId: columnId | [columnId]}. Per-row column distribution.
        rows_def = config.get("rows") or []
        cols_def = config.get("columns") or []
        col_labels = {c.get("id"): i18n(c.get("label")) for c in cols_def if isinstance(c, dict)}
        col_ids = [c.get("id") for c in cols_def if isinstance(c, dict)]
        matrix_rows = []
        for row in rows_def:
            if not isinstance(row, dict):
                continue
            rid = row.get("id")
            counter: Counter = Counter()
            row_total = 0
            for a in answers:
                if not isinstance(a, dict) or rid not in a:
                    continue
                row_total += 1
                sel = a[rid]
                for cid in (sel if isinstance(sel, list) else [sel]):
                    counter[cid] += 1
            cells = [{"label": col_labels.get(cid, cid), "count": counter.get(cid, 0),
                      "pct": round(counter.get(cid, 0) / row_total * 100) if row_total else 0}
                     for cid in col_ids]
            matrix_rows.append({"label": i18n(row.get("label")), "total": row_total,
                                "cells": cells})
        entry["kind"] = "matrix"
        entry["columns"] = [col_labels.get(cid, cid) for cid in col_ids]
        entry["rows"] = matrix_rows

    elif qtype == "number":
        nums = _nums(answers)
        entry["kind"] = "number"
        entry["count"] = len(nums)
        entry["average"] = round(sum(nums) / len(nums), 2) if nums else None
        entry["minimum"] = min(nums) if nums else None
        entry["maximum"] = max(nums) if nums else None

    elif qtype == "date":
        dates = [a for a in answers if isinstance(a, str) and a]
        dates.sort()  # ISO date strings sort lexicographically = chronologically
        entry["kind"] = "date"
        entry["count"] = len(dates)
        entry["earliest"] = dates[0] if dates else None
        entry["latest"] = dates[-1] if dates else None

    elif qtype == "fileUpload":
        total_files = 0
        with_files = 0
        for a in answers:
            files = a if isinstance(a, list) else ([a] if a else [])
            if files:
                with_files += 1
                total_files += len(files)
        entry["kind"] = "file"
        entry["total_files"] = total_files
        entry["with_files"] = with_files

    elif qtype == "consent":
        agreed = sum(1 for a in answers if a is True)
        declined = sum(1 for a in answers if a is False)
        entry["kind"] = "consent"
        entry["agreed"] = agreed
        entry["declined"] = declined
        entry["agreed_pct"] = round(agreed / answered_count * 100) if answered_count else 0

    elif qtype == "cta":
        # answer is bool (clicked) or null; non-empty answers here ARE clicks/acks.
        clicked = sum(1 for a in answers if a is True) + sum(1 for a in answers
                                                             if isinstance(a, str) and a)
        entry["kind"] = "cta"
        entry["clicked"] = clicked or answered_count  # any non-empty answer = an interaction

    elif qtype == "scheduleMeeting":
        booked = sum(1 for a in answers if isinstance(a, dict) and a.get("booked"))
        entry["kind"] = "meeting"
        entry["booked"] = booked

    elif qtype in _COMPOSITE_FIELDS:
        # contactInfo / address: count + per-sub-field fill rate.
        fields_cfg = (config.get("fields") or {})
        # Only summarize sub-fields the question actually enabled (show != False); fall back to
        # the canonical sub-field list when config is sparse.
        enabled = [f for f in _COMPOSITE_FIELDS[qtype]
                   if not isinstance(fields_cfg.get(f), dict)
                   or fields_cfg.get(f, {}).get("show", True)]
        if not enabled:
            enabled = _COMPOSITE_FIELDS[qtype]
        fill: Counter = Counter()
        for a in answers:
            if not isinstance(a, dict):
                continue
            for f in enabled:
                v = a.get(f)
                if v not in (None, "", [], {}):
                    fill[f] += 1
        entry["kind"] = "composite"
        entry["fields"] = [
            {"label": f, "count": fill.get(f, 0),
             "pct": round(fill.get(f, 0) / answered_count * 100) if answered_count else 0}
            for f in enabled
        ]

    else:
        # openText / hidden / anything else → recent text samples.
        samples = []
        for a in answers:
            if isinstance(a, str):
                samples.append(a)
            elif isinstance(a, dict):
                parts = [str(v) for v in a.values()
                         if isinstance(v, str | int | float) and str(v)]
                if parts:
                    samples.append(", ".join(parts))
            else:
                samples.append(str(a))
        entry["kind"] = "text"
        entry["samples"] = samples[:5]

    return entry


def question_results(survey: Survey, responses: list) -> list[dict]:
    """Per-question aggregated results aligned with ``survey.questions`` order.

    A single Python fold over the already-loaded responses (NO per-question DB queries).
    Questions with no answers still render (showing 0 responses) so the page mirrors the survey
    structure. Each entry is ``{id, type, headline, answered_count, kind, ...kind-specific}``.
    """
    results: list[dict] = []
    for q in (survey.questions or []):
        if not isinstance(q, dict) or "id" not in q:
            continue
        qid = q["id"]
        answers = [(r.data or {}).get(qid) for r in responses]
        answers = [a for a in answers if is_answered(a)]
        entry = {
            "id": qid,
            "type": q.get("type", ""),
            "headline": i18n(q.get("headline")) or qid,
            "answered_count": len(answers),
        }
        entry.update(_summarize_question(q, answers, len(answers)))
        results.append(entry)
    return results


# --------------------------------------------------------------------------- #
#  Responses-over-time (activity sparkline)                                   #
# --------------------------------------------------------------------------- #
def responses_per_day(response_dates, days: int = 7) -> list[dict]:
    """Dense day-count series (oldest→newest) from an iterable of response ``created_at`` values.

    The CALLER supplies the timestamps (one query, already loaded), keeping this helper
    DB-agnostic and unit-testable. Days outside the window are ignored; missing days are
    zero-filled. ``response_dates`` may be aware datetimes or plain dates.
    """
    today = timezone.now().date()
    start = today - timezone.timedelta(days=days - 1)
    series = {start + timezone.timedelta(days=i): 0 for i in range(days)}
    for created in response_dates:
        if created is None:
            continue
        if isinstance(created, _date) and not hasattr(created, "hour"):
            d = created
        elif timezone.is_aware(created):
            d = timezone.localtime(created).date()
        else:
            d = created.date()
        if d in series:
            series[d] += 1
    return [{"date": d, "count": c} for d, c in sorted(series.items())]


def sparkline_points(series: list[dict], width: int = 280, height: int = 120) -> str:
    """SVG polyline ``points`` from a day-count series (the activity chart geometry).

    x spread evenly across ``width``; y inverted so larger counts sit higher. A flat/empty
    series renders along the baseline.
    """
    n = len(series)
    if n == 0:
        return ""
    max_count = max((p["count"] for p in series), default=0)
    pad = 6
    usable_h = height - pad * 2
    step_x = width / (n - 1) if n > 1 else 0
    pts = []
    for i, p in enumerate(series):
        x = round(i * step_x, 1)
        y = round(height - pad - (p["count"] / max_count * usable_h if max_count else 0), 1)
        pts.append(f"{x},{y}")
    return " ".join(pts)
