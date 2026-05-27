"""
Segment filter mini-language — definition, validation, and in-process evaluation.

THE FILTER LANGUAGE (frozen shape: survey-schema.json#/$defs/segmentFilter)
==========================================================================
A ``Segment.filter`` is a recursive boolean tree of attribute conditions. A node is EITHER:

  LEAF   {"attributeKey": <str>, "operator": <op>, "value"?: <scalar|array|null>}
         Compares a contact's attribute (resolved by ``ContactAttributeKey.key``) against
         ``value`` using ``operator``. ``value`` is omitted for the presence operators
         (isSubmitted/isSkipped/isEmpty/isNotEmpty).

  BRANCH {"connector": "and"|"or", "conditions": [<node>, ...]}   (conditions: minItems 1)
         Combines child nodes. ``and`` = all match; ``or`` = any match.

An empty filter (``{}``) matches EVERY contact in the environment (the "all contacts"
segment) — this mirrors Formbricks and makes a freshly-created segment a useful default.

OPERATOR VALIDITY BY data_type (authored HERE; the contract gives a *question*-type table
for survey logic — survey-schema.json#/$defs/operator — but segments key off the
``ContactAttributeKey.data_type`` ∈ {string, number, boolean, date}). This table is the
single source of truth for which operators a leaf may use against a key of a given type;
``validate_filter`` rejects an operator that is not valid for its key's type (→ 400 with a
``schema_errors`` message, matching openapi ``ValidationErrorBody``).

  string : equals, notEquals, contains, doesNotContain, startsWith, endsWith, isOneOf,
           isSubmitted, isSkipped, isEmpty, isNotEmpty
  number : equals, notEquals, greaterThan, greaterThanOrEqual, lessThan, lessThanOrEqual,
           isOneOf, isSubmitted, isSkipped
  boolean: equals, notEquals, isAccepted, isNotAccepted, isSubmitted, isSkipped
  date   : equals, notEquals, before, after, isSubmitted, isSkipped

SEMANTICS (deterministic; mirrors the survey-logic evaluator's drift-guards)
---------------------------------------------------------------------------
- "Submitted" / present  = the contact HAS a stored value for the key (any non-null text).
  "Skipped"   / absent   = the contact has NO ``ContactAttribute`` row for the key.
- ``isSubmitted`` ⇔ present;  ``isSkipped`` ⇔ absent. They take no ``value``.
- ``isEmpty``     ⇔ absent OR stored value is the empty string;
  ``isNotEmpty``  ⇔ present AND stored value is a non-empty string. (string only)
- For every COMPARISON operator (equals, contains, greaterThan, before, …), an ABSENT
  attribute deterministically does NOT match (the left operand is missing). ``notEquals``
  on an absent attribute is therefore ``False`` too (no value to be unequal to) — presence
  is a precondition for any value comparison. (Use ``isSkipped`` to match absence.)
- Values are stored as TEXT (``ContactAttribute.value``) and CAST per the key's data_type at
  evaluation time (number→Decimal, boolean→bool, date→date.fromisoformat). A stored value
  that cannot be cast to its declared type does NOT match any comparison (treated as a
  corrupt/legacy value) — it never raises mid-evaluation.
- ``isOneOf`` with an empty array matches nothing. ``isOneOf`` compares the cast left value
  against each cast array element.

EVALUATION STRATEGY (v1)
------------------------
``evaluate_segment`` resolves matches IN-PROCESS: it fetches the environment's contacts with
``prefetch_related("attributes__attribute_key")`` (bounded queries — one for contacts, one
for their attributes) and walks the tree per contact in Python. This is simple and correct
for v1 environment sizes; a SQL compiler (translate the tree to ``Q`` objects / a CTE) is a
documented future optimization (see FOLLOWUPS). Callers that only need a count or a preview
pass a ``limit`` so a very large environment cannot OOM.

This module is import-safe (no Django models at import time); model access happens inside
``evaluate_segment`` to avoid app-loading order issues.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

# ---------------------------------------------------------------------------
# Operator validity by attribute data_type (the single source of truth — see module docstring).
# ---------------------------------------------------------------------------
_PRESENCE_OPS = frozenset({"isSubmitted", "isSkipped"})
_STRING_EMPTINESS_OPS = frozenset({"isEmpty", "isNotEmpty"})

VALID_OPERATORS_BY_TYPE: dict[str, frozenset[str]] = {
    "string": frozenset(
        {
            "equals",
            "notEquals",
            "contains",
            "doesNotContain",
            "startsWith",
            "endsWith",
            "isOneOf",
        }
    )
    | _PRESENCE_OPS
    | _STRING_EMPTINESS_OPS,
    "number": frozenset(
        {
            "equals",
            "notEquals",
            "greaterThan",
            "greaterThanOrEqual",
            "lessThan",
            "lessThanOrEqual",
            "isOneOf",
        }
    )
    | _PRESENCE_OPS,
    "boolean": frozenset(
        {"equals", "notEquals", "isAccepted", "isNotAccepted"}
    )
    | _PRESENCE_OPS,
    "date": frozenset({"equals", "notEquals", "before", "after"}) | _PRESENCE_OPS,
}

# Operators that DO NOT take a `value` (presence/emptiness checks).
_NO_VALUE_OPS = _PRESENCE_OPS | _STRING_EMPTINESS_OPS | frozenset(
    {"isAccepted", "isNotAccepted"}
)
# Operators whose `value` MUST be an array.
_ARRAY_VALUE_OPS = frozenset({"isOneOf"})

_CONNECTORS = frozenset({"and", "or"})

# Maximum nesting depth of a filter tree (root = depth 1). A filter nested deeper is rejected
# with a 400 (FilterValidationError) BEFORE it can overflow Python's recursion limit — guards
# both validate_filter and matches against a one-request recursion DoS (a ~1000-deep tree would
# otherwise raise RecursionError → HTTP 500). 64 is far beyond any legitimate hand-built or
# UI-built segment while staying well under the interpreter's default limit (~1000).
MAX_FILTER_DEPTH = 64


class FilterDepthExceeded(Exception):
    """Internal sentinel raised when the evaluator walks past MAX_FILTER_DEPTH. Caught at the
    public boundary (matches/evaluate_segment) and surfaced as a FilterValidationError (400)."""


class FilterValidationError(ValueError):
    """A segment ``filter`` is structurally or semantically invalid.

    ``messages`` is a list of human-readable strings suitable for the openapi
    ``ValidationErrorBody.schema_errors`` array (one per problem found, best-effort).
    """

    def __init__(self, messages: list[str]):
        self.messages = messages
        super().__init__("; ".join(messages))


# Maximum total number of nodes (leaves + branches) in a filter tree. A second guard beside the
# depth bound: a WIDE-but-shallow tree (e.g. one group with 10⁶ leaf conditions) is also a DoS
# vector (memory + eval time per contact). Far above any real segment.
MAX_FILTER_NODES = 1000


def check_filter_bounds(node: Any) -> None:
    """Cheap, NON-recursive pre-check of the filter's depth + total node count.

    Run FIRST (before any recursive validator or the jsonschema pass) so a pathological tree is
    rejected with a 400 (FilterValidationError) BEFORE it can overflow Python's / jsonschema's
    recursion limit (which would be a 500). Walks the tree with an explicit stack — no recursion.

    Raises:
        FilterValidationError: if depth > MAX_FILTER_DEPTH or node count > MAX_FILTER_NODES.
    """
    if not isinstance(node, dict) or not node:
        return  # match-all / non-dict → other validators report shape errors.
    # Explicit stack of (node, depth); count nodes as we pop.
    stack: list[tuple[Any, int]] = [(node, 1)]
    count = 0
    while stack:
        current, depth = stack.pop()
        count += 1
        if depth > MAX_FILTER_DEPTH:
            raise FilterValidationError(
                [f"filter: nesting exceeds the maximum depth of {MAX_FILTER_DEPTH}."]
            )
        if count > MAX_FILTER_NODES:
            raise FilterValidationError(
                [f"filter: exceeds the maximum of {MAX_FILTER_NODES} total conditions."]
            )
        if isinstance(current, dict) and "conditions" in current:
            conditions = current.get("conditions")
            if isinstance(conditions, list):
                for child in conditions:
                    stack.append((child, depth + 1))


# ===========================================================================
# Validation (structure + operator/type validity).
# ===========================================================================
def validate_filter(node: Any, *, attribute_types: dict[str, str]) -> None:
    """Validate a filter tree against the language + the environment's attribute key types.

    Args:
        node: the filter JSON (a dict; ``{}`` = match-all and is valid).
        attribute_types: ``{key: data_type}`` for the environment's ``ContactAttributeKey``s.
            A leaf referencing an unknown key is an error (the key must be declared first —
            the API auto-creates keys on contact write, not on segment write).

    Raises:
        FilterValidationError: with all problems found (does not stop at the first).
    """
    errors: list[str] = []
    _validate_node(node, attribute_types=attribute_types, errors=errors, path="filter", depth=1)
    if errors:
        raise FilterValidationError(errors)


def _validate_node(
    node: Any, *, attribute_types: dict[str, str], errors: list[str], path: str, depth: int
) -> None:
    # Depth guard (root = depth 1). Beyond the cap we record a single error and STOP recursing —
    # never let the tree drive Python past its recursion limit (would be a 500; this keeps it 400).
    if depth > MAX_FILTER_DEPTH:
        errors.append(
            f"{path}: filter nesting exceeds the maximum depth of {MAX_FILTER_DEPTH}."
        )
        return

    if not isinstance(node, dict):
        errors.append(f"{path}: each node must be an object.")
        return

    # Empty object → match-all (only valid at the root; a child {} is meaningless).
    if not node:
        if path != "filter":
            errors.append(f"{path}: empty condition is not allowed inside a group.")
        return

    is_branch = "connector" in node or "conditions" in node
    is_leaf = "attributeKey" in node or "operator" in node

    if is_branch and is_leaf:
        errors.append(
            f"{path}: a node is either a group (connector+conditions) or a leaf "
            f"(attributeKey+operator), not both."
        )
        return

    if is_branch:
        _validate_branch(
            node, attribute_types=attribute_types, errors=errors, path=path, depth=depth
        )
    else:
        _validate_leaf(node, attribute_types=attribute_types, errors=errors, path=path)


def _validate_branch(
    node: dict, *, attribute_types: dict[str, str], errors: list[str], path: str, depth: int
) -> None:
    connector = node.get("connector")
    if connector not in _CONNECTORS:
        errors.append(f"{path}.connector: must be one of {sorted(_CONNECTORS)}.")
    conditions = node.get("conditions")
    if not isinstance(conditions, list) or len(conditions) < 1:
        errors.append(f"{path}.conditions: must be a non-empty array.")
        return
    extra = set(node) - {"connector", "conditions"}
    if extra:
        errors.append(f"{path}: unexpected keys on group node: {sorted(extra)}.")
    # If we're at the cap, recording one depth error (children are depth+1 > cap) is enough —
    # don't append one error per child of a pathological tree.
    if depth >= MAX_FILTER_DEPTH:
        errors.append(
            f"{path}: filter nesting exceeds the maximum depth of {MAX_FILTER_DEPTH}."
        )
        return
    for i, child in enumerate(conditions):
        _validate_node(
            child,
            attribute_types=attribute_types,
            errors=errors,
            path=f"{path}.conditions[{i}]",
            depth=depth + 1,
        )


def _validate_leaf(
    node: dict, *, attribute_types: dict[str, str], errors: list[str], path: str
) -> None:
    key = node.get("attributeKey")
    operator = node.get("operator")
    extra = set(node) - {"attributeKey", "operator", "value"}
    if extra:
        errors.append(f"{path}: unexpected keys on leaf: {sorted(extra)}.")
    if not isinstance(key, str) or not key:
        errors.append(f"{path}.attributeKey: must be a non-empty string.")
        return
    if not isinstance(operator, str) or not operator:
        errors.append(f"{path}.operator: must be a non-empty string.")
        return

    data_type = attribute_types.get(key)
    if data_type is None:
        errors.append(
            f"{path}.attributeKey: unknown attribute '{key}' (no ContactAttributeKey in "
            f"this environment)."
        )
        return

    valid = VALID_OPERATORS_BY_TYPE.get(data_type, frozenset())
    if operator not in valid:
        errors.append(
            f"{path}.operator: '{operator}' is not valid for attribute '{key}' of type "
            f"'{data_type}'. Valid: {sorted(valid)}."
        )
        return

    # value presence/shape checks.
    has_value = "value" in node
    if operator in _NO_VALUE_OPS:
        if has_value:
            errors.append(f"{path}: operator '{operator}' takes no value.")
        return
    if not has_value:
        errors.append(f"{path}: operator '{operator}' requires a value.")
        return
    value = node.get("value")
    if operator in _ARRAY_VALUE_OPS:
        if not isinstance(value, list):
            errors.append(f"{path}.value: operator '{operator}' requires an array value.")
    else:
        if isinstance(value, (list | dict)):
            errors.append(
                f"{path}.value: operator '{operator}' requires a scalar value, got "
                f"{type(value).__name__}."
            )


# ===========================================================================
# Evaluation (in-process tree walk).
# ===========================================================================
def _coerce(value_text: str | None, data_type: str):
    """Cast a stored text value to its declared type. Returns a sentinel-free typed value, or
    raises ``ValueError`` if uncastable (callers treat uncastable as no-match)."""
    if value_text is None:
        raise ValueError("absent")
    if data_type == "number":
        try:
            return Decimal(value_text)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("not a number") from exc
    if data_type == "boolean":
        return _coerce_bool(value_text)
    if data_type == "date":
        try:
            return date.fromisoformat(value_text)
        except ValueError as exc:
            raise ValueError("not an ISO date") from exc
    # string (or unknown) → as-is.
    return value_text


def _coerce_bool(text: str) -> bool:
    lowered = str(text).strip().lower()
    if lowered in ("true", "1"):
        return True
    if lowered in ("false", "0"):
        return False
    raise ValueError("not a boolean")


def _coerce_operand(raw, data_type: str):
    """Cast a filter ``value`` (JSON scalar) to the key's type for comparison. Raises
    ``ValueError`` if uncastable."""
    if data_type == "number":
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("operand not a number") from exc
    if data_type == "boolean":
        if isinstance(raw, bool):
            return raw
        return _coerce_bool(str(raw))
    if data_type == "date":
        try:
            return date.fromisoformat(str(raw))
        except ValueError as exc:
            raise ValueError("operand not an ISO date") from exc
    return str(raw)


def _eval_leaf(node: dict, attr_map: dict[str, tuple[str, str | None]]) -> bool:
    """Evaluate one leaf against a contact's attributes.

    Args:
        node: leaf ``{attributeKey, operator, value?}``.
        attr_map: ``{key: (data_type, stored_value_or_None)}`` for this contact, where a key
            present with value ``None`` means the key exists in the environment but the
            contact has no row for it (absent). A key absent from the map also means absent.
    """
    key = node["attributeKey"]
    operator = node["operator"]
    data_type, stored = attr_map.get(key, (None, None))
    present = stored is not None

    # Presence operators first (they don't read a value).
    if operator == "isSubmitted":
        return present
    if operator == "isSkipped":
        return not present
    if operator == "isEmpty":
        return (not present) or stored == ""
    if operator == "isNotEmpty":
        return present and stored != ""

    # Boolean convenience operators (consent-style).
    if operator in ("isAccepted", "isNotAccepted"):
        if not present:
            return False  # absent attribute matches neither isAccepted nor isNotAccepted.
        try:
            as_bool = _coerce_bool(stored)
        except ValueError:
            return False
        return as_bool if operator == "isAccepted" else (not as_bool)

    # All remaining operators are value comparisons → require presence + a castable value.
    if not present:
        return False  # absent attribute never matches a value comparison.
    if data_type is None:
        return False
    try:
        left = _coerce(stored, data_type)
    except ValueError:
        return False  # corrupt/legacy stored value → no match.

    if operator == "isOneOf":
        raw_list = node.get("value") or []
        for raw in raw_list:
            try:
                if left == _coerce_operand(raw, data_type):
                    return True
            except ValueError:
                continue
        return False

    # Scalar comparisons.
    try:
        right = _coerce_operand(node.get("value"), data_type)
    except ValueError:
        return False

    if operator == "equals":
        return left == right
    if operator == "notEquals":
        return left != right
    if operator == "greaterThan":
        return left > right
    if operator == "greaterThanOrEqual":
        return left >= right
    if operator == "lessThan":
        return left < right
    if operator == "lessThanOrEqual":
        return left <= right
    if operator == "before":  # date
        return left < right
    if operator == "after":  # date
        return left > right
    # string text operators.
    if operator == "contains":
        return str(right) in str(left)
    if operator == "doesNotContain":
        return str(right) not in str(left)
    if operator == "startsWith":
        return str(left).startswith(str(right))
    if operator == "endsWith":
        return str(left).endswith(str(right))
    return False  # unknown operator (validation should have caught it).


def matches(node: Any, attr_map: dict[str, tuple[str, str | None]]) -> bool:
    """Return whether a contact (described by ``attr_map``) matches the filter ``node``.

    ``attr_map`` = ``{key: (data_type, stored_value_or_None)}``. ``{}`` (empty filter)
    matches everything.

    Depth-bounded: a tree nested past ``MAX_FILTER_DEPTH`` raises ``FilterValidationError``
    rather than recursing into a ``RecursionError``. In practice writes are gated by
    ``validate_filter`` (which rejects too-deep trees at 400), so this is a defense-in-depth
    backstop for the read/eval path on any already-stored filter.
    """
    try:
        return _matches(node, attr_map, depth=1)
    except FilterDepthExceeded:
        raise FilterValidationError(
            [f"filter: nesting exceeds the maximum depth of {MAX_FILTER_DEPTH}."]
        ) from None


def _matches(node: Any, attr_map: dict[str, tuple[str, str | None]], *, depth: int) -> bool:
    if depth > MAX_FILTER_DEPTH:
        raise FilterDepthExceeded()
    if not isinstance(node, dict) or not node:
        return True  # match-all.
    if "connector" in node:
        conditions = node.get("conditions") or []
        if node["connector"] == "and":
            return all(_matches(child, attr_map, depth=depth + 1) for child in conditions)
        return any(_matches(child, attr_map, depth=depth + 1) for child in conditions)
    return _eval_leaf(node, attr_map)


def _attr_map_for_contact(contact, key_types: dict[str, str]) -> dict[str, tuple[str, str | None]]:
    """Build ``{key: (data_type, stored_value_or_None)}`` for one contact.

    Starts from the environment's key types (so every declared key is represented as absent
    by default), then overlays the contact's stored attribute values. Uses the prefetched
    ``contact.attributes`` (no extra query per contact)."""
    attr_map: dict[str, tuple[str, str | None]] = {
        key: (dtype, None) for key, dtype in key_types.items()
    }
    for attr in contact.attributes.all():  # prefetched
        k = attr.attribute_key.key
        attr_map[k] = (attr.attribute_key.data_type, attr.value)
    return attr_map


def evaluate_segment(filter_def: Any, environment, *, limit: int | None = None) -> list:
    """Resolve which contacts in ``environment`` match ``filter_def`` (in-process; v1).

    Bounded queries: one for the environment's attribute keys, one for the contacts +
    (prefetched) their attributes. Walks the tree per contact in Python. ``limit`` caps the
    number of contacts SCANNED (and thus matched) so preview/count on a huge environment
    cannot OOM — callers needing exact large-scale counts will use the future SQL compiler.

    Returns a list of matched ``Contact`` instances (ordered by ``created_at``, then id).
    Must be called with the tenant schema ACTIVE.
    """
    # Local imports — keep this module import-safe (no models at import time).
    from apps.contacts.models import Contact, ContactAttributeKey

    key_types = dict(
        ContactAttributeKey.objects.filter(environment=environment).values_list(
            "key", "data_type"
        )
    )

    contacts_qs = (
        Contact.objects.filter(environment=environment)
        .prefetch_related("attributes__attribute_key")
        .order_by("created_at", "id")
    )
    if limit is not None:
        contacts_qs = contacts_qs[:limit]

    matched: list = []
    for contact in contacts_qs:
        attr_map = _attr_map_for_contact(contact, key_types)
        if matches(filter_def, attr_map):
            matched.append(contact)
    return matched
