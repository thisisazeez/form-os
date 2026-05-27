"""
Tiny constructor helpers for the in-repo survey-template catalog.

Each catalog entry's ``definition`` is a FULL survey definition that must pass
``apps.surveys.validators.validate_survey_definition`` (jsonschema against
``contracts/survey-schema.json`` + the in-code logic checks). Hand-writing those
nested ``{default: "..."}`` i18n maps and per-type ``config`` blocks is verbose and
error-prone, so this module centralizes the boilerplate:

  * ``t(text)``            → an i18nString ``{"default": text}`` (the one required key).
  * ``choice(id, label)``  → a ``{id, label}`` choice for select/ranking questions.
  * ``q_*`` helpers        → one valid question dict per type, with ``config`` pre-filled.
  * ``end_screen(...)``    → an ``endScreen`` ending (the only ending kind the catalog uses).
  * ``rule(...)`` / ``jump_to`` / ``end_survey`` → logic-rule builders (used sparingly,
    keeping branches FORWARD-only so the validator's no-backward-jump check passes).

These are PURE constructors (no Django imports) so the catalog module and its
validation test can import them without app-loading side effects. The single
language every template ships with is English (``EN_LANGUAGES``); ``styling`` is an
empty ``{}`` (runtime applies defaults — the gallery is about questions, not theming).
"""
from __future__ import annotations

from typing import Any

# Every catalog template ships English-only (en is the default/fallback language).
# Exactly one language with default=true is required by #/$defs/surveyLanguages.
EN_LANGUAGES: list[dict[str, Any]] = [{"code": "en", "label": "English", "default": True}]


def t(text: str) -> dict[str, str]:
    """An i18nString: ``{"default": text}`` (the schema's one required key)."""
    return {"default": text}


def choice(choice_id: str, label: str, *, is_other: bool = False) -> dict[str, Any]:
    """A single-/multi-select / ranking choice ``{id, label[, isOther]}``."""
    c: dict[str, Any] = {"id": choice_id, "label": t(label)}
    if is_other:
        c["isOther"] = True
    return c


def _base_q(qid: str, qtype: str, headline: str, *, required: bool, subheader: str | None,
            logic: list[dict[str, Any]] | None) -> dict[str, Any]:
    q: dict[str, Any] = {
        "id": qid,
        "type": qtype,
        "headline": t(headline),
        "required": required,
    }
    if subheader is not None:
        q["subheader"] = t(subheader)
    if logic:
        q["logic"] = logic
    return q


def q_open_text(qid, headline, *, required=False, long=False, placeholder=None,
                subheader=None, logic=None) -> dict[str, Any]:
    config: dict[str, Any] = {"inputType": "long" if long else "short"}
    if placeholder is not None:
        config["placeholder"] = t(placeholder)
    q = _base_q(qid, "openText", headline, required=required, subheader=subheader, logic=logic)
    q["config"] = config
    return q


def q_single_select(qid, headline, choices, *, required=False, display="list",
                    subheader=None, logic=None) -> dict[str, Any]:
    q = _base_q(qid, "singleSelect", headline, required=required, subheader=subheader, logic=logic)
    q["config"] = {"choices": choices, "display": display}
    return q


def q_multi_select(qid, headline, choices, *, required=False, subheader=None,
                   logic=None) -> dict[str, Any]:
    q = _base_q(qid, "multiSelect", headline, required=required, subheader=subheader, logic=logic)
    q["config"] = {"choices": choices}
    return q


def q_rating(qid, headline, *, range_=5, scale="star", required=False, lower=None, upper=None,
             subheader=None, logic=None) -> dict[str, Any]:
    config: dict[str, Any] = {"range": range_, "scale": scale}
    if lower is not None:
        config["lowerLabel"] = t(lower)
    if upper is not None:
        config["upperLabel"] = t(upper)
    q = _base_q(qid, "rating", headline, required=required, subheader=subheader, logic=logic)
    q["config"] = config
    return q


def q_nps(qid, headline, *, required=True, lower=None, upper=None, subheader=None,
          logic=None) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if lower is not None:
        config["lowerLabel"] = t(lower)
    if upper is not None:
        config["upperLabel"] = t(upper)
    q = _base_q(qid, "nps", headline, required=required, subheader=subheader, logic=logic)
    q["config"] = config
    return q


def q_consent(qid, headline, label, *, required=False, subheader=None, logic=None) -> dict[str, Any]:
    q = _base_q(qid, "consent", headline, required=required, subheader=subheader, logic=logic)
    q["config"] = {"label": t(label)}
    return q


def q_number(qid, headline, *, required=False, min_=None, max_=None, placeholder=None,
             subheader=None, logic=None) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if min_ is not None:
        config["min"] = min_
    if max_ is not None:
        config["max"] = max_
    if placeholder is not None:
        config["placeholder"] = t(placeholder)
    q = _base_q(qid, "number", headline, required=required, subheader=subheader, logic=logic)
    q["config"] = config
    return q


def q_contact_info(qid, headline, *, fields, required=False, subheader=None,
                   logic=None) -> dict[str, Any]:
    """``fields`` is a dict like ``{"email": {"show": True, "required": True}}`` (>=1 key)."""
    q = _base_q(qid, "contactInfo", headline, required=required, subheader=subheader, logic=logic)
    q["config"] = {"fields": fields}
    return q


def q_date(qid, headline, *, required=False, fmt="YYYY-MM-DD", subheader=None,
           logic=None) -> dict[str, Any]:
    q = _base_q(qid, "date", headline, required=required, subheader=subheader, logic=logic)
    q["config"] = {"format": fmt}
    return q


def q_ranking(qid, headline, choices, *, required=False, subheader=None,
              logic=None) -> dict[str, Any]:
    q = _base_q(qid, "ranking", headline, required=required, subheader=subheader, logic=logic)
    q["config"] = {"choices": choices}
    return q


def q_cta(qid, headline, *, button_label=None, button_url=None, html=None, required=False,
          subheader=None, logic=None) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if button_label is not None:
        config["buttonLabel"] = t(button_label)
    if button_url is not None:
        config["buttonUrl"] = button_url
    if html is not None:
        config["html"] = t(html)
    q = _base_q(qid, "cta", headline, required=required, subheader=subheader, logic=logic)
    q["config"] = config
    return q


def end_screen(eid, headline, *, subheader=None, button_label=None) -> dict[str, Any]:
    e: dict[str, Any] = {"id": eid, "type": "endScreen", "headline": t(headline)}
    if subheader is not None:
        e["subheader"] = t(subheader)
    if button_label is not None:
        e["buttonLabel"] = t(button_label)
    return e


def rule(rule_id, *, left, operator, value=None, action) -> dict[str, Any]:
    """A single logic rule: ``{id, condition: {leftQuestionId, operator[, value]}, action}``.

    ``left`` MUST be a question id at or before the host question; ``action``'s jumpTo/require
    target MUST be a LATER question id (or an ending id) — both enforced by validate_survey.
    """
    condition: dict[str, Any] = {"leftQuestionId": left, "operator": operator}
    if value is not None:
        condition["value"] = value
    return {"id": rule_id, "condition": condition, "action": action}


def jump_to(target: str) -> dict[str, str]:
    return {"type": "jumpTo", "target": target}


def end_survey(ending_id: str | None = None) -> dict[str, Any]:
    a: dict[str, Any] = {"type": "endSurvey"}
    if ending_id is not None:
        a["endingId"] = ending_id
    return a
