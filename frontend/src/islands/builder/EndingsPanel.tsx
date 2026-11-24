// ============================================================================
// onForm — Builder Welcome-card + Endings editors (the "Endings" tab).
//
// Two content editors that author the survey's non-question screens — both shapes
// already exist in contracts/survey-schema.json (#/$defs/welcomeCard, #/$defs/ending)
// and ALREADY render in the runtime + PreviewPane. This tab is the missing AUTHORING
// UI for them; it emits FRESH Surveys via the surveyModel transforms so BuilderApp's
// dirty-tracking + Save drive persistence (toSurveyWrite forwards welcome_card +
// endings opaquely; the server re-validates against the schema).
//
// i18n: every text field edits the CURRENT authoring language (`lang`, threaded from
// the builder's language switcher) via the shared lang-aware writers. When `lang` is
// the survey default it writes the i18nString `default` key (single-language flow
// byte-identical); for a non-default language it writes that lang key while PRESERVING
// `default` + the other languages. A blank non-default field falls back to `default`.
//
// Ending-id STABILITY: addEnding mints a namespace-unique id (newEndingId, across
// questions + endings + hidden fields); update/text/url/type/reorder never rewrite an
// id, so any logic that already targets an ending (endSurvey.endingId / jumpTo.target)
// keeps resolving. Removing a referenced ending is allowed — validateSurvey reports
// `unknown_target` (surfaced in the Save banner), the same as deleting a referenced
// question, so the author is told rather than silently losing a branch.
// ============================================================================
import { useId } from "react";

import type { Ending, Survey } from "../../lib/types";
import {
  addEnding,
  disableWelcomeCard,
  enableWelcomeCard,
  getLangText,
  moveEndingDown,
  moveEndingUp,
  removeEnding,
  setEndingText,
  setEndingType,
  setEndingUrl,
  setWelcomeButtonLabel,
  setWelcomeHeadline,
  setWelcomeHtml,
} from "./surveyModel";

export interface EndingsTabProps {
  survey: Survey;
  onChange: (next: Survey) => void;
  /** Active authoring language; `default` writes the i18nString `default` key. */
  lang: string;
  /** Survey default-language code (pairs with the `default` key). */
  defaultLang: string;
}

