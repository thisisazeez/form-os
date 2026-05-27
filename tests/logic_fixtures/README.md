# Shared TS↔PY Logic-Fixture Suite

> **Status: the evaluator contract + fixture format below are FROZEN** (Wave-0/Wave-1
> contract, per `PLAN.md` §6/§15 and `contracts/decisions.md` ADR-004). Wave 3 implements
> `nextStep(...)` **identically** in TypeScript (React runtime + builder) and Python
> (Django finalize validator) against this exact signature and these exact fixtures.
> Changing the signature, the `expected_next` shape, or the evaluation semantics is a
> **coordinated contract re-broadcast** (`contracts/REVIEW.md` §7) — never a unilateral edit.

This directory is the single corpus of `(survey, answers, currentQuestionId) -> expected_next`
cases that keeps the survey **logic engine** from drifting between the three places it runs:

- the **React runtime** (TS) — decides the next question as the respondent answers,
- the **React builder** (TS) — validates/previews logic,
- the **Django backend** (Python) — finalize validation + analytics path reconstruction.

**One fixture set, two languages, identical expected results.** CI runs both runners on every
push/PR; a divergence fails the build (`contracts/REVIEW.md` §6).

---

## 1. Files

```
tests/logic_fixtures/
├── README.md                  ← this file (the frozen contract)
├── fixtures/*.json            ← the language-neutral fixture corpus
├── run_py.py                  ← Python runner (pytest-discoverable)
├── run_ts.ts                  ← TypeScript runner (vitest)
├── reference/
│   ├── evaluator.py           ← reference Python evaluator (used until Wave 3 ships the real one)
│   └── evaluator.ts           ← reference TS evaluator       (")
├── package.json               ← minimal Node setup (vitest + tsx)
├── tsconfig.json
└── vitest.config.ts
```

The reference evaluators are **clearly marked** as the stand-in implementation. They encode the
frozen semantics below so the harness is runnable *now* and the contract is exercised before
Wave 3 exists. When the real evaluators land, the runners import those instead (see §6,
"Injecting the real evaluator").

---

## 2. The frozen evaluator contract

Both languages implement one function with this signature:

```
nextStep(survey, answersSoFar, currentQuestionId) -> NextStep
```

| Param | Type | Meaning |
|---|---|---|
| `survey` | object | A full survey definition per `contracts/survey-schema.json` (its `questions`, `endings`, `languages`, …). |
| `answersSoFar` | map: questionId → answer | Every answer the respondent has given so far, **including** the just-submitted answer for `currentQuestionId`. Answer value shapes follow `survey-schema.json` `#/$defs/answerShapes`. A key being **absent** means the question was skipped/not-yet-answered; see `isSkipped`/`isSubmitted`. |
| `currentQuestionId` | questionId **or** `null`/`None` | The question that was just answered. The special value `null` (Python `None`) means **"start of survey"** — return the first question to show. |

### Return shape — `NextStep` (FROZEN)

```jsonc
{ "type": "question", "id": "q5" }   // show question q5 next
{ "type": "ending",   "id": "end1" } // show ending screen end1
{ "type": "end" }                    // end of survey, no explicit ending screen (no id)
```

- `type` ∈ `"question" | "ending" | "end"`.
- `id` is present for `question` and `ending`; **omitted** for `end`.
- TS returns `{ type, id? }`; Python returns `{"type": ..., "id": ...}` with `"id"` **omitted**
  (not `None`) for `end`. Runners compare structurally (an absent key and `null` are treated
  as "no id"), so both encodings are accepted, but producers SHOULD omit the key for `end`.

### Evaluation order (FROZEN — verbatim from `survey-schema.json` `#/$defs/action` `$comment`)

For a call with a non-null `currentQuestionId = Q`:

1. The respondent has answered `Q` (its answer is in `answersSoFar`; an optional question may
   be skipped, in which case its key is absent).
2. `Q`'s `logic` rules are evaluated **in array order** against the **full set of answers so
   far**.
3. The **FIRST** rule whose `condition` is true determines the **navigation** action
   (`jumpTo` / `skip` / `endSurvey`). `require` is **not** navigation (see §3).
4. If **no** navigation rule matches, advance to the **next question in linear array order**.
5. A `jumpTo` target MUST be a **later** question id **or** an ending id. **Backward jumps are
   rejected by the validator** (`validateSurvey`, §4) to guarantee termination — the evaluator
   assumes a validated survey.
