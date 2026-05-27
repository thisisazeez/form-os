"""
Response TAGS + response FILTERS + response EXPORT service layer (parity: tags/filters/export).

This is the request-free business layer for the response admin tags/filters/export surface —
the HTMX views (apps/responses/views_htmx.py) call it; it never touches HttpRequest, so the
filter parser, tag mutators, and CSV/JSON row builders are unit-testable in isolation (the
canonical thin-view / fat-service split used across the contacts + surveys surfaces).

Three concerns, kept separate:

  TAGS (models.md §2.9 — Tag + ResponseTag through-table, already in migration 0001):
    - ``tag_create_or_get`` — create-on-the-fly, case-insensitive unique (uniq_tag_name).
    - ``apply_tag`` / ``remove_tag`` — idempotent link/unlink of a tag to a response.
    - ``tags_for_response`` / ``tags_for_responses`` — N+1-free tag hydration for the table.
    - ``rename_tag`` / ``delete_tag`` / ``merge_tags`` — the tenant-global management ops.

  FILTERS (PLAN §7/§10): a clean querystring filter over the responses table —
    - tag(s) (``?tag=<uuid>`` repeatable, AND-semantics),
    - completion (``?status=completed|partial``),
    - date range (``?from=YYYY-MM-DD`` / ``?to=YYYY-MM-DD`` over ``created_at``),
    - answer-to-a-question (``?answer=<qid>:<op>:<value>`` repeatable) evaluated with native
      Postgres JSONB lookups against ``Response.data`` (the load-bearing ``gin_response_data``
      index — models.py §Response). The segments evaluator is NOT reused here: it matches
      Contact ATTRIBUTES, not response JSON, so a clean querystring filter is the right tool
      (the brief explicitly allows this fallback).

  EXPORT: ``export_rows`` yields header + per-response cells (CSV/JSON share the same row
    builder), honoring the active filters, with PII masking decided by the caller (mirrors the
    contacts exporter's ``pii_unmasked_for_environment`` rule) and CSV formula-injection
    sanitization on every emitted cell (OWASP — the contacts exporter predates this and is out
    of scope to retrofit).

Must be called with the tenant schema ACTIVE (all models here are tenant-schema).
"""
from __future__ import annotations

from datetime import datetime, time

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import Response, ResponseTag, Tag

# ---------------------------------------------------------------------------
# Answer-filter operators (response JSONB ``data`` — NOT the segments contact-attr operators).
# Kept tiny + explicit: the response admin needs equality / contains / numeric compare on a
# single question's stored answer. Each maps a (qid, value) to a Django ORM filter kwarg.
# ---------------------------------------------------------------------------
ANSWER_OPERATORS: dict[str, str] = {
    "equals": "exact",          # data__<qid> == value
    "contains": "icontains",    # case-insensitive substring (text answers)
    "gt": "gt",                 # numeric/scale answer >
    "gte": "gte",
    "lt": "lt",
    "lte": "lte",
}
# Operators whose value is compared numerically (cast the querystring value to int/float).
_NUMERIC_ANSWER_OPS = frozenset({"gt", "gte", "lt", "lte"})

# CSV formula-injection trigger characters (OWASP CSV-injection guidance). A cell that BEGINS
# with one of these is prefixed with a single quote so a spreadsheet treats it as text, never a
# formula. \t and \r are included because some spreadsheets honor leading whitespace.
_CSV_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


