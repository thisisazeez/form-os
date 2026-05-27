// ============================================================================
// Within-block presentation randomization tests (lib/blocks.ts).
//
// Asserts:
//   - NO blocks / all randomize:"none" -> applyBlockRandomization returns the SAME
//     reference (identity; flat behavior byte-identical).
//   - "all" shuffles only WITHIN the block's flat slots; non-members + other questions
//     never move; the question OBJECTS are reused (only order changes).
//   - "exceptLast" keeps the final member pinned at the block's last slot.
//   - the permutation is DETERMINISTIC + STABLE for a given seed (back-nav stability),
//     and two seeds generally differ.
//   - logic targets still resolve (nextStep over the permuted survey navigates by id).
// ============================================================================
import { describe, expect, it } from "vitest";

import { nextStep } from "./logic";
import { applyBlockRandomization, presentBlockOrder } from "./blocks";
import type { Block, Question, Survey } from "./types";

function q(id: string): Question {
  return { id, type: "openText", headline: { default: id }, config: {} };
}

function survey(ids: string[], blocks?: Block[]): Survey {
  return {
    name: "S",
    questions: ids.map(q),
    languages: [{ code: "en", default: true }],
    ...(blocks ? { blocks } : {}),
  };
}

const ids = (s: Survey) => s.questions.map((x) => x.id);

describe("applyBlockRandomization — identity (no-blocks / none)", () => {
  it("returns the SAME survey reference when there are no blocks", () => {
    const s = survey(["q1", "q2", "q3"]);
    expect(applyBlockRandomization(s, "seed")).toBe(s);
  });

  it("returns the SAME reference when every block is randomize:none", () => {
    const s = survey(["q1", "q2", "q3"], [{ id: "b1", questionIds: ["q1", "q2"] }]);
    expect(applyBlockRandomization(s, "seed")).toBe(s);
  });

  it("returns the SAME reference for a single-member randomizing block (nothing to shuffle)", () => {
    const s = survey(["q1", "q2"], [{ id: "b1", questionIds: ["q1"], randomize: "all" }]);
    expect(applyBlockRandomization(s, "seed")).toBe(s);
  });
});

describe("applyBlockRandomization — 'all' shuffles within block slots only", () => {
  it("only permutes the block's flat positions; others stay put; objects reused", () => {
    const s = survey(
      ["a", "q1", "q2", "q3", "z"],
      [{ id: "b1", questionIds: ["q1", "q2", "q3"], randomize: "all" }],
    );
    // Find a seed that actually permutes (overwhelmingly likely on the first try).
    let permuted = applyBlockRandomization(s, "seed-1");
    if (ids(permuted).join() === ids(s).join()) permuted = applyBlockRandomization(s, "seed-xyz");
    const got = ids(permuted);
    // Non-members never move.
    expect(got[0]).toBe("a");
    expect(got[4]).toBe("z");
    // The block's three slots still hold exactly the three members (a permutation).
    expect([...got.slice(1, 4)].sort()).toEqual(["q1", "q2", "q3"]);
    // Question OBJECTS are reused by reference (only order changed).
    for (const orig of s.questions) {
      expect(permuted.questions).toContain(orig);
    }
  });
});

describe("applyBlockRandomization — 'exceptLast' pins the final member", () => {
  it("keeps the last block member in the block's last slot", () => {
    const s = survey(
      ["q1", "q2", "q3", "q4"],
      [{ id: "b1", questionIds: ["q1", "q2", "q3"], randomize: "exceptLast" }],
    );
    // Try several seeds: the LAST member ("q3") must always land in the block's last slot (idx 2).
    for (const seed of ["s0", "s1", "s2", "s3", "s4", "s5"]) {
      const got = ids(applyBlockRandomization(s, seed));
      expect(got[2]).toBe("q3"); // pinned
      expect(got[3]).toBe("q4"); // non-member untouched
      expect([...got.slice(0, 2)].sort()).toEqual(["q1", "q2"]);
    }
  });
});

describe("seeded permutation is deterministic + stable", () => {
  it("same seed -> identical order (back-nav stability)", () => {
    const s = survey(
      ["q1", "q2", "q3", "q4", "q5"],
      [{ id: "b1", questionIds: ["q1", "q2", "q3", "q4", "q5"], randomize: "all" }],
    );
    const a = ids(applyBlockRandomization(s, "stable-seed"));
    const b = ids(applyBlockRandomization(s, "stable-seed"));
    expect(a).toEqual(b);
  });

  it("different seeds generally differ", () => {
    const members = ["q1", "q2", "q3", "q4", "q5", "q6"];
    const s = survey(members, [{ id: "b1", questionIds: members, randomize: "all" }]);
    const orders = new Set(
      ["s1", "s2", "s3", "s4", "s5"].map((seed) => ids(applyBlockRandomization(s, seed)).join()),
    );
    // At least two distinct orders across five seeds (vanishingly unlikely to all collide).
    expect(orders.size).toBeGreaterThan(1);
  });

  it("presentBlockOrder is pure for a fixed (block, members, seed)", () => {
    const block: Block = { id: "b1", questionIds: ["q1", "q2", "q3"], randomize: "all" };
    const members = ["q1", "q2", "q3"];
    expect(presentBlockOrder(block, members, "z")).toEqual(presentBlockOrder(block, members, "z"));
    expect(presentBlockOrder({ ...block, randomize: "none" }, members, "z")).toEqual(members);
  });
});

describe("logic still resolves over the permuted survey", () => {
  it("a jumpTo target inside a randomized block lands on that target by id", () => {
    const s: Survey = {
      name: "S",
      languages: [{ code: "en", default: true }],
      questions: [
        {
          id: "start",
          type: "openText",
          headline: { default: "start" },
          config: {},
          logic: [
            {
              id: "r1",
              condition: { leftQuestionId: "start", operator: "isNotEmpty" },
              action: { type: "jumpTo", target: "q3" },
            },
          ],
        },
        q("q1"),
        q("q2"),
        q("q3"),
      ],
      blocks: [{ id: "b1", questionIds: ["q1", "q2", "q3"], randomize: "all" }],
    };
    const flow = applyBlockRandomization(s, "any-seed");
    // The jump resolves by id regardless of the permuted order.
    const step = nextStep(flow, { start: "hi" }, "start");
    expect(step).toEqual({ type: "question", id: "q3" });
  });

  it("with no current question, the FIRST presented step is the first flat slot", () => {
    const s = survey(
      ["q1", "q2", "q3"],
      [{ id: "b1", questionIds: ["q1", "q2", "q3"], randomize: "all" }],
    );
    const flow = applyBlockRandomization(s, "seed-A");
    const first = nextStep(flow, {}, null);
    // The first step is whatever object now occupies flat slot 0 (a block member).
    expect(first.type).toBe("question");
    expect(["q1", "q2", "q3"]).toContain((first as { id: string }).id);
    expect((first as { id: string }).id).toBe(ids(flow)[0]);
  });
});
