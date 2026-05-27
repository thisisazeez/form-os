// ============================================================================
// OpenSurvey — Builder SETTINGS tab editor.
//
// Survey-level behavior settings (distinct from SettingsPanel, which edits the
// name/languages on the Build tab). Surfaces the response/lifecycle controls the
// backend ALREADY enforces but had no UI for, plus the IP-capture privacy gate:
//
//   - Survey type           (survey.survey_type)           — Link vs Website/App (WS-11)
//   - Triggers              (survey.triggers)              — ActionClass ids (app only)
//   - Display options       (survey.display_options)       — limit / percentage (app only)
//   - Single-use links     (survey.single_use)            — link consumed on finalize
//   - Response limit        (close_on.by_response_cap)     — auto-close at N finished
//   - Close on a date       (close_on.by_date, ISO UTC)    — auto-close after a time
//   - Capture IP address    (survey.capture_ip)            — store meta.ip (off by default)
//
// The panel reads the survey and emits a FRESH Survey via the surveyModel helpers,
// so BuilderApp's existing onSurveyChange (dirty-tracking + Save) drives persistence.
// `by_date` is stored as ISO 8601 UTC; the <input type=datetime-local> shows/edits it
// in the author's LOCAL wall-clock and converts on both directions (no TZ surprises).
//
// SURVEY TYPE (WS-11): a Link (default) vs Website/App radio. The app-only controls
// (trigger multi-select + display options) render ONLY when the type is "app", so a
// link survey's panel is byte-identical to before. `actionClasses` are injected from
// the host page (the env's ActionClasses) so the trigger picker shows human names; an
// empty list shows a "create an action first" hint linking to the Actions settings page.
// ============================================================================
import { useState } from "react";

import type { Survey } from "../../lib/types";
import type { ActionClass } from "./types";
import {
  patchCloseConditions,
  patchDisplayOptions,
  setCaptureIp,
  setSingleUse,
  setSurveyType,
  toggleTrigger,
} from "./surveyModel";

export interface SurveySettingsPanelProps {
  survey: Survey;
  onChange: (next: Survey) => void;
  /** ActionClasses available in the survey's environment (for the app-survey trigger picker). */
  actionClasses?: ActionClass[];
  /** Routed org slug — used to deep-link to the Actions settings page when none exist yet. */
  orgSlug?: string;
}

