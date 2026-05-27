// ============================================================================
// e2e/pages/seed.ts — read the seed metadata global-setup wrote.
//
// global-setup runs e2e/seed.py and persists the resulting ids to e2e/.auth/seed.json.
// Specs/page-objects import `seed()` to get the org slug + survey id etc., so nothing is
// hardcoded and other agents can reseed without touching every spec.
// ============================================================================
import { readFileSync } from "node:fs";

import { SEED_META } from "../config";

export interface SeedMeta {
  org_slug: string;
  org_id: string;
  schema_name: string;
  owner_email: string;
  owner_id: string;
  workspace_id: string;
  environment_id: string;
  survey_id: string;
  survey_name: string;
  ending_headline: string;
}

let _cache: SeedMeta | null = null;

/** The seed metadata written by global-setup (cached after first read). */
export function seed(): SeedMeta {
  if (_cache === null) {
    _cache = JSON.parse(readFileSync(SEED_META, "utf-8")) as SeedMeta;
  }
  return _cache;
}