6. `skip` removes the named target question(s) from the path **for this respondent**; when
   advancing linearly the evaluator **steps over** every skipped question (see §3, "skip is
   cumulative").

For `currentQuestionId = null` (start): return the first question in array order **that is not
skipped** by any already-true `skip` rule (at start, no questions are answered, so this is the
first question — unless the welcome card logic applies, which is a UI concern, not `nextStep`'s).

### Reaching an ending / end-of-survey

- `endSurvey` action with `endingId` → `{ type: "ending", id: endingId }`.
- `endSurvey` action **without** `endingId` → the **first** ending in `survey.endings`
  (`{ type: "ending", id: endings[0].id }`); if there are no endings → `{ type: "end" }`.
- `jumpTo` whose `target` matches an **ending id** → `{ type: "ending", id: target }`.
- Advancing **past the last question** (linear or via skip) → the **first** ending if any
  endings exist, else `{ type: "end" }`.

> **Resolution rule for "fall off the end":** end-of-survey resolves to the first ending when
> `survey.endings` is non-empty, otherwise to `{ type: "end" }`. An explicit `endSurvey` with an
> `endingId` always targets that specific ending. This is frozen so TS and PY agree on the
> no-ending vs. has-ending case.

---

## 3. `require`, `skip`, conditions — precise semantics

### `require` (not navigation)
`require` makes a **later** target question required for this respondent. It does **not** change
the next step. `nextStep` therefore **ignores `require` when picking the navigation target** —
it keeps scanning for the first `jumpTo`/`skip`/`endSurvey`. A separate helper exposes the
required set for the finalize validator:

```
requiredQuestionIds(survey, answersSoFar) -> Set<questionId>
```
= every question with `required: true`, **plus** every `require` action `target` whose rule
condition is true given `answersSoFar` — **but only for rules whose HOST question has already
been answered** (i.e. the host's id is a key in `answersSoFar`).

> **FROZEN interpretation (decides a TS↔PY drift risk).** Because the schema says `require`
> actions are "applied **during answering**" (`survey-schema.json` `#/$defs/action` `$comment`),
> a `require` rule on question `Q` takes effect **only once `Q` itself is answered**. A `require`
> rule on a **not-yet-reached** host does **not** pre-emptively mark its target required. Both
> evaluators implement this, and the fixture *"required — require on a not-yet-answered host does
> not apply"* pins it so Wave 3 cannot diverge. (Provided by the reference evaluator and asserted
> by the `required-gating` fixtures via `expected_required`.)

> Note: because step 3 says the **first matching navigation rule wins**, a `require` rule that
> appears *before* a `jumpTo` rule does **not** block the jump — both can fire (the `require`
> applies to answering; the `jumpTo` decides navigation). Fixtures pin this ordering.

### `skip` is cumulative
A `skip` action removes its `target` (a questionId or array of questionIds) from this
respondent's path. Because `nextStep` is **stateless** (called fresh each step), the set of
skipped questions is **recomputed every call** as:

> For each **answered** question P (i.e. `P ∈ keys(answersSoFar)`), evaluate P's rules in order;
> if the **first matching navigation rule** is a `skip`, add its target(s) to the skip set.

When advancing linearly (steps 4/6) or resolving a `jumpTo`/start, the evaluator **skips over**
any question id in this set. (A `jumpTo` whose explicit target is itself skipped is a survey-
authoring contradiction; the validator may warn, and the evaluator treats an explicit `jumpTo`
target as authoritative — it does not skip the explicitly-targeted question.)

### `isSubmitted` / `isSkipped` / `isEmpty` / `isNotEmpty`
- `isSubmitted` → true iff the question's key is present in `answersSoFar` **with a non-empty
  value** (an answer was actually given).
- `isSkipped` → true iff the question's key is **absent** from `answersSoFar` (optional, not
  answered).
- `isEmpty` → key absent, or present but empty (`""`, `[]`, `{}`, `null`).
- `isNotEmpty` → present and non-empty.

These operators take **no `value`**.

### Operator/type validity
Operators are only valid for certain question types — the table is frozen in
`survey-schema.json`'s `operator` description. The evaluator + builder enforce it; the
validator (`validateSurvey`) rejects an invalid operator/type pairing. Fixtures exercise
multiple operators across question types (`multi-operator` fixtures).

---

## 4. `validateSurvey` (the in-code checks jsonschema cannot do)

Per ADR-004 / `REVIEW.md` §2.3, three checks live in **code** (both languages) and are part of
the contract:

```
validateSurvey(survey) -> { ok: true } | { ok: false, errors: [ { code, message, ... } ] }
```

1. **unique question ids** — `questions[].id` and `endings[].id` are unique across the survey.
2. **no backward `jumpTo`** — every `jumpTo`/`require` target is a **later** question (greater
   array index) or an ending id; targets that don't exist, or point backward/at self, are
   errors. (Error `code`s: `duplicate_id`, `backward_jump`, `unknown_target`.)
3. **operator/type validity** — each `conditionLeaf.operator` is valid for the referenced
   question's `type` (the frozen table in `survey-schema.json`), and `leftQuestionId` references
   an existing question at-or-before the rule's host question. (Error `code`: `invalid_operator`,
   `forward_condition_ref`, `unknown_condition_ref`.)

