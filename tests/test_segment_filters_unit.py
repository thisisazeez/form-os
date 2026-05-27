"""
Unit tests for the segment filter mini-language (apps.segments.filters) — pure Python, NO DB.

Exercises the type-aware operator table (validate_filter) and the deterministic in-process
evaluator (matches) across all four data_types, the boolean connectors (and/or, nested), and
the documented edge cases: absent attribute, isOneOf empty array, isSkipped/isSubmitted,
uncastable stored value, empty match-all filter.
"""
from apps.segments import filters


# ---------------------------------------------------------------------------
# matches() — leaf semantics per data_type.
# ---------------------------------------------------------------------------
def _amap(**kv):
    """Build an attr_map {key: (data_type, value)}. Pass key=(type, value)."""
    return dict(kv)


def test_match_all_empty_filter():
    assert filters.matches({}, {}) is True
    assert filters.matches({}, _amap(plan=("string", "pro"))) is True


def test_string_equals_and_contains():
    am = _amap(plan=("string", "pro"))
    assert filters.matches({"attributeKey": "plan", "operator": "equals", "value": "pro"}, am)
    assert not filters.matches({"attributeKey": "plan", "operator": "equals", "value": "free"}, am)
    assert filters.matches({"attributeKey": "plan", "operator": "contains", "value": "r"}, am)
    assert filters.matches(
        {"attributeKey": "plan", "operator": "startsWith", "value": "pr"}, am
    )
    assert filters.matches({"attributeKey": "plan", "operator": "endsWith", "value": "ro"}, am)
    assert filters.matches(
        {"attributeKey": "plan", "operator": "doesNotContain", "value": "z"}, am
    )


def test_number_comparisons_cast_from_text():
    am = _amap(age=("number", "42"))
    assert filters.matches({"attributeKey": "age", "operator": "greaterThan", "value": 40}, am)
    assert filters.matches({"attributeKey": "age", "operator": "lessThanOrEqual", "value": 42}, am)
    assert filters.matches({"attributeKey": "age", "operator": "equals", "value": 42}, am)
    # Numeric equality ignores text formatting (42 == 42.0 == "42").
    assert filters.matches({"attributeKey": "age", "operator": "equals", "value": "42.0"}, am)
    assert not filters.matches({"attributeKey": "age", "operator": "greaterThan", "value": 100}, am)


def test_boolean_operators():
    am_true = _amap(consent=("boolean", "true"))
    am_false = _amap(consent=("boolean", "false"))
    assert filters.matches({"attributeKey": "consent", "operator": "isAccepted"}, am_true)
    assert not filters.matches({"attributeKey": "consent", "operator": "isAccepted"}, am_false)
    assert filters.matches({"attributeKey": "consent", "operator": "isNotAccepted"}, am_false)
    assert filters.matches(
        {"attributeKey": "consent", "operator": "equals", "value": True}, am_true
    )
    assert filters.matches(
        {"attributeKey": "consent", "operator": "notEquals", "value": True}, am_false
    )


def test_date_before_after():
    am = _amap(signup=("date", "2026-03-01"))
    assert filters.matches({"attributeKey": "signup", "operator": "after", "value": "2026-01-01"}, am)
    assert filters.matches({"attributeKey": "signup", "operator": "before", "value": "2026-12-31"}, am)
    assert not filters.matches(
        {"attributeKey": "signup", "operator": "after", "value": "2026-06-01"}, am
    )
    assert filters.matches({"attributeKey": "signup", "operator": "equals", "value": "2026-03-01"}, am)


def test_is_one_of():
    am = _amap(plan=("string", "pro"))
    assert filters.matches(
        {"attributeKey": "plan", "operator": "isOneOf", "value": ["free", "pro", "ent"]}, am
    )
    assert not filters.matches(
        {"attributeKey": "plan", "operator": "isOneOf", "value": ["free", "ent"]}, am
    )
    # Empty array matches nothing.
    assert not filters.matches({"attributeKey": "plan", "operator": "isOneOf", "value": []}, am)


# ---------------------------------------------------------------------------
# Presence / absence edge cases.
# ---------------------------------------------------------------------------
def test_absent_attribute_never_matches_value_comparison():
    # Key declared in env but contact has no value (None) → absent.
    am = _amap(plan=("string", None))
    assert not filters.matches({"attributeKey": "plan", "operator": "equals", "value": "pro"}, am)
    # notEquals on an absent attribute is also False (presence is a precondition).
    assert not filters.matches({"attributeKey": "plan", "operator": "notEquals", "value": "pro"}, am)
    # Key entirely missing from the map → absent too.
    assert not filters.matches({"attributeKey": "ghost", "operator": "equals", "value": "x"}, am)


def test_is_submitted_is_skipped():
    present = _amap(plan=("string", "pro"))
    absent = _amap(plan=("string", None))
    assert filters.matches({"attributeKey": "plan", "operator": "isSubmitted"}, present)
    assert not filters.matches({"attributeKey": "plan", "operator": "isSubmitted"}, absent)
    assert filters.matches({"attributeKey": "plan", "operator": "isSkipped"}, absent)
    assert not filters.matches({"attributeKey": "plan", "operator": "isSkipped"}, present)


def test_is_empty_is_not_empty():
    empty = _amap(note=("string", ""))
    filled = _amap(note=("string", "hi"))
    absent = _amap(note=("string", None))
    assert filters.matches({"attributeKey": "note", "operator": "isEmpty"}, empty)
    assert filters.matches({"attributeKey": "note", "operator": "isEmpty"}, absent)
    assert not filters.matches({"attributeKey": "note", "operator": "isEmpty"}, filled)
    assert filters.matches({"attributeKey": "note", "operator": "isNotEmpty"}, filled)
    assert not filters.matches({"attributeKey": "note", "operator": "isNotEmpty"}, absent)


