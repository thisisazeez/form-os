// ============================================================================
// surveyModel BLOCK transform tests (vitest).
//
// Covers the immutable, pure block CRUD helpers + the toSurveyWrite blocks carry.
// Every transform returns a FRESH Survey (or the SAME survey on a no-op) and keeps
// the result schema-valid (well-formed block items + the in-code cross-ref invariants:
// questionIds resolve, no question in two blocks, unique block ids). CRITICAL: NO
// transform ever mutates `survey.questions` — the flat array stays source-of-truth.
// ============================================================================
import { describe, expect, it } from "vitest";

import type { Question, Survey } from "../../lib/types";
import {
  addQuestionToBlock,
  createBlock,
  moveBlockDown,
  moveBlockUp,
  newBlockId,
  removeQuestionFromBlock,
  setBlockRandomize,
  setBlockTitle,
  toSurveyWrite,
  ungroupBlock,
  ungroupedQuestionIds,
} from "./surveyModel";

function q(id: string): Question {
  return { id, type: "openText", headline: { default: id }, config: {} };
}

function baseSurvey(ids: string[] = ["q1", "q2", "q3"]): Survey {
  return { name: "S", questions: ids.map(q), languages: [{ code: "en", default: true }] };
}

const BLOCK_ID_RE = /^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/;

describe("newBlockId", () => {
  it("produces a schema-valid id unique among existing", () => {
    const id = newBlockId(["b_aaa", "b_bbb"]);
    expect(id).toMatch(BLOCK_ID_RE);
    expect(["b_aaa", "b_bbb"]).not.toContain(id);
  });
});

describe("createBlock", () => {
  it("appends an empty block with a fresh id; questions untouched", () => {
    const s0 = baseSurvey();
    const s = createBlock(s0, "Demographics", "all");
    expect(s).not.toBe(s0);
    expect(s.questions).toBe(s0.questions); // flat array never touched
    expect(s.blocks).toHaveLength(1);
    expect(s.blocks![0]).toMatchObject({ title: "Demographics", questionIds: [], randomize: "all" });
    expect(s.blocks![0].id).toMatch(BLOCK_ID_RE);
  });

  it("omits title/randomize when blank/none (schema default)", () => {
    const s = createBlock(baseSurvey());
    expect(s.blocks![0]).toEqual({ id: expect.any(String), questionIds: [] });
  });
});

describe("addQuestionToBlock", () => {
  it("adds a real question to a block in display order", () => {
    let s = createBlock(baseSurvey());
    const bid = s.blocks![0].id;
    s = addQuestionToBlock(s, bid, "q1");
    s = addQuestionToBlock(s, bid, "q3");
    expect(s.blocks![0].questionIds).toEqual(["q1", "q3"]);
  });

  it("no-ops for an unknown question id (refs must resolve)", () => {
    let s = createBlock(baseSurvey());
    const bid = s.blocks![0].id;
    const before = s;
    s = addQuestionToBlock(s, bid, "ghost");
    expect(s).toBe(before);
  });

  it("no-ops when the question is already in this block", () => {
    let s = createBlock(baseSurvey());
    const bid = s.blocks![0].id;
    s = addQuestionToBlock(s, bid, "q1");
    const before = s;
    expect(addQuestionToBlock(s, bid, "q1")).toBe(before);
  });

  it("MOVES a question out of another block (single-membership invariant)", () => {
    let s = createBlock(baseSurvey());
    const b1 = s.blocks![0].id;
    s = addQuestionToBlock(s, b1, "q1");
    s = createBlock(s, "Second");
    const b2 = s.blocks![1].id;
    s = addQuestionToBlock(s, b2, "q1"); // move q1 from b1 -> b2
    expect(s.blocks!.find((b) => b.id === b1)!.questionIds).toEqual([]);
    expect(s.blocks!.find((b) => b.id === b2)!.questionIds).toEqual(["q1"]);
  });
});