# ===========================================================================
# TAGS — create-on-the-fly + apply/remove (response-scoped) + management (tenant-global).
# ===========================================================================
def tag_create_or_get(name: str, *, color: str = "", created_by_user_id=None) -> Tag:
    """Create-on-the-fly: return the existing tag (case-insensitive) or create a new one.

    ``uniq_tag_name`` is a ``Lower(name)`` unique constraint, so a concurrent create can race;
    we catch the IntegrityError and re-fetch (the canonical get-or-create-under-unique idiom).
    Raises ``ValueError`` on a blank name (a tag must be addressable).
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Tag name is required.")
    existing = Tag.objects.filter(name__iexact=name).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            return Tag.objects.create(
                name=name, color=(color or "").strip(), created_by_user_id=created_by_user_id
            )
    except IntegrityError:
        # Lost the race against a concurrent create with the same lower(name) → re-fetch.
        return Tag.objects.get(name__iexact=name)


def apply_tag(response: Response, tag: Tag) -> ResponseTag:
    """Link ``tag`` to ``response`` (idempotent — a repeat is a no-op returning the existing row).

    ``uniq_response_tag`` (response, tag) guards duplicates; we get_or_create against it.
    """
    link, _created = ResponseTag.objects.get_or_create(response=response, tag=tag)
    return link


def remove_tag(response: Response, tag: Tag) -> None:
    """Unlink ``tag`` from ``response`` (idempotent — removing an absent tag is a no-op)."""
    ResponseTag.objects.filter(response=response, tag=tag).delete()


def tags_for_response(response: Response) -> list[Tag]:
    """The tags applied to one response, ordered by name (single query)."""
    return list(
        Tag.objects.filter(response_tags__response=response).order_by("name").distinct()
    )


def tags_for_responses(response_ids) -> dict:
    """``{response_id: [Tag, ...]}`` for many responses in ONE query (no per-row N+1).

    Used by the responses table to render tag chips for the visible page. Empty input → ``{}``.
    """
    if not response_ids:
        return {}
    rows = (
        ResponseTag.objects.filter(response_id__in=response_ids)
        .select_related("tag")
        .order_by("tag__name")
    )
    out: dict = {}
    for rt in rows:
        out.setdefault(rt.response_id, []).append(rt.tag)
    return out


def all_tags_with_counts() -> list[dict]:
    """Every tenant tag + its response count (the management view + the filter rail).

    ``[{tag, count}]`` ordered by name, in ONE annotated query.
    """
    rows = Tag.objects.annotate(count=Count("response_tags")).order_by("name")
    return [{"tag": t, "count": t.count} for t in rows]


def get_tag_or_none(tag_id) -> Tag | None:
    """Load a tag by id (tolerates a malformed id → None, so a bad querystring never 500s)."""
    try:
        return Tag.objects.filter(pk=tag_id).first()
    except (ValueError, TypeError):
        return None


def rename_tag(tag: Tag, new_name: str) -> Tag:
    """Rename a tag (case-insensitive uniqueness enforced by ``uniq_tag_name``).

    Raises ``ValueError`` on a blank name or a collision with a DIFFERENT existing tag (the
    caller surfaces the message). Renaming to the same name (any case) is a no-op save.
    """
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("Tag name is required.")
    clash = Tag.objects.filter(name__iexact=new_name).exclude(pk=tag.pk).first()
    if clash is not None:
        raise ValueError(f"A tag named “{clash.name}” already exists.")
    tag.name = new_name
    tag.save(update_fields=["name", "updated_at"])
    return tag


def delete_tag(tag: Tag) -> None:
    """Delete a tag; ``ResponseTag`` rows cascade (on_delete=CASCADE) — responses are untouched."""
    tag.delete()


def merge_tags(*, source: Tag, target: Tag) -> int:
    """Merge ``source`` INTO ``target``: re-point source's response links to target, then delete
    source. Returns the number of responses that GAINED the target tag (i.e. were not already
    tagged with target). A response already carrying BOTH tags keeps a single target link (the
    ``uniq_response_tag`` constraint forbids a duplicate). Idempotent on re-run (source is gone).

    Raises ``ValueError`` if source is target (nothing to merge).
    """
    if source.pk == target.pk:
        raise ValueError("Cannot merge a tag into itself.")
    with transaction.atomic():
        # Responses already tagged with target (to avoid violating uniq_response_tag on re-point).
        already = set(
            ResponseTag.objects.filter(tag=target).values_list("response_id", flat=True)
        )
        source_links = list(ResponseTag.objects.filter(tag=source))
        gained = 0
        for link in source_links:
            if link.response_id in already:
                # Response already has target → just drop the (now redundant) source link.
                link.delete()
            else:
                link.tag = target
                link.save(update_fields=["tag"])
                already.add(link.response_id)
                gained += 1
        source.delete()
    return gained


# ===========================================================================
# FILTERS — a clean querystring filter over the responses table (PLAN §7/§10).
# ===========================================================================
def _to_number(value):
    """Coerce a querystring value to int (preferred) / float, or None if not numeric.

    Integers are preferred so a comparison against a JSONB integer answer (NPS/rating) is exact
    (a ``9.0`` float would not ``__exact``-match a stored ``9`` integer in Postgres JSONB).
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


def parse_answer_filters(raw_values, questions_by_id: dict) -> tuple[list[dict], list[dict]]:
    """Parse repeatable ``answer=<qid>:<op>:<value>`` params → (display rows, ORM-leaf specs).

    Each spec is ``{"qid", "op", "value", "lookup"}`` where ``lookup`` is the JSONB field
    lookup to apply (``data__<qid>__<orm_op>``). Unknown question ids / operators / malformed
    params are skipped silently (the table never errors on a bad URL). Numeric operators cast
    the value; a non-numeric value for a numeric op is skipped.

    Returns the display rows (for the active-filter chips) separately from the ORM specs so the
    template renders human-readable chips while the queryset uses the structured leaves.
    """
    display: list[dict] = []
    specs: list[dict] = []
    for raw in raw_values:
        parts = (raw or "").split(":", 2)
        if len(parts) < 2:
            continue
        qid, op = parts[0].strip(), parts[1].strip()
        value = parts[2] if len(parts) == 3 else ""
        if qid not in questions_by_id:
            continue
        orm_op = ANSWER_OPERATORS.get(op)
        if orm_op is None:
            continue
        cast_value: object = value
        if op in _NUMERIC_ANSWER_OPS:
            cast_value = _to_number(value)
            if cast_value is None:
                continue  # numeric op needs a number — skip a non-numeric value
        elif op == "equals":
            if value == "":
                continue
            # JSONB equality is type-sensitive: an NPS/rating answer is stored as a JSON NUMBER,
            # so ``data__q__exact="9"`` (a string) would NOT match the integer 9. Coerce a numeric
            # value to int/float so equals works for numeric answers; keep the string for text.
            num = _to_number(value)
            cast_value = num if num is not None else value
        else:
            if value == "":
                continue  # contains needs a value
        specs.append(
            {
                "qid": qid,
                "op": op,
                "value": cast_value,
                "lookup": f"data__{qid}__{orm_op}",
            }
        )
        display.append(
            {
                "raw": raw,
                "qid": qid,
                "headline": questions_by_id[qid],
                "op": op,
                "value": value,
            }
        )
    return display, specs


