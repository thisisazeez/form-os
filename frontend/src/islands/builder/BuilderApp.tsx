// ============================================================================
// onForm — Survey Builder app (top-level component).
//
// Owns the editable DRAFT survey (the shared ../../lib/types shape) and wires:
//   - QuestionPalette  → add a schema-valid question
//   - QuestionList     → select / reorder (up/down) / delete
//   - QuestionEditor   → per-type settings for the selected question
//   - SettingsPanel    → survey name + languages (read-only chips)
//   - PreviewPane ★    → live respondent preview (reuses the shared runtime lib)
//   - Save             → pre-validates with the shared validateSurvey, then PUTs
//                        the SurveyWrite envelope; surfaces server schema/logic
//                        errors (400 ValidationErrorBody).
//   - Publish          → client-gates with the SAME validateSurvey, then POSTs the
//                        status transition ({status:"published"}); reflects the new
//                        status pill on success, surfaces 400/403 {detail} errors.
//   - Discard          → reverts the in-memory survey to the last-saved snapshot.
//   - ← Back           → full-page nav back to /{orgSlug}/surveys.
//
// A `dirty` flag (survey !== lastSavedSurvey by reference — every surveyModel
// transform returns a new object) gates Save/Discard and disables Publish until the
// draft is saved (the status endpoint does NOT accept the survey definition, so
// publishing while dirty would silently drop edits).
//
// Layout: a top toolbar (back · name + status pill · Save · Publish), a left
// authoring column (settings + palette + list + editor) and a right live-preview
// column. Uses the brand Tailwind tokens defined in templates/base.html (the host
// templates/builder/edit.html extends it): brand accent, ink/muted neutrals,
// hairline borders, panel/surface insets, success/rose semantics.
// ============================================================================
import { useCallback, useEffect, useMemo, useState } from "react";

import { defaultLanguageCode } from "../../lib/i18n";
import { validateSurvey } from "../../lib/logic";
import type { Question, Styling, Survey, SurveyStatus } from "../../lib/types";
import { BuilderApi, SaveError, type SavedSurvey } from "./api";
import { LogicView } from "./logic/LogicView";
import { PreviewPane } from "./PreviewPane";
import { BlocksPanel } from "./BlocksPanel";
import { EndingsPanel, WelcomeCardPanel } from "./EndingsPanel";
import { FollowUpsPanel } from "./FollowUpsPanel";
import { HiddenFieldsPanel } from "./HiddenFieldsPanel";
import { QuestionEditor } from "./QuestionEditor";
import { QuestionList } from "./QuestionList";
import { QuestionPalette } from "./QuestionPalette";
import { QuotasPanel } from "./QuotasPanel";
import { SettingsPanel } from "./SettingsPanel";
import { StylingPanel } from "./StylingPanel";
import { SurveySettingsPanel } from "./SurveySettingsPanel";
import { VariablesPanel } from "./VariablesPanel";
import {
  addLanguage,
  addQuestion,
  deleteQuestion,
  makeQuestion,
  moveQuestionDown,
  moveQuestionUp,
  normalizeInitialSurvey,
  patchStyling,
  removeLanguage,
  setDefaultLanguage,
  toSurveyWrite,
  updateQuestion,
} from "./surveyModel";
import type { ActionClass, BuilderTab, CoreQuestionType, PublishState, SaveState } from "./types";

export interface BuilderAppProps {
  api: BuilderApi;
  initialSurvey: Survey;
  /** The routed org slug (mount contract data-org-slug) — used for the back link. */
  orgSlug: string;
  /** ActionClasses in the survey's environment (host-injected) — the app-survey trigger picker. */
  actionClasses?: ActionClass[];
}

/** Read the admin-only `status` off a survey echo (the shared type omits it). */
function readStatus(s: Survey): SurveyStatus {
  const v = (s as SavedSurvey).status;
  return v ?? "draft";
}

