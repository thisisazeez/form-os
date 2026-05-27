"""
In-code validation for survey BLOCK DEFINITIONS (beyond what jsonschema can express).

jsonschema (survey-schema.json #/$defs/block) checks the STRUCTURE — required keys
(id, questionIds), the questionId charset, the randomize enum, additionalProperties:false.
It cannot assert CROSS-REFERENCES (every ``questionIds`` entry must resolve to a real
question; a question id may appear in at most ONE block) nor cross-item uniqueness of block
ids. Those are checked here, mirroring how ``apps.followups.validation.validate_follow_ups``
adds the in-code follow-up cross-ref checks the schema can't.

DESIGN (load-bearing): blocks are an ADDITIVE, OPTIONAL grouping overlay. The flat
``questions[]`` array remains the SINGLE SOURCE OF TRUTH for flow order + logic resolution —
a block NEVER moves questions inside the schema and NEVER reorders ``questions[]``. So the
ONLY references a block makes are into the question-id namespace (its ``questionIds``); a
block id itself is NOT a Response.data key (unlike a hidden-field id), so it is deliberately
NOT cross-checked against the question/ending/hidden-field id namespace — only uniqueness
AMONG blocks is enforced.

Pure functions (no Django/model imports) so ``apps.surveys.validators`` can call this on the
write path without a circular import. Error dicts use the same ``{code, message, ...}`` shape
as the logic + follow-up validators; codes are namespaced ``block_*`` so the view surfaces
them cleanly.
"""
from __future__ import annotations

from typing import Any


def validate_blocks(survey: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the ``blocks`` array against the survey's question-id namespace.

    Returns a list of error dicts (empty == valid). The ``blocks`` key is the SAME in both
    the snake_case model payload and the camelCase schema view (no rename, unlike
    welcome_card/hidden_fields/follow_ups), so a single key lookup serves both call sites.

    Checks (frozen error codes):
      - ``block_duplicate_id``                — two blocks share an id.
      - ``block_unknown_question_ref``        — a block's ``questionIds`` entry is not a
        question id present in ``questions[]``.
      - ``block_question_in_multiple_blocks`` — a question id appears in more than one block
        (a question belongs to AT MOST one block).
    """
    errors: list[dict[str, Any]] = []
    blocks = survey.get("blocks")
    if not isinstance(blocks, list):
        # Absent or structurally wrong (jsonschema's job) -> nothing to cross-check.
        return errors

    # The valid reference namespace: question ids only. (Endings/hidden fields are not
    # groupable into question blocks, so they are NOT valid block members.)
    questions = survey.get("questions") or []
    question_ids = {
        q["id"] for q in questions if isinstance(q, dict) and "id" in q
    }

    seen_block_ids: set[str] = set()
    # Maps a question id -> the FIRST block id that claimed it, to detect double-membership.
    claimed_by: dict[str, str] = {}

    for block in blocks:
        if not isinstance(block, dict):
            continue  # structure errors are jsonschema's job.

        block_id = block.get("id")
        if block_id is not None:
            if block_id in seen_block_ids:
                errors.append({
                    "code": "block_duplicate_id",
                    "message": f"Duplicate block id {block_id!r}",
                    "id": block_id,
                })
            seen_block_ids.add(block_id)

        question_ids_in_block = block.get("questionIds")
        if not isinstance(question_ids_in_block, list):
            continue  # structure errors are jsonschema's job.

        # Track ids seen WITHIN this single block so a within-block duplicate doesn't
        # masquerade as a cross-block double-membership (it's still a single block claiming it).
        within_block: set[str] = set()
        for qid in question_ids_in_block:
            if not isinstance(qid, str):
                continue  # structure errors are jsonschema's job.

            if qid not in question_ids:
                errors.append({
                    "code": "block_unknown_question_ref",
                    "message": (
                        f"block {block_id!r} references unknown question {qid!r}"
                    ),
                    "id": block_id,
                })
                # Don't also claim it for membership — it isn't a real question.
                continue

            if qid in within_block:
                # The same question listed twice in the SAME block: treat as double-membership.
                errors.append({
                    "code": "block_question_in_multiple_blocks",
                    "message": (
                        f"question {qid!r} appears more than once in block {block_id!r}"
                    ),
                    "id": block_id,
                })
                continue
            within_block.add(qid)

            prior = claimed_by.get(qid)
            if prior is not None and prior != block_id:
                errors.append({
                    "code": "block_question_in_multiple_blocks",
                    "message": (
                        f"question {qid!r} appears in more than one block "
                        f"({prior!r} and {block_id!r})"
                    ),
                    "id": block_id,
                })
            else:
                claimed_by[qid] = block_id

    return errors