describe("removeQuestionFromBlock", () => {
  it("drops the grouping ref but keeps the question in questions[]", () => {
    let s = createBlock(baseSurvey());
    const bid = s.blocks![0].id;
    s = addQuestionToBlock(s, bid, "q1");
    s = removeQuestionFromBlock(s, bid, "q1");
    expect(s.blocks![0].questionIds).toEqual([]);
    expect(s.questions.map((x) => x.id)).toEqual(["q1", "q2", "q3"]); // intact
  });

  it("no-ops when the question isn't in the block", () => {
    let s = createBlock(baseSurvey());
    const bid = s.blocks![0].id;
    const before = s;
    expect(removeQuestionFromBlock(s, bid, "q2")).toBe(before);
  });
});

describe("setBlockTitle / setBlockRandomize", () => {
  it("sets and clears the title", () => {
    let s = createBlock(baseSurvey());
    const bid = s.blocks![0].id;
    s = setBlockTitle(s, bid, "Intro");
    expect(s.blocks![0].title).toBe("Intro");
    s = setBlockTitle(s, bid, "   ");
    expect(s.blocks![0].title).toBeUndefined();
  });

  it("sets randomize and drops the key for 'none' (omit-when-default)", () => {
    let s = createBlock(baseSurvey());
    const bid = s.blocks![0].id;
    s = setBlockRandomize(s, bid, "exceptLast");
    expect(s.blocks![0].randomize).toBe("exceptLast");
    s = setBlockRandomize(s, bid, "none");
    expect(s.blocks![0].randomize).toBeUndefined();
  });
});

describe("moveBlockUp / moveBlockDown", () => {
  it("reorders the blocks[] metadata only; questions[] untouched", () => {
    let s = createBlock(baseSurvey(), "A");
    s = createBlock(s, "B");
    const q0 = s.questions;
    const moved = moveBlockDown(s, 0);
    expect(moved.blocks!.map((b) => b.title)).toEqual(["B", "A"]);
    expect(moved.questions).toBe(q0); // flat order never changes
    expect(moveBlockUp(moved, 1).blocks!.map((b) => b.title)).toEqual(["A", "B"]);
  });

  it("no-ops at the boundaries", () => {
    let s = createBlock(baseSurvey(), "A");
    s = createBlock(s, "B");
    expect(moveBlockUp(s, 0)).toBe(s);
    expect(moveBlockDown(s, 1)).toBe(s);
  });
});

describe("ungroupBlock / ungroupedQuestionIds", () => {
  it("removes the block; member questions remain (now ungrouped)", () => {
    let s = createBlock(baseSurvey());
    const bid = s.blocks![0].id;
    s = addQuestionToBlock(s, bid, "q1");
    s = ungroupBlock(s, bid);
    expect(s.blocks).toEqual([]);
    expect(s.questions.map((x) => x.id)).toEqual(["q1", "q2", "q3"]);
  });

  it("ungroupedQuestionIds excludes claimed questions, preserves flat order", () => {
    let s = createBlock(baseSurvey());
    const bid = s.blocks![0].id;
    s = addQuestionToBlock(s, bid, "q2");
    expect(ungroupedQuestionIds(s)).toEqual(["q1", "q3"]);
  });
});

describe("toSurveyWrite blocks carry", () => {
  it("forwards survey.blocks under the same `blocks` body key", () => {
    let s = createBlock(baseSurvey(), "A", "all");
    const bid = s.blocks![0].id;
    s = addQuestionToBlock(s, bid, "q1");
    const body = toSurveyWrite(s);
    expect(body.blocks).toEqual([
      { id: bid, title: "A", randomize: "all", questionIds: ["q1"] },
    ]);
  });

  it("omits blocks entirely when undefined (flat survey unchanged)", () => {
    const s: Survey = baseSurvey();
    const body = toSurveyWrite(s);
    expect("blocks" in body).toBe(false);
  });
});