/** ISO 8601 UTC -> "YYYY-MM-DDTHH:mm" in the browser's LOCAL time (for datetime-local). */
function isoToLocalInput(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Local datetime-local value -> ISO 8601 UTC (parsed as local wall-clock). */
function localInputToIso(local: string): string {
  if (!local) return "";
  const d = new Date(local);
  if (Number.isNaN(d.getTime())) return "";
  return d.toISOString();
}

/** Default seed when the author first enables "Close on a date": now + 7 days. */
function defaultCloseDateIso(): string {
  return new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();
}

export function SurveySettingsPanel({
  survey,
  onChange,
  actionClasses = [],
  orgSlug,
}: SurveySettingsPanelProps) {
  const close = survey.close_on ?? null;
  const cap = close?.by_response_cap;
  const date = close?.by_date;

  // The section open/closed state is LOCAL so clearing the number/date field (a momentary
  // invalid value that cleanCloseConditions drops to null) does NOT collapse the control
  // mid-edit. `|| <model has a value>` keeps it open after a Discard restores a saved value.
  const [capOpen, setCapOpen] = useState<boolean>(cap !== undefined && cap !== null);
  const [dateOpen, setDateOpen] = useState<boolean>(typeof date === "string" && date !== "");
  const capEnabled = capOpen || (cap !== undefined && cap !== null);
  const dateEnabled = dateOpen || (typeof date === "string" && date !== "");

  // Default channel is LINK (the server default for an absent survey_type). Only an explicit
  // "app" reveals the trigger/display controls — a link survey's panel is byte-identical to before.
  const surveyType = survey.survey_type === "app" ? "app" : "link";
  const isApp = surveyType === "app";
  const selectedTriggers = survey.triggers ?? [];
  const display = survey.display_options ?? {};

  return (
    <div className="space-y-6">
      {/* ---- Survey type (WS-11) ---- */}
      <Group title="Survey type">
        <p className="mb-3 text-xs text-muted/80">
          How respondents reach this survey. Switch to Website &amp; App to show it in-product when a
          tracked action fires.
        </p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <TypeCard
            label="Link"
            hint="A shareable URL you send to respondents."
            checked={!isApp}
            onSelect={() => onChange(setSurveyType(survey, "link"))}
            testId="survey-type-link"
          />
          <TypeCard
            label="Website & App"
            hint="Shown in your product via the JS widget when a tracked action fires."
            checked={isApp}
            onSelect={() => onChange(setSurveyType(survey, "app"))}
            testId="survey-type-app"
          />
        </div>
      </Group>

      {/* ---- App-survey controls (only when type === "app") ---- */}
      {isApp && (
        <>
          <Group title="Triggers">
            <p className="mb-3 text-xs text-muted/80">
              The user actions that show this survey. Define actions on the Actions settings page.
            </p>
            {actionClasses.length === 0 ? (
              <p
                data-testid="no-actions-hint"
                className="rounded-lg border border-dashed border-hairline bg-panel px-3 py-3 text-sm text-muted"
              >
                No actions yet.{" "}
                {orgSlug ? (
                  <a
                    href={`/${orgSlug}/settings/actions`}
                    className="font-medium text-brand hover:underline"
                  >
                    Create an action
                  </a>
                ) : (
                  <span className="font-medium text-ink">Create an action</span>
                )}{" "}
                to trigger this survey.
              </p>
            ) : (
              <div className="space-y-2" data-testid="trigger-list">
                {actionClasses.map((ac) => (
                  <label
                    key={ac.id}
                    className="flex cursor-pointer items-center gap-2.5 rounded-lg border border-hairline bg-white px-3 py-2 text-sm text-ink transition-colors hover:bg-panel"
                  >
                    <input
                      type="checkbox"
                      checked={selectedTriggers.includes(ac.id)}
                      onChange={() => onChange(toggleTrigger(survey, ac.id))}
                      aria-label={`Trigger: ${ac.name}`}
                      className="h-4 w-4 rounded border-hairline text-brand accent-brand focus:ring-brand"
                    />
                    <span className="min-w-0 flex-1 truncate font-medium">{ac.name}</span>
                    <span className="shrink-0 rounded-full bg-panel px-2 py-0.5 font-mono text-xs text-muted">
                      {ac.type}
                    </span>
                  </label>
                ))}
              </div>
            )}
          </Group>

          <Group title="Display options">
            <div className="space-y-4">
              <label className="flex items-center gap-2">
                <span className="w-44 text-xs font-medium text-muted">Show at most (per contact)</span>
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={display.displayLimit ?? ""}
                  placeholder="Unlimited"
                  onChange={(e) =>
                    onChange(
                      patchDisplayOptions(survey, {
                        displayLimit: e.target.value === "" ? undefined : Number(e.target.value),
                      }),
                    )
                  }
                  aria-label="Display limit"
                  className="w-32 rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-sm tabular-nums text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
                />
                <span className="text-xs font-medium text-muted">times</span>
              </label>
              <label className="flex items-center gap-2">
                <span className="w-44 text-xs font-medium text-muted">Show to a percentage</span>
                <input
                  type="number"
                  min={0}
                  max={100}
                  step={1}
                  value={display.displayPercentage ?? ""}
                  placeholder="100"
                  onChange={(e) =>
                    onChange(
                      patchDisplayOptions(survey, {
                        displayPercentage:
                          e.target.value === "" ? undefined : Number(e.target.value),
                      }),
                    )
                  }
                  aria-label="Display percentage"
                  className="w-32 rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-sm tabular-nums text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
                />
                <span className="text-xs font-medium text-muted">% of eligible views</span>
              </label>
            </div>
          </Group>
        </>
      )}

      {/* ---- Responses ---- */}
      <Group title="Responses">
        <div className="space-y-4">
          <Toggle
            label="Single-use links"
            hint="Each response link works once, then expires."
            checked={survey.single_use === true}
            onChange={(v) => onChange(setSingleUse(survey, v))}
          />

          <div>
            <Toggle
              label="Limit responses"
              hint="Automatically close the survey after a number of completed responses."
              checked={capEnabled}
              onChange={(v) => {
                setCapOpen(v);
                onChange(patchCloseConditions(survey, { by_response_cap: v ? (cap ?? 100) : undefined }));
              }}
            />
            {capEnabled && (
              <div className="mt-2 pl-1">
                <label className="flex items-center gap-2">
                  <span className="text-xs font-medium text-muted">Close after</span>
                  <input
                    type="number"
                    min={1}
                    step={1}
                    value={cap ?? ""}
                    onChange={(e) =>
                      onChange(
                        patchCloseConditions(survey, {
                          by_response_cap: e.target.value === "" ? undefined : Number(e.target.value),
                        }),
                      )
                    }
                    aria-label="Response limit"
                    className="w-28 rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-sm tabular-nums text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
                  />
                  <span className="text-xs font-medium text-muted">responses</span>
                </label>
              </div>
            )}
          </div>
        </div>
      </Group>

      {/* ---- Schedule ---- */}
      <Group title="Schedule">
        <Toggle
          label="Close on a date"
          hint="Stop accepting responses after a specific date and time (your local time)."
          checked={dateEnabled}
          onChange={(v) => {
            setDateOpen(v);
            onChange(patchCloseConditions(survey, { by_date: v ? defaultCloseDateIso() : "" }));
          }}
        />
        {dateEnabled && (
          <div className="mt-2 pl-1">
            <input
              type="datetime-local"
              value={isoToLocalInput(date)}
              onChange={(e) =>
                onChange(patchCloseConditions(survey, { by_date: localInputToIso(e.target.value) }))
              }
              aria-label="Close date and time"
              className="rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-sm text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
            />
          </div>
        )}
      </Group>

      {/* ---- Privacy ---- */}
      <Group title="Privacy">
        <Toggle
          label="Capture IP address"
          hint="Store each respondent's IP address with their response. Off by default."
          checked={survey.capture_ip === true}
          onChange={(v) => onChange(setCaptureIp(survey, v))}
        />
      </Group>
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  building blocks                                                            //
// --------------------------------------------------------------------------- //
function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">{title}</h3>
      {children}
    </section>
  );
}

/**
 * A selectable radio "card" for the Survey type control. Renders as a labelled tile that
 * highlights (brand border + tint) when chosen; the hidden radio keeps it keyboard/AT-accessible.
 */
function TypeCard({
  label,
  hint,
  checked,
  onSelect,
  testId,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onSelect: () => void;
  testId?: string;
}) {
  return (
    <label
      data-testid={testId}
      className={
        "flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-3 transition-colors " +
        (checked ? "border-brand bg-brand/5" : "border-hairline bg-white hover:bg-panel")
      }
    >
      <input
        type="radio"
        name="survey-type"
        checked={checked}
        onChange={onSelect}
        className="mt-0.5 h-4 w-4 border-hairline text-brand accent-brand focus:ring-brand"
      />
      <span className="min-w-0">
        <span className="block text-sm font-medium text-ink">{label}</span>
        <span className="block text-xs text-muted/80">{hint}</span>
      </span>
    </label>
  );
}

/** A labelled checkbox row with a one-line hint (matches the lightweight admin aesthetic). */
function Toggle({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2.5">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-4 w-4 rounded border-hairline text-brand accent-brand focus:ring-brand"
      />
      <span className="min-w-0">
        <span className="block text-sm font-medium text-ink">{label}</span>
        {hint && <span className="block text-xs text-muted/80">{hint}</span>}
      </span>
    </label>
  );
}