A fixture with `"expectValid": false` asserts `validateSurvey` returns `ok: false` and that the
expected error `code` appears (it does **not** call `nextStep` on an invalid survey). All other
fixtures are valid surveys and assert `expected_next` (and optionally `expected_required`).

---

## 5. Fixture format (FROZEN)

Each file in `fixtures/` is **either** a single case object **or** an array of case objects.
A case is:

```jsonc
{
  "name": "jumpTo to a later question",          // unique, human-readable
  "description": "NPS detractors jump to reason", // optional
  "survey": { /* full survey per contracts/survey-schema.json */ },

  // --- next-step assertion cases ---
  "answers": { "nps": 3 },             // map questionId -> answer (answersSoFar)
  "currentQuestionId": "nps",          // the just-answered question; null/None = start
  "expected_next": { "type": "question", "id": "reason" },

  // --- optional extra assertions ---
  "expected_required": ["email"],      // OPTIONAL: requiredQuestionIds(survey, answers) must equal this set

  // --- validation-only cases (mutually exclusive with the next-step fields) ---
  "expectValid": false,                // OPTIONAL: assert validateSurvey fails
  "expectedErrorCode": "backward_jump" // OPTIONAL: an error with this code must be present
}
```

Rules:
- A **next-step case** MUST have `survey`, `answers`, `currentQuestionId`, `expected_next`.
  `expected_required` is optional and, when present, additionally asserts
  `requiredQuestionIds(...)`.
- A **validation case** sets `"expectValid": false` (and usually `expectedErrorCode`); it omits
  `expected_next` (the runners skip `nextStep` for it).
- `answers` values follow `survey-schema.json` `#/$defs/answerShapes` (e.g. `singleSelect` →
  choiceId string; `multiSelect` → array of choiceIds; `nps`/`rating`/`number` → number;
  `consent` → boolean; `date` → `"YYYY-MM-DD"`).
- Every `survey` in a fixture MUST itself validate against `contracts/survey-schema.json` (the
  positive cases) — the Python runner validates them with `jsonschema` as a guard, so a fixture
  can't accidentally encode an illegal survey shape. (Validation-only cases intentionally carry
  the in-code violations from §4, which are **not** expressible in JSON Schema, so they still
  pass the JSON-Schema structural check.)

### Test-id / parametrization
Each case's `name` is globally unique across all fixture files and is used as the test id in both
runners, so a failure names the exact case in both languages.

---

## 6. Running the suites

### Python
```bash
# from repo root, with the scaffold's deps installed (pytest, jsonschema)
pytest tests/logic_fixtures/run_py.py -v
```
`run_py.py` is pytest-parametrized over every fixture case (one test per case).

### TypeScript
```bash
cd tests/logic_fixtures
npm ci          # or: npm install
npm test        # runs vitest over run_ts.ts
```

### Injecting the real evaluator (Wave 3)
Both runners resolve the evaluator through a single indirection so the **same fixtures** run
against the **reference** evaluator today and the **real** evaluator once Wave 3 ships it — with
no fixture changes:

- **Python** (`run_py.py`): imports from an env-overridable module path.
  - Default: `reference.evaluator` (this dir's reference impl).
  - Override: set `OPENSURVEY_LOGIC_EVALUATOR` to the real module
    (e.g. `OPENSURVEY_LOGIC_EVALUATOR=apps.surveys.logic.evaluator`). The module MUST export
    `next_step`, `required_question_ids`, and `validate_survey`.
- **TypeScript** (`run_ts.ts`): imports from `./evaluator-under-test.ts`, a one-line re-export
  shim. Today it re-exports `./reference/evaluator`. Wave 3 **repoints that single shim file** to
  the real package (e.g. `export { nextStep, requiredQuestionIds, validateSurvey } from
  "@opensurvey/runtime/logic";`) — `run_ts.ts` and the fixtures are untouched. The target module
  MUST export `nextStep`, `requiredQuestionIds`, and `validateSurvey`.

This is why the contract is **injectable**: the harness asserts behavior, not a specific
implementation. The reference evaluators exist only so the corpus is green before Wave 3.

---

## 7. Naming map (TS ↔ PY)

The contract is one set of behaviors; the surface names differ idiomatically per language. Both
are frozen:

| Concept | TypeScript | Python |
|---|---|---|
| next-step | `nextStep(survey, answersSoFar, currentQuestionId)` | `next_step(survey, answers_so_far, current_question_id)` |
| required set | `requiredQuestionIds(survey, answersSoFar)` | `required_question_ids(survey, answers_so_far)` |
| validate | `validateSurvey(survey)` | `validate_survey(survey)` |
| return | `{ type, id? }` | `{"type": ..., "id"?: ...}` |

Wave 3's real modules MUST export these exact names (the runner shims map to them).