def _parse_date(value: str, *, end_of_day: bool):
    """Parse a ``YYYY-MM-DD`` querystring date into an aware datetime (or None if blank/bad).

    ``end_of_day`` makes a ``to`` bound inclusive of the whole day (23:59:59.999999) so
    ``?from=D&to=D`` selects responses created ON day D.
    """
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
    t = time.max if end_of_day else time.min
    naive = datetime.combine(d, t)
    if timezone.is_naive(naive):
        return timezone.make_aware(naive)
    return naive


def filtered_responses(
    survey: Response,
    *,
    tag_ids=None,
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    answer_specs=None,
):
    """Build the filtered ``Response`` queryset for a survey — the SINGLE narrowing path shared
    by the table page and the export (so a filtered Export matches the visible table).

    Filters (all optional, AND-combined):
      - ``tag_ids``: iterable of Tag uuids — a response must carry EVERY listed tag (AND). A
        response is matched per-tag then intersected, so multi-tag narrows (not widens).
      - ``status``: ``"completed"`` → finished=True; ``"partial"`` → finished=False; else ignored.
      - ``date_from`` / ``date_to``: ``YYYY-MM-DD`` over ``created_at`` (to-bound inclusive).
      - ``answer_specs``: the parsed answer leaves from ``parse_answer_filters`` — applied as
        native JSONB lookups (``data__<qid>__<op>``) against the gin_response_data index.

    Returns a queryset ordered newest-first; the caller paginates / iterates.
    """
    qs = Response.objects.filter(survey=survey)

    if status == "completed":
        qs = qs.filter(finished=True)
    elif status == "partial":
        qs = qs.filter(finished=False)

    dt_from = _parse_date(date_from, end_of_day=False)
    if dt_from is not None:
        qs = qs.filter(created_at__gte=dt_from)
    dt_to = _parse_date(date_to, end_of_day=True)
    if dt_to is not None:
        qs = qs.filter(created_at__lte=dt_to)

    if tag_ids:
        # AND semantics: a response must carry every listed tag. One subquery per tag keeps the
        # intersection correct (a single __in would be OR). Tag list is small (admin-chosen).
        for tag_id in tag_ids:
            qs = qs.filter(response_tags__tag_id=tag_id)
        qs = qs.distinct()

    for spec in answer_specs or []:
        qs = qs.filter(Q(**{spec["lookup"]: spec["value"]}))

    return qs.order_by("-created_at", "id")


# ===========================================================================
# EXPORT — row builder shared by CSV + JSON, with PII masking + CSV-injection sanitization.
# ===========================================================================
def csv_safe(value) -> str:
    """Sanitize a value for CSV output against formula injection (OWASP).

    A cell whose text BEGINS with a formula trigger (``= + - @`` / tab / CR) is prefixed with a
    single quote so a spreadsheet treats it as literal text. Non-string values are stringified
    first. This is applied to EVERY emitted CSV cell (the contacts exporter predates this rule;
    retrofitting it is out of scope).
    """
    text = "" if value is None else str(value)
    if text and text[0] in _CSV_INJECTION_PREFIXES:
        return "'" + text
    return text


def _answer_to_text(survey_questions_by_id: dict, qid: str, val) -> str:
    """Flatten one stored answer to a single CSV/JSON-friendly string (presentation, not RBAC).

    Lists join with ``; ``; dicts emit their scalar values; everything else stringifies. Mirrors
    the responses table's text rendering (no chips in a flat export).
    """
    if val is None:
        return ""
    if isinstance(val, list):
        return "; ".join(str(v) for v in val)
    if isinstance(val, dict):
        cid = val.get("choiceId")
        if cid is not None and len(val) == 1:
            return str(cid)
        parts = [str(v) for v in val.values() if isinstance(v, str | int | float) and str(v)]
        return "; ".join(parts)
    return str(val)


def export_columns(survey) -> list[dict]:
    """The per-question export columns (``[{qid, headline}]``) — every question (not capped, the
    export is the full record). ``headline`` is resolved via the shared i18n helper by the view.
    """
    cols: list[dict] = []
    for q in survey.questions or []:
        if isinstance(q, dict) and "id" in q:
            cols.append({"qid": q["id"]})
    return cols
