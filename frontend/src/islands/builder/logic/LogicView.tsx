// ============================================================================
// onForm — Logic TAB view (rule list + flow map).
//
// The Logic tab body: a left column of IF/THEN rule cards (the survey-wide flat
// view of every question's logic[]) + a right Flow Map preview. Matches
// designs/stitch/builder-logic.png. Empty state when no rules exist.
//
// All edits flow through the logicModel helpers (add / set / delete / reorder),
// each returning a fresh Survey so BuilderApp's reference-equality dirty tracking
// fires and Save (PUT) persists the rules inside question.logic[]. The shared
// validateSurvey (run by Save/Publish) is the authoritative gate; the per-rule
// inline notices here are a convenience mirror.
// ============================================================================
import type { LogicRule, Survey } from "../../../lib/types";
import {
  flattenRules,
  makeRule,
  removeRule,
  reorderRule,
  setRule,
} from "./logicModel";
import { FlowMap } from "./FlowMap";
import { RuleCard } from "./RuleCard";

export interface LogicViewProps {
  survey: Survey;
  onSurveyChange: (next: Survey) => void;
}

export function LogicView({ survey, onSurveyChange }: LogicViewProps) {
  const flat = flattenRules(survey);
  const hasQuestions = survey.questions.length > 0;

  const handleAddRule = () => {
    const rule = makeRule(survey);
    if (!rule) return;
    onSurveyChange(setRule(survey, rule));
  };

  const handleRuleChange = (rule: LogicRule) => {
    onSurveyChange(setRule(survey, rule));
  };

  const handleDeleteRule = (ruleId: string) => {
    onSurveyChange(removeRule(survey, ruleId));
  };

  const handleMove = (ruleId: string, dir: -1 | 1) => {
    onSurveyChange(reorderRule(survey, ruleId, dir));
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
      {/* Rule list column */}
      <div className="border-b border-hairline p-4 lg:border-b-0 lg:border-r">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 className="font-display text-lg font-bold tracking-tight text-ink">Logic rules</h2>
            <p className="mt-0.5 text-sm text-muted">
              Define conditional branching and actions based on respondent answers.
            </p>
          </div>
          <button
            type="button"
            onClick={handleAddRule}
            disabled={!hasQuestions}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-brand-hover disabled:cursor-not-allowed disabled:opacity-50"
            title={hasQuestions ? undefined : "Add a question first"}
          >
            <span aria-hidden="true">+</span> Add logic rule
          </button>
        </div>

        {flat.length === 0 ? (
          <EmptyLogic hasQuestions={hasQuestions} />
        ) : (
          <ol className="space-y-4" aria-label="Logic rules">
            {flat.map((fr, i) => (
              <li key={fr.rule.id}>
                <RuleCard
                  survey={survey}
                  rule={fr.rule}
                  ruleNumber={i + 1}
                  total={flat.length}
                  onChange={handleRuleChange}
                  onDelete={() => handleDeleteRule(fr.rule.id)}
                  onMoveUp={() => handleMove(fr.rule.id, -1)}
                  onMoveDown={() => handleMove(fr.rule.id, 1)}
                />
              </li>
            ))}
          </ol>
        )}
      </div>

      {/* Flow map column */}
      <div className="flex min-h-[24rem] flex-col bg-panel">
        <div className="flex items-center gap-2 border-b border-hairline px-4 py-2.5">
          <span aria-hidden="true">🗺️</span>
          <span className="text-sm font-semibold text-ink">Flow map</span>
        </div>
        <div className="flex-1 overflow-auto">
          <FlowMap survey={survey} />
        </div>
        <p className="border-t border-hairline px-4 py-2 text-center text-xs text-muted">
          Graph updates automatically as you build rules.
        </p>
      </div>
    </div>
  );
}

function EmptyLogic({ hasQuestions }: { hasQuestions: boolean }) {
  return (
    <div className="rounded-xl border border-dashed border-hairline bg-white px-6 py-12 text-center">
      <p className="text-sm font-medium text-ink">No logic yet — your survey runs top to bottom.</p>
      <p className="mt-1 text-sm text-muted">
        {hasQuestions
          ? "Add a rule to branch, skip, or end the survey based on answers."
          : "Add a question on the Build tab first, then come back to add logic."}
      </p>
    </div>
  );
}