export function BuilderApp({ api, initialSurvey, orgSlug, actionClasses = [] }: BuilderAppProps) {
  const [survey, setSurvey] = useState<Survey>(initialSurvey);
  // The last server-persisted (or initial) snapshot. `dirty` is reference-inequality
  // against this; Discard restores it. Save/Publish replace it with the server echo.
  const [lastSavedSurvey, setLastSavedSurvey] = useState<Survey>(initialSurvey);
  const [status, setStatus] = useState<SurveyStatus>(() => readStatus(initialSurvey));
  const [selectedId, setSelectedId] = useState<string | null>(
    initialSurvey.questions[0]?.id ?? null,
  );
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });
  const [publishState, setPublishState] = useState<PublishState>({ kind: "idle" });
  const [tab, setTab] = useState<BuilderTab>("build");

  // The authoring language: which language's text the editor edits + the preview
  // renders. UI-only state (NOT persisted) — defaults to the survey default. When
  // only one language is configured this stays the default and the per-string
  // language affordances stay hidden (single-language flow unchanged).
  const defaultLang = useMemo(
    () => defaultLanguageCode(survey.languages),
    [survey.languages],
  );
  const [activeLang, setActiveLang] = useState<string>(defaultLang);

  // Keep the active language valid: if the selected language is removed (or its
  // code changes, e.g. the default flips), clamp back to the current default.
  useEffect(() => {
    const langs = survey.languages ?? [];
    if (!langs.some((l) => l.code === activeLang)) {
      setActiveLang(defaultLang);
    }
  }, [survey.languages, activeLang, defaultLang]);

  // Reference equality is sound: every surveyModel transform returns a fresh object.
  const dirty = survey !== lastSavedSurvey;

  const selected = useMemo(
    () => survey.questions.find((q) => q.id === selectedId) ?? null,
    [survey.questions, selectedId],
  );

  /** Mark an in-memory edit: clear transient save/publish notices. */
  const onEdited = useCallback(() => {
    setSaveState({ kind: "idle" });
    setPublishState({ kind: "idle" });
  }, []);

  // --- question CRUD --- //
  const handleAdd = useCallback(
    (type: CoreQuestionType) => {
      setSurvey((s) => {
        const q = makeQuestion(
          type,
          s.questions.map((x) => x.id),
        );
        setSelectedId(q.id);
        return addQuestion(s, q);
      });
      onEdited();
    },
    [onEdited],
  );

  const handleDelete = useCallback(
    (id: string) => {
      setSurvey((s) => deleteQuestion(s, id));
      setSelectedId((cur) => {
        if (cur !== id) return cur;
        // Select a neighbour so the editor isn't left empty after a delete.
        const idx = survey.questions.findIndex((q) => q.id === id);
        const next = survey.questions[idx + 1] ?? survey.questions[idx - 1];
        return next?.id ?? null;
      });
      onEdited();
    },
    [survey.questions, onEdited],
  );

  const handleUpdate = useCallback(
    (q: Question) => {
      setSurvey((s) => updateQuestion(s, q));
      onEdited();
    },
    [onEdited],
  );

  const handleMoveUp = useCallback(
    (index: number) => {
      setSurvey((s) => moveQuestionUp(s, index));
      onEdited();
    },
    [onEdited],
  );

  const handleMoveDown = useCallback(
    (index: number) => {
      setSurvey((s) => moveQuestionDown(s, index));
      onEdited();
    },
    [onEdited],
  );

  const handleName = useCallback(
    (name: string) => {
      setSurvey((s) => ({ ...s, name }));
      onEdited();
    },
    [onEdited],
  );

  // --- language management (Build-tab Languages section) --- //
  const handleAddLanguage = useCallback(
    (code: string, label: string) => {
      setSurvey((s) => addLanguage(s, code, label));
      onEdited();
    },
    [onEdited],
  );

  const handleRemoveLanguage = useCallback(
    (code: string) => {
      setSurvey((s) => removeLanguage(s, code));
      onEdited();
    },
    [onEdited],
  );

  const handleSetDefaultLanguage = useCallback(
    (code: string) => {
      setSurvey((s) => setDefaultLanguage(s, code));
      onEdited();
    },
    [onEdited],
  );

  // The Logic tab edits the whole survey (logic rules live in question.logic[]).
  // The logicModel helpers return a fresh Survey, so dirty-tracking fires here too.
  const handleSurveyChange = useCallback(
    (next: Survey) => {
      setSurvey(next);
      onEdited();
    },
    [onEdited],
  );

  // The Style tab edits survey.styling; patchStyling merges + cleans to a
  // schema-valid object and returns a fresh Survey (dirty-tracking fires).
  const handleStylingChange = useCallback(
    (patch: Partial<Styling>) => {
      setSurvey((s) => patchStyling(s, patch));
      onEdited();
    },
    [onEdited],
  );

  // --- discard (revert to the last-saved snapshot) --- //
  const handleDiscard = useCallback(() => {
    if (!dirty) return;
    setSurvey(lastSavedSurvey);
    setSelectedId(lastSavedSurvey.questions[0]?.id ?? null);
    setSaveState({ kind: "idle" });
    setPublishState({ kind: "idle" });
  }, [dirty, lastSavedSurvey]);

  // --- save (pre-validate → PUT → surface errors) --- //
  const handleSave = useCallback(async () => {
    setSaveState({ kind: "saving" });
    setPublishState({ kind: "idle" });

    // Client-side gate using the SAME validator the server runs (shared lib).
    // Catches schema/logic errors before the round-trip; the server is still the
    // source of truth and may add checks (uniqueness, backward-jump) we surface
    // identically from its 400 response.
    const result = validateSurvey(survey);
    if (!result.ok) {
      setSaveState({ kind: "error", messages: result.errors.map((e) => e.message) });
      return;
    }

    try {
      // Adopt the server's canonical echo (ids/normalized blobs) as the new draft AND the new
      // last-saved snapshot (clears the dirty flag). The echo is snake_case (follow_ups /
      // welcome_card / hidden_fields), so it MUST go through the SAME snake→camel bridge as the
      // initial load — otherwise those fields vanish from the builder the instant you Save.
      const saved = normalizeInitialSurvey(await api.saveSurvey(toSurveyWrite(survey)));
      setSurvey(saved);
      setLastSavedSurvey(saved);
      setStatus(readStatus(saved));
      setSaveState({ kind: "saved", at: Date.now() });
    } catch (e) {
      if (e instanceof SaveError) {
        setSaveState({ kind: "error", messages: e.messages });
      } else {
        setSaveState({ kind: "error", messages: ["Unexpected error while saving."] });
      }
    }
  }, [api, survey]);

  // --- publish (client-gate → POST status → reflect new status) --- //
  const handlePublish = useCallback(async () => {
    setPublishState({ kind: "publishing" });
    setSaveState({ kind: "idle" });

    // The status endpoint does NOT re-validate the definition (and the schema has no
    // minItems on questions), so an empty survey would otherwise publish silently.
    // Guard it explicitly with the task's named failure mode.
    if (survey.questions.length === 0) {
      setPublishState({
        kind: "error",
        messages: ["Add at least one question before publishing."],
      });
      return;
    }
    // Also run the SAME logic validator Save uses, to catch duplicate ids / unknown or
    // backward jump targets before the round-trip (the server is still authoritative).
    const result = validateSurvey(survey);
    if (!result.ok) {
      setPublishState({ kind: "error", messages: result.errors.map((e) => e.message) });
      return;
    }

    try {
      const updated = await api.publishSurvey();
      // The status endpoint changes ONLY the status; Publish is enabled only when the
      // draft is already saved (not dirty), so the in-memory survey already matches the
      // persisted definition. Reflect just the new status pill from the server echo.
      setStatus(readStatus(updated));
      setPublishState({ kind: "idle" });
    } catch (e) {
      if (e instanceof SaveError) {
        setPublishState({ kind: "error", messages: e.messages });
      } else {
        setPublishState({ kind: "error", messages: ["Unexpected error while publishing."] });
      }
    }
  }, [api, survey]);

  // A survey is publishable from draft or paused (the lifecycle edges into published).
  const canPublishStatus = status === "draft" || status === "paused";
  const banner =
    saveState.kind === "error"
      ? { title: "Couldn’t save the survey", messages: saveState.messages }
      : publishState.kind === "error"
        ? { title: "Couldn’t publish the survey", messages: publishState.messages }
        : null;

  return (
    <div className="overflow-hidden rounded-xl border border-hairline bg-white shadow-sm">
      <Toolbar
        name={survey.name ?? "Untitled survey"}
        status={status}
        dirty={dirty}
        saveState={saveState}
        publishState={publishState}
        canPublishStatus={canPublishStatus}
        backHref={`/${orgSlug}/surveys`}
        tab={tab}
        onTabChange={setTab}
        onSave={handleSave}
        onPublish={handlePublish}
        onDiscard={handleDiscard}
      />

      {banner && <ErrorBanner title={banner.title} messages={banner.messages} />}

      {tab === "build" && (
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          {/* Authoring column */}
          <div className="space-y-5 border-b border-hairline p-4 lg:border-b-0 lg:border-r">
            <Section>
              <SettingsPanel
                survey={survey}
                onNameChange={handleName}
                activeLang={activeLang}
                onActiveLangChange={setActiveLang}
                onAddLanguage={handleAddLanguage}
                onRemoveLanguage={handleRemoveLanguage}
                onSetDefaultLanguage={handleSetDefaultLanguage}
              />
            </Section>

            <Section>
              <QuestionPalette onAdd={handleAdd} />
            </Section>

            <Section>
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
                Questions
              </h2>
              <QuestionList
                survey={survey}
                selectedId={selectedId}
                onSelect={setSelectedId}
                onMoveUp={handleMoveUp}
                onMoveDown={handleMoveDown}
                onDelete={handleDelete}
              />
            </Section>

            {selected && (
              <Section>
                <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">
                  Edit question
                </h2>
                <QuestionEditor
                  question={selected}
                  onChange={handleUpdate}
                  lang={activeLang}
                  defaultLang={defaultLang}
                  survey={survey}
                />
              </Section>
            )}
          </div>

          {/* Live preview column */}
          <div className="min-h-[28rem] bg-panel">
            <PreviewPane survey={survey} lang={activeLang} />
          </div>
        </div>
      )}

      {tab === "style" && (
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          {/* Theming column */}
          <div className="border-b border-hairline p-4 lg:border-b-0 lg:border-r">
            <StylingPanel styling={survey.styling ?? {}} onChange={handleStylingChange} />
          </div>

          {/* Live preview column — reflects styling edits instantly. */}
          <div className="min-h-[28rem] bg-panel">
            <PreviewPane survey={survey} lang={activeLang} />
          </div>
        </div>
      )}

      {tab === "endings" && (
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          {/* Content column: welcome card + endings authoring */}
          <div className="space-y-5 border-b border-hairline p-4 lg:border-b-0 lg:border-r">
            {/* Compact editing-language switcher — only meaningful with >1 language. */}
            {(survey.languages?.length ?? 0) > 1 && (
              <label className="flex items-center justify-end gap-2 text-xs font-medium text-muted">
                Editing language
                <select
                  value={activeLang}
                  onChange={(e) => setActiveLang(e.target.value)}
                  aria-label="Editing language"
                  className="rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-sm text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
                >
                  {(survey.languages ?? []).map((l) => (
                    <option key={l.code} value={l.code}>
                      {l.label?.trim() ? l.label : l.code}
                      {l.default ? " (default)" : ""}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <Section>
              <WelcomeCardPanel
                survey={survey}
                onChange={handleSurveyChange}
                lang={activeLang}
                defaultLang={defaultLang}
              />
            </Section>
            <Section>
              <EndingsPanel
                survey={survey}
                onChange={handleSurveyChange}
                lang={activeLang}
                defaultLang={defaultLang}
              />
            </Section>
          </div>

          {/* Live preview column — reflects welcome/ending edits instantly. */}
          <div className="min-h-[28rem] bg-panel">
            <PreviewPane survey={survey} lang={activeLang} />
          </div>
        </div>
      )}

      {tab === "settings" && (
        <div className="p-4">
          <div className="mx-auto max-w-2xl space-y-8">
            <SurveySettingsPanel
              survey={survey}
              onChange={handleSurveyChange}
              actionClasses={actionClasses}
              orgSlug={orgSlug}
            />
            <HiddenFieldsPanel survey={survey} onChange={handleSurveyChange} />
            <VariablesPanel survey={survey} onChange={handleSurveyChange} />
            <QuotasPanel
              survey={survey}
              onChange={handleSurveyChange}
              lang={activeLang}
              defaultLang={defaultLang}
            />
          </div>
        </div>
      )}

      {tab === "followups" && (
        <div className="p-4">
          <div className="mx-auto max-w-2xl">
            {/* Editing-language switcher — meaningful only with >1 language (i18n subject/body). */}
            {(survey.languages?.length ?? 0) > 1 && (
              <label className="mb-4 flex items-center justify-end gap-2 text-xs font-medium text-muted">
                Editing language
                <select
                  value={activeLang}
                  onChange={(e) => setActiveLang(e.target.value)}
                  aria-label="Editing language"
                  className="rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-sm text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
                >
                  {(survey.languages ?? []).map((l) => (
                    <option key={l.code} value={l.code}>
                      {l.label?.trim() ? l.label : l.code}
                      {l.default ? " (default)" : ""}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <FollowUpsPanel
              survey={survey}
              onChange={handleSurveyChange}
              lang={activeLang}
              defaultLang={defaultLang}
            />
          </div>
        </div>
      )}

      {tab === "blocks" && (
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          {/* Block-grouping column */}
          <div className="border-b border-hairline p-4 lg:border-b-0 lg:border-r">
            <div className="mx-auto max-w-2xl">
              <BlocksPanel
                survey={survey}
                onChange={handleSurveyChange}
                lang={activeLang}
                defaultLang={defaultLang}
              />
            </div>
          </div>

          {/* Live preview column — reflects within-block randomization (press Restart to
              reshuffle). With no randomizing block the preview is byte-identical to flat. */}
          <div className="min-h-[28rem] bg-panel">
            <PreviewPane survey={survey} lang={activeLang} />
          </div>
        </div>
      )}

      {tab === "logic" && <LogicView survey={survey} onSurveyChange={handleSurveyChange} />}
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Toolbar + status pill + error banner                                       //
// --------------------------------------------------------------------------- //
function Toolbar({
  name,
  status,
  dirty,
  saveState,
  publishState,
  canPublishStatus,
  backHref,
  tab,
  onTabChange,
  onSave,
  onPublish,
  onDiscard,
}: {
  name: string;
  status: SurveyStatus;
  dirty: boolean;
  saveState: SaveState;
  publishState: PublishState;
  canPublishStatus: boolean;
  backHref: string;
  tab: BuilderTab;
  onTabChange: (tab: BuilderTab) => void;
  onSave: () => void;
  onPublish: () => void;
  onDiscard: () => void;
}) {
  const saving = saveState.kind === "saving";
  const publishing = publishState.kind === "publishing";
  // Publish is offered only while the survey can transition into `published`, and only
  // once the draft is saved (the status endpoint can't carry unsaved edits).
  const publishDisabled = !canPublishStatus || dirty || saving || publishing;

  return (
    <div className="flex items-center gap-3 border-b border-hairline bg-white px-4 py-3">
      <div className="flex min-w-0 shrink items-center gap-3">
        <a
          href={backHref}
          className="inline-flex shrink-0 items-center gap-1 rounded-lg px-2 py-1.5 text-sm font-medium text-muted transition-colors hover:bg-panel hover:text-ink"
        >
          <span aria-hidden="true">←</span> Back to surveys
        </a>
        <span className="hidden h-5 w-px bg-hairline sm:block" aria-hidden="true" />
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-base font-semibold text-ink font-display">{name}</h1>
            <StatusPill status={status} />
          </div>
          <p className="text-xs text-muted">
            Survey builder
            {dirty && (
              <span className="ml-2 text-brand" data-testid="unsaved-hint">
                • Unsaved changes
              </span>
            )}
          </p>
        </div>
      </div>

      <div className="flex min-w-0 flex-1 justify-center overflow-x-auto">
        <TabSwitcher tab={tab} onTabChange={onTabChange} />
      </div>

      <div className="flex shrink-0 items-center gap-2">
        {saveState.kind === "saved" && !dirty && (
          <span className="text-xs font-medium text-success">Saved</span>
        )}
        <button
          type="button"
          onClick={onDiscard}
          disabled={!dirty || saving || publishing}
          className="rounded-lg border border-hairline bg-white px-3 py-2 text-sm font-medium text-ink transition-colors hover:bg-panel disabled:cursor-not-allowed disabled:opacity-50"
        >
          Discard changes
        </button>
        <button
          type="button"
          onClick={onSave}
          disabled={saving || publishing}
          className="inline-flex items-center justify-center rounded-lg border border-hairline bg-white px-4 py-2 text-sm font-medium text-ink shadow-sm transition-colors hover:bg-panel disabled:cursor-not-allowed disabled:opacity-60"
        >
          {saving ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          onClick={onPublish}
          disabled={publishDisabled}
          title={
            dirty
              ? "Save your changes before publishing"
              : canPublishStatus
                ? undefined
                : "This survey is already published"
          }
          className="inline-flex items-center justify-center rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-brand-hover disabled:cursor-not-allowed disabled:opacity-60"
        >
          {publishing ? "Publishing…" : "Publish"}
        </button>
      </div>
    </div>
  );
}

/**
 * Build · Logic tab switcher (segmented control). Drives the body between the
 * existing authoring panes and the new Logic view. The toolbar actions
 * (Back / Save / Publish / Discard / status pill) are shared across both tabs.
 */
function TabSwitcher({
  tab,
  onTabChange,
}: {
  tab: BuilderTab;
  onTabChange: (tab: BuilderTab) => void;
}) {
  const tabs: Array<{ id: BuilderTab; label: string }> = [
    { id: "build", label: "Build" },
    { id: "endings", label: "Endings" },
    { id: "style", label: "Style" },
    { id: "settings", label: "Settings" },
    { id: "followups", label: "Follow-ups" },
    { id: "blocks", label: "Blocks" },
    { id: "logic", label: "Logic" },
  ];
  return (
    <div
      role="tablist"
      aria-label="Builder view"
      className="inline-flex items-center gap-1 rounded-lg bg-panel p-1"
    >
      {tabs.map((t) => {
        const active = tab === t.id;
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onTabChange(t.id)}
            className={
              "rounded-md px-4 py-1.5 text-sm font-medium transition-colors " +
              (active ? "bg-white text-brand shadow-sm" : "text-muted hover:text-ink")
            }
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

/** A small status badge (Draft / Published / Paused / Closed) using brand tokens. */
function StatusPill({ status }: { status: SurveyStatus }) {
  const styles: Record<SurveyStatus, string> = {
    draft: "bg-panel text-muted",
    published: "bg-success/15 text-success",
    paused: "bg-secondary/10 text-secondary",
    closed: "bg-ink/10 text-ink",
  };
  const label: Record<SurveyStatus, string> = {
    draft: "Draft",
    published: "Published",
    paused: "Paused",
    closed: "Closed",
  };
  return (
    <span
      data-testid="status-pill"
      className={
        "inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide " +
        styles[status]
      }
    >
      {label[status]}
    </span>
  );
}

function ErrorBanner({ title, messages }: { title: string; messages: string[] }) {
  return (
    <div role="alert" className="border-b border-rose-200 bg-rose-50 px-4 py-3">
      <p className="text-sm font-semibold text-rose-800">{title}</p>
      <ul className="mt-1 list-inside list-disc space-y-0.5 text-sm text-rose-700">
        {messages.map((m, i) => (
          <li key={i}>{m}</li>
        ))}
      </ul>
    </div>
  );
}

function Section({ children }: { children: React.ReactNode }) {
  return <section>{children}</section>;
}