def test_uncastable_stored_value_no_match():
    # A number key whose stored text is garbage → no comparison matches (never raises).
    am = _amap(age=("number", "not-a-number"))
    assert not filters.matches({"attributeKey": "age", "operator": "greaterThan", "value": 1}, am)
    assert not filters.matches({"attributeKey": "age", "operator": "equals", "value": 0}, am)


# ---------------------------------------------------------------------------
# Boolean connectors (and / or / nested).
# ---------------------------------------------------------------------------
def test_and_connector():
    am = _amap(plan=("string", "pro"), age=("number", "30"))
    tree = {
        "connector": "and",
        "conditions": [
            {"attributeKey": "plan", "operator": "equals", "value": "pro"},
            {"attributeKey": "age", "operator": "greaterThan", "value": 18},
        ],
    }
    assert filters.matches(tree, am)
    # One condition fails → whole AND fails.
    am2 = _amap(plan=("string", "free"), age=("number", "30"))
    assert not filters.matches(tree, am2)


def test_or_connector():
    tree = {
        "connector": "or",
        "conditions": [
            {"attributeKey": "plan", "operator": "equals", "value": "pro"},
            {"attributeKey": "plan", "operator": "equals", "value": "ent"},
        ],
    }
    assert filters.matches(tree, _amap(plan=("string", "ent")))
    assert not filters.matches(tree, _amap(plan=("string", "free")))


def test_deeply_nested_tree():
    # (plan == pro) AND ( (age > 65) OR (country == US AND consent) )
    am = _amap(
        plan=("string", "pro"),
        age=("number", "40"),
        country=("string", "US"),
        consent=("boolean", "true"),
    )
    tree = {
        "connector": "and",
        "conditions": [
            {"attributeKey": "plan", "operator": "equals", "value": "pro"},
            {
                "connector": "or",
                "conditions": [
                    {"attributeKey": "age", "operator": "greaterThan", "value": 65},
                    {
                        "connector": "and",
                        "conditions": [
                            {"attributeKey": "country", "operator": "equals", "value": "US"},
                            {"attributeKey": "consent", "operator": "isAccepted"},
                        ],
                    },
                ],
            },
        ],
    }
    assert filters.matches(tree, am)
    # Break the inner AND (consent false) and the OR branch (age < 65) → fails.
    am2 = dict(am)
    am2["consent"] = ("boolean", "false")
    am2["age"] = ("number", "40")
    assert not filters.matches(tree, am2)


# ---------------------------------------------------------------------------
# validate_filter — operator/type validity + structure.
# ---------------------------------------------------------------------------
TYPES = {"plan": "string", "age": "number", "consent": "boolean", "signup": "date"}


def _validate(node):
    filters.validate_filter(node, attribute_types=TYPES)


def test_validate_accepts_valid_leaf_and_tree():
    _validate({"attributeKey": "plan", "operator": "contains", "value": "x"})
    _validate(
        {
            "connector": "and",
            "conditions": [
                {"attributeKey": "age", "operator": "greaterThan", "value": 1},
                {"attributeKey": "consent", "operator": "isAccepted"},
            ],
        }
    )
    _validate({})  # match-all is valid.


def test_validate_rejects_operator_type_mismatch():
    # greaterThan is not valid for a string attribute.
    try:
        _validate({"attributeKey": "plan", "operator": "greaterThan", "value": "x"})
        raise AssertionError("expected FilterValidationError")
    except filters.FilterValidationError as exc:
        assert any("not valid for attribute 'plan'" in m for m in exc.messages)


def test_validate_rejects_value_on_presence_operator():
    try:
        _validate({"attributeKey": "plan", "operator": "isSkipped", "value": "x"})
        raise AssertionError("expected FilterValidationError")
    except filters.FilterValidationError as exc:
        assert any("takes no value" in m for m in exc.messages)


def test_validate_requires_value_for_comparison():
    try:
        _validate({"attributeKey": "plan", "operator": "equals"})
        raise AssertionError("expected FilterValidationError")
    except filters.FilterValidationError as exc:
        assert any("requires a value" in m for m in exc.messages)


def test_validate_rejects_unknown_attribute_key():
    try:
        _validate({"attributeKey": "ghost", "operator": "equals", "value": "x"})
        raise AssertionError("expected FilterValidationError")
    except filters.FilterValidationError as exc:
        assert any("unknown attribute 'ghost'" in m for m in exc.messages)


def test_validate_rejects_isoneof_non_array_value():
    try:
        _validate({"attributeKey": "plan", "operator": "isOneOf", "value": "pro"})
        raise AssertionError("expected FilterValidationError")
    except filters.FilterValidationError as exc:
        assert any("requires an array value" in m for m in exc.messages)


def test_validate_rejects_mixed_leaf_and_branch():
    try:
        _validate({"connector": "and", "conditions": [], "attributeKey": "plan"})
        raise AssertionError("expected FilterValidationError")
    except filters.FilterValidationError as exc:
        assert exc.messages  # some structural complaint.


def test_validate_rejects_empty_group_conditions():
    try:
        _validate({"connector": "and", "conditions": []})
        raise AssertionError("expected FilterValidationError")
    except filters.FilterValidationError as exc:
        assert any("non-empty array" in m for m in exc.messages)
