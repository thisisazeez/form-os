"""
Python recall resolver tests — the case-for-case mirror of frontend/src/lib/recall.test.ts.

This is the FU-1 drift guard for the follow-up send path: the server renders email
subjects/bodies with ``apps.followups.recall.resolve_recall`` and MUST resolve recall tokens
byte-identically to the TS runtime/builder. Every case here corresponds to a case in
recall.test.ts (same token format, same empty rule, same value rendering). No DB needed.
"""
from apps.followups.recall import has_recall_token, resolve_recall


def test_no_token_text_unchanged():
    assert resolve_recall("Plain headline", {}) == "Plain headline"
    assert resolve_recall("What is your favorite color?", {"q1": "blue"}) == (
        "What is your favorite color?"
    )


def test_empty_or_none_text_is_empty_string():
    assert resolve_recall("", {}) == ""
    assert resolve_recall(None, {}) == ""


def test_replaces_token_with_live_answer():
    assert resolve_recall("Hi #recall:name/fallback:there#!", {"name": "Ada"}) == "Hi Ada!"


def test_fallback_when_unanswered():
    assert resolve_recall("Hi #recall:name/fallback:there#!", {}) == "Hi there!"


def test_fallback_when_empty():
    assert resolve_recall("Hi #recall:name/fallback:guest#", {"name": ""}) == "Hi guest"
    assert resolve_recall("Picks: #recall:q/fallback:none#", {"q": []}) == "Picks: none"
    assert resolve_recall("Addr: #recall:q/fallback:n/a#", {"q": {}}) == "Addr: n/a"


def test_empty_fallback():
    assert resolve_recall("Hello #recall:name/fallback:#", {}) == "Hello "
    assert resolve_recall("Hello #recall:name/fallback:#", {"name": "Ada"}) == "Hello Ada"


def test_multiple_tokens():
    text = "Hi #recall:first/fallback:friend#, on the #recall:plan/fallback:free# plan."
    assert resolve_recall(text, {"first": "Ada", "plan": "pro"}) == "Hi Ada, on the pro plan."


def test_mixed_present_and_absent():
    text = "Hi #recall:first/fallback:friend# (#recall:plan/fallback:free#)"
    assert resolve_recall(text, {"first": "Ada"}) == "Hi Ada (free)"


def test_hidden_field_value_recall():
    # Hidden-field values live in the same answers map (captured from the URL by the runtime).
    assert resolve_recall(
        "Welcome back, user #recall:userId/fallback:guest#", {"userId": "123"}
    ) == "Welcome back, user 123"
    assert resolve_recall(
        "Welcome back, user #recall:userId/fallback:guest#", {}
    ) == "Welcome back, user guest"


def test_number_boolean_array_rendering():
    assert resolve_recall("Score: #recall:nps/fallback:?#", {"nps": 9}) == "Score: 9"
    assert resolve_recall("Agree: #recall:c/fallback:?#", {"c": True}) == "Agree: Yes"
    assert resolve_recall("Agree: #recall:c/fallback:?#", {"c": False}) == "Agree: No"
    assert resolve_recall("Picks: #recall:m/fallback:?#", {"m": ["a", "b"]}) == "Picks: a, b"


def test_other_choice_typed_text():
    answers = {"sel": {"choiceId": "other", "other": "Custom thing"}}
    assert resolve_recall("You said: #recall:sel/fallback:nothing#", answers) == (
        "You said: Custom thing"
    )


def test_object_shaped_answer_falls_back():
    answers = {"contact": {"firstName": "Ada", "email": "a@x.com"}}
    assert resolve_recall("Hi #recall:contact/fallback:friend#", answers) == "Hi friend"


def test_malformed_token_left_literal():
    text = "Broken #recall:name# token"
    assert resolve_recall(text, {"name": "Ada"}) == "Broken #recall:name# token"


def test_hash_in_fallback_not_part_of_token():
    # The fallback charset excludes '#': "#recall:a/fallback:x#y#" matches with fallback "x"
    # and leaves the trailing "y#" as literal text — identical to the TS rule.
    assert resolve_recall("#recall:a/fallback:x#y#", {}) == "xy#"


def test_no_lastindex_bleed_between_calls():
    text = "#recall:a/fallback:A#"
    assert resolve_recall(text, {"a": "x"}) == "x"
    assert resolve_recall(text, {"a": "y"}) == "y"


def test_has_recall_token():
    assert has_recall_token("Hi #recall:name/fallback:x#") is True
    assert has_recall_token("Plain text") is False
    assert has_recall_token(None) is False
    assert has_recall_token("#recall:bad#") is False
