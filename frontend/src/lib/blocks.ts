// ============================================================================
// OpenSurvey — block within-block presentation randomization (pure, runtime).
//
// Blocks are an ADDITIVE, OPTIONAL grouping overlay (contracts/survey-schema.json
// #/$defs/block). The flat `questions[]` array is the SINGLE SOURCE OF TRUTH for
// flow order + logic resolution. The ONLY runtime effect of a block is OPTIONAL
// WITHIN-BLOCK randomization: at the FLAT POSITIONS where a randomizing block's
// member questions sit in `questions[]`, those question OBJECTS rotate per a
// per-response seed. Everything else stays put.
//
// HOW IT STAYS LOGIC-SAFE (load-bearing): we never touch the navigator. Instead we
// build a "presentation-permuted survey" — a shallow clone whose `questions[]` is
// the permutation — and hand THAT to the SAME nextStep()/requiredQuestionIds()
// engine. Because:
//   - nextStep resolves jumps/skips/conditions by question ID, not by index, and
//   - _next_linear_after walks the array in whatever order it's given,
// passing the permuted order makes "linear next within a block" fall out for free,
// and the validate-time backward-jump check (run on the ORIGINAL flat order at save)
// can never be tripped at runtime (the runtime never re-validates — it only navigates).
//
// With NO blocks (or all randomize:"none"), the permutation is the IDENTITY — the
// returned survey's `questions[]` is reference-different but ELEMENT-IDENTICAL in
// order, so behavior is byte-identical to flat surveys. (applyBlockRandomization
// returns the SAME survey reference when there is nothing to shuffle, to keep
// downstream memoization honest.)
//
// KNOWN SEMANTIC QUIRK (documented, NOT fixed — DEFERRED): a jumpTo/skip whose target
// is a question INSIDE a randomizing block lands on that target, then advances in the
// PRESENTATION (permuted) order from there. An author who routes logic INTO a block
// that also randomizes owns that consequence. Block-level LOGIC is out of scope.
// ============================================================================
import type { Block, Question, Survey } from "./types";

// --------------------------------------------------------------------------- //
//  seeded RNG (deterministic; same seed -> same permutation)                  //
// --------------------------------------------------------------------------- //
/** Hash an arbitrary string seed to a 32-bit unsigned int (xfnv1a). */
function hashSeed(seed: string): number {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** mulberry32 PRNG — tiny, fast, deterministic. Returns a () => [0,1) generator. */
function mulberry32(a: number): () => number {
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * A deterministic Fisher–Yates shuffle of `items`'s INDICES, seeded by `seed`. Returns the
 * permutation of indices [0..n) — the caller maps it back onto whatever positions it owns.
 * Pure: same (items.length, seed) -> same order; never mutates `items`.
 */
function seededPermutation(n: number, seed: string): number[] {
  const idx = Array.from({ length: n }, (_, i) => i);
  if (n <= 1) return idx;
  const rng = mulberry32(hashSeed(seed));
  for (let i = n - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [idx[i], idx[j]] = [idx[j], idx[i]];
  }
  return idx;
}

// --------------------------------------------------------------------------- //
//  within-block presentation randomization                                    //
// --------------------------------------------------------------------------- //
/**
 * Given a block's member question ids IN FLAT-ARRAY ORDER, return them reordered per the
 * block's `randomize` mode + the per-response `seed`. The seed is salted with the block id
 * so two blocks in one survey don't shuffle in lockstep.
 *   - "none"       -> unchanged (flat order).
 *   - "all"        -> full seeded shuffle.
 *   - "exceptLast" -> shuffle all but the last member (which stays pinned at the end).
 * `memberIdsInFlatOrder` MUST already be in flat-array order (the runtime derives it that way).
 */
export function presentBlockOrder(
  block: Block,
  memberIdsInFlatOrder: string[],
  seed: string,
): string[] {
  const mode = block.randomize ?? "none";
  if (mode === "none" || memberIdsInFlatOrder.length <= 1) {
    return memberIdsInFlatOrder;
  }
  const blockSeed = `${seed}:${block.id}`;
  if (mode === "all") {
    const perm = seededPermutation(memberIdsInFlatOrder.length, blockSeed);
    return perm.map((i) => memberIdsInFlatOrder[i]);
  }
  // "exceptLast": pin the final member; shuffle the rest.
  const head = memberIdsInFlatOrder.slice(0, -1);
  const last = memberIdsInFlatOrder[memberIdsInFlatOrder.length - 1];
  if (head.length <= 1) return memberIdsInFlatOrder; // nothing to shuffle.
  const perm = seededPermutation(head.length, blockSeed);
  return [...perm.map((i) => head[i]), last];
}

/**
 * Build the PRESENTATION-PERMUTED survey the runtime/preview hands to the logic engine.
 *
 * For every block with a non-"none" randomize mode, the FLAT POSITIONS occupied by that
 * block's member questions are filled with the block's presented order (presentBlockOrder).
 * Non-member questions and members of non-randomizing blocks stay exactly where they are.
 *
 * Returns the SAME survey reference (no clone) when there is NOTHING to randomize — so a
 * flat / no-blocks / all-"none" survey is byte-identical end-to-end and downstream
 * memoization isn't churned. Otherwise returns a SHALLOW clone with a fresh `questions[]`
 * (the question OBJECTS are reused by reference — only their ORDER changes).
 */
export function applyBlockRandomization(survey: Survey, seed: string): Survey {
  const blocks = survey.blocks ?? [];
  const randomizing = blocks.filter((b) => (b.randomize ?? "none") !== "none");
  if (randomizing.length === 0) return survey; // nothing to do — identity.

  const questions = survey.questions;
  const indexById = new Map<string, number>();
  questions.forEach((q, i) => indexById.set(q.id, i));

  // Start from the flat order; overwrite only the slots a randomizing block owns.
  const result: Question[] = [...questions];
  let changed = false;

  for (const block of randomizing) {
    // The block's members that actually exist in this survey, IN FLAT-ARRAY ORDER. (A member
    // ref that doesn't resolve is dropped here — it can't occupy a slot. Save-time validation
    // already rejects unknown refs; this is just defensive.)
    const positions = block.questionIds
      .map((id) => indexById.get(id))
      .filter((i): i is number => i !== undefined)
      .sort((a, b) => a - b);
    if (positions.length <= 1) continue; // nothing to reorder.

    const memberIdsInFlatOrder = positions.map((i) => questions[i].id);
    const presented = presentBlockOrder(block, memberIdsInFlatOrder, seed);

    // Drop the presented question objects back into the SAME flat slots, in presented order.
    positions.forEach((slot, k) => {
      const q = questions[indexById.get(presented[k])!];
      if (result[slot] !== q) changed = true;
      result[slot] = q;
    });
  }

  if (!changed) return survey; // every block resolved to the identity order.
  return { ...survey, questions: result };
}