// --------------------------------------------------------------------------- //
//  Welcome card editor                                                        //
// --------------------------------------------------------------------------- //
export function WelcomeCardPanel({ survey, onChange, lang, defaultLang }: EndingsTabProps) {
  const card = survey.welcomeCard;
  const enabled = card?.enabled === true;
  const toggleId = useId();

  return (
    <section data-testid="welcome-card-panel">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Welcome card</h3>
          <p className="mt-1 text-xs text-muted/80">
            An optional intro screen shown before the first question.
          </p>
        </div>
        <label
          htmlFor={toggleId}
          className="flex shrink-0 cursor-pointer items-center gap-2 text-sm text-ink"
        >
          <input
            id={toggleId}
            type="checkbox"
            checked={enabled}
            onChange={(e) =>
              onChange(e.target.checked ? enableWelcomeCard(survey) : disableWelcomeCard(survey))
            }
            className="h-4 w-4 accent-brand"
          />
          {enabled ? "Enabled" : "Disabled"}
        </label>
      </div>

      {enabled ? (
        <div className="space-y-3 rounded-lg border border-hairline bg-white p-3">
          <Field label="Headline">
            <TextInput
              value={getLangText(card?.headline, lang, defaultLang)}
              onChange={(v) => onChange(setWelcomeHeadline(survey, v, lang, defaultLang))}
              placeholder="Welcome"
            />
          </Field>
          <Field label="Body (HTML)" hint="optional">
            <TextArea
              value={getLangText(card?.html, lang, defaultLang)}
              onChange={(v) => onChange(setWelcomeHtml(survey, v, lang, defaultLang))}
              placeholder="<p>Thanks for taking a moment to share your thoughts.</p>"
            />
          </Field>
          <Field label="Start button label" hint="optional">
            <TextInput
              value={getLangText(card?.buttonLabel, lang, defaultLang)}
              onChange={(v) => onChange(setWelcomeButtonLabel(survey, v, lang, defaultLang))}
              placeholder="Start"
            />
          </Field>
        </div>
      ) : (
        <p className="rounded-lg border border-dashed border-hairline bg-panel px-3 py-2 text-xs text-muted">
          The survey starts on the first question. Enable a welcome card to greet respondents first.
        </p>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- //
//  Endings editor                                                             //
// --------------------------------------------------------------------------- //
export function EndingsPanel({ survey, onChange, lang, defaultLang }: EndingsTabProps) {
  const endings: Ending[] = survey.endings ?? [];

  return (
    <section data-testid="endings-panel">
      <div className="mb-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Endings</h3>
        <p className="mt-1 text-xs text-muted/80">
          The thank-you screen(s) shown after the last question. Logic can route respondents to a
          specific ending. With none defined, respondents see the default thank-you.
        </p>
      </div>

      {endings.length === 0 ? (
        <p className="rounded-lg border border-dashed border-hairline bg-panel px-3 py-2 text-xs text-muted">
          No custom endings — respondents see the default thank-you screen.
        </p>
      ) : (
        <div className="space-y-3">
          {endings.map((ending, index) => (
            <EndingCard
              key={ending.id}
              ending={ending}
              index={index}
              count={endings.length}
              lang={lang}
              defaultLang={defaultLang}
              onTypeChange={(type) => onChange(setEndingType(survey, ending.id, type))}
              onText={(field, text) =>
                onChange(setEndingText(survey, ending.id, field, text, lang, defaultLang))
              }
              onUrl={(field, url) => onChange(setEndingUrl(survey, ending.id, field, url))}
              onMoveUp={() => onChange(moveEndingUp(survey, index))}
              onMoveDown={() => onChange(moveEndingDown(survey, index))}
              onRemove={() => onChange(removeEnding(survey, ending.id))}
            />
          ))}
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => onChange(addEnding(survey, "endScreen"))}
          className="rounded-lg border border-dashed border-hairline px-3 py-1.5 text-sm font-medium text-brand transition-colors hover:border-brand hover:bg-brand/5"
        >
          + Add thank-you screen
        </button>
        <button
          type="button"
          onClick={() => onChange(addEnding(survey, "redirect"))}
          className="rounded-lg border border-dashed border-hairline px-3 py-1.5 text-sm font-medium text-brand transition-colors hover:border-brand hover:bg-brand/5"
        >
          + Add redirect
        </button>
      </div>
    </section>
  );
}

function EndingCard({
  ending,
  index,
  count,
  lang,
  defaultLang,
  onTypeChange,
  onText,
  onUrl,
  onMoveUp,
  onMoveDown,
  onRemove,
}: {
  ending: Ending;
  index: number;
  count: number;
  lang: string;
  defaultLang: string;
  onTypeChange: (type: Ending["type"]) => void;
  onText: (field: "headline" | "subheader" | "buttonLabel", text: string) => void;
  onUrl: (field: "redirectUrl" | "buttonLink", url: string) => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onRemove: () => void;
}) {
  const isRedirect = ending.type === "redirect";
  return (
    <div
      data-testid="ending-card"
      className="rounded-lg border border-hairline bg-white p-3 shadow-sm"
    >
      <div className="mb-2.5 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded-full bg-panel px-1 text-[11px] font-semibold text-muted">
            {index + 1}
          </span>
          <span
            className={
              "rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide " +
              (isRedirect ? "bg-secondary/10 text-secondary" : "bg-brand/10 text-brand")
            }
          >
            {isRedirect ? "Redirect" : "Thank-you"}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            onClick={onMoveUp}
            disabled={index === 0}
            aria-label={`Move ending ${index + 1} up`}
            title="Move up"
            className="rounded p-1 text-muted hover:bg-panel hover:text-ink disabled:opacity-30"
          >
            ↑
          </button>
          <button
            type="button"
            onClick={onMoveDown}
            disabled={index === count - 1}
            aria-label={`Move ending ${index + 1} down`}
            title="Move down"
            className="rounded p-1 text-muted hover:bg-panel hover:text-ink disabled:opacity-30"
          >
            ↓
          </button>
          <button
            type="button"
            onClick={onRemove}
            aria-label={`Remove ending ${index + 1}`}
            title="Remove ending"
            className="rounded p-1 text-muted hover:bg-rose-50 hover:text-rose-600"
          >
            ✕
          </button>
        </div>
      </div>

      <div className="space-y-3">
        <Field label="Type">
          <Select
            value={ending.type}
            options={[
              { value: "endScreen", label: "Thank-you screen" },
              { value: "redirect", label: "Redirect to a URL" },
            ]}
            onChange={(v) => onTypeChange(v as Ending["type"])}
          />
        </Field>

        {isRedirect ? (
          <Field label="Redirect URL" hint="required">
            <TextInput
              value={ending.redirectUrl ?? ""}
              onChange={(v) => onUrl("redirectUrl", v)}
              placeholder="https://example.com/thank-you"
            />
          </Field>
        ) : (
          <>
            <Field label="Headline" hint="optional">
              <TextInput
                value={getLangText(ending.headline, lang, defaultLang)}
                onChange={(v) => onText("headline", v)}
                placeholder="Thanks for your feedback!"
              />
            </Field>
            <Field label="Subheader" hint="optional">
              <TextArea
                value={getLangText(ending.subheader, lang, defaultLang)}
                onChange={(v) => onText("subheader", v)}
                placeholder="Your response helps us improve."
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Button label" hint="optional">
                <TextInput
                  value={getLangText(ending.buttonLabel, lang, defaultLang)}
                  onChange={(v) => onText("buttonLabel", v)}
                  placeholder="Visit our site"
                />
              </Field>
              <Field label="Button link" hint="optional">
                <TextInput
                  value={ending.buttonLink ?? ""}
                  onChange={(v) => onUrl("buttonLink", v)}
                  placeholder="https://example.com"
                />
              </Field>
            </div>
            <p className="text-[11px] text-muted/70">
              The button shows only when both a label and a link are set.
            </p>
          </>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Small form primitives (match the QuestionEditor aesthetic)                 //
// --------------------------------------------------------------------------- //
const inputClass =
  "w-full rounded-lg border border-hairline bg-white px-3 py-2 text-sm text-ink " +
  "shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand";

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center gap-2 text-xs font-medium text-muted">
        {label}
        {hint && <span className="font-normal text-muted/70">({hint})</span>}
      </span>
      {children}
    </label>
  );
}

function TextInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <input
      type="text"
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className={inputClass}
    />
  );
}

function TextArea({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <textarea
      value={value}
      placeholder={placeholder}
      rows={3}
      onChange={(e) => onChange(e.target.value)}
      className={inputClass}
    />
  );
}

function Select({
  value,
  options,
  onChange,
}: {
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (v: string) => void;
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className={inputClass}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
