// ============================================================================
// onForm — Builder per-question settings editor.
//
// Edits the selected question's COMMON fields (headline / subheader / required)
// plus its per-type config, writing back schema-valid shapes via the surveyModel
// helpers. i18n editing scope: every text field edits the CURRENT authoring
// language (`lang`, threaded from the builder's language switcher). When `lang`
// is the survey default it writes the i18nString `default` key (single-language
// flow unchanged); for a non-default language it writes that lang key while
// PRESERVING `default` + the other languages' keys. The author sees the raw text
// for the current language (blank = untranslated → runtime falls back to default).
//
// DEFERRED (noted in the island summary): the per-question logic editor (jump/
// skip rules), shuffle ordering.
// ============================================================================
import { useId, useRef, useState } from "react";

import { resolveI18n } from "../../lib/i18n";
import type {
  AddressConfig,
  AddressFieldKey,
  Choice,
  ContactFieldKey,
  ContactInfoConfig,
  CtaConfig,
  DateConfig,
  FileUploadConfig,
  MatrixConfig,
  MultiSelectConfig,
  NumberConfig,
  OpenTextConfig,
  PictureChoiceConfig,
  Question,
  RatingConfig,
  ScheduleMeetingConfig,
  SingleSelectConfig,
  Survey,
} from "../../lib/types";
import {
  addChoice,
  addNamedChoice,
  addPictureOption,
  deleteChoice,
  deleteNamedChoice,
  deletePictureOption,
  getLangText,
  patchConfig,
  setChoiceIsOther,
  setContactField,
  setLangText,
  setOptionalLangText,
  updateChoiceLabel,
  updateNamedChoiceLabel,
  updatePictureOptionImage,
  updatePictureOptionLabel,
} from "./surveyModel";
import { RichTextEditor, type RichTextEditorApi } from "./RichTextEditor";

/** A recall source the author can insert into a headline/subheader: an earlier question, a
 *  hidden field, or a survey variable (all share the recall id namespace). */
export interface RecallSource {
  id: string;
  label: string;
  kind: "question" | "hidden" | "variable";
}

/**
 * Build the recall-insert list for `question` within `survey`: only EARLIER questions
 * (index < the host question's index — a question can't recall a later/unanswered one
 * except via the token's fallback) PLUS all survey-level hidden fields. The label is the
 * question's resolved headline (default lang) or the hidden field's name/id.
 */
export function recallSourcesFor(
  survey: Survey | undefined,
  question: Question,
  defaultLang: string,
): RecallSource[] {
  if (!survey) return [];
  const idx = survey.questions.findIndex((q) => q.id === question.id);
  const sources: RecallSource[] = [];
  const earlier = idx >= 0 ? survey.questions.slice(0, idx) : [];
  for (const q of earlier) {
    const headline = resolveI18n(q.headline, defaultLang, defaultLang);
    sources.push({ id: q.id, kind: "question", label: headline.trim() || q.id });
  }
  for (const hf of survey.hiddenFields ?? []) {
    sources.push({ id: hf.id, kind: "hidden", label: hf.name?.trim() || hf.id });
  }
  // Survey variables are recall sources too (their live value is read by the same #recall:<id>#
  // token mechanism — the runtime threads variable values into the recall lookup map).
  for (const v of survey.variables ?? []) {
    sources.push({ id: v.id, kind: "variable", label: v.name?.trim() || v.id });
  }
  return sources;
}

/** The recall token string for a given source id (FROZEN format; see lib/recall.ts). */
export function recallToken(id: string, fallback = ""): string {
  return `#recall:${id}/fallback:${fallback}#`;
}

export function QuestionEditor({
  question,
  onChange,
  lang = "default",
  defaultLang = "default",
  survey,
}: {
  question: Question;
  onChange: (q: Question) => void;
  /** The active authoring language (writes its i18nString key). Defaults to `default`. */
  lang?: string;
  /** The survey's default-language code (pairs with the i18nString `default` key). */
  defaultLang?: string;
  /** The full survey — used to list earlier questions + hidden fields for recall insert. */
  survey?: Survey;
}) {
  const recallSources = recallSourcesFor(survey, question, defaultLang);
  return (
    <div className="space-y-5">
      <CommonFields
        question={question}
        onChange={onChange}
        lang={lang}
        defaultLang={defaultLang}
        recallSources={recallSources}
      />
      <div className="border-t border-hairline pt-4">
        <TypeConfig question={question} onChange={onChange} lang={lang} defaultLang={defaultLang} />
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Common fields                                                              //
// --------------------------------------------------------------------------- //
function CommonFields({
  question,
  onChange,
  lang,
  defaultLang,
  recallSources = [],
}: EditorProps & { recallSources?: RecallSource[] }) {
  const headlineText = getLangText(question.headline, lang, defaultLang);
  // The DESCRIPTION is now rich text stored in `descriptionHtml`. For backward
  // compatibility, a legacy plain `subheader` (with no descriptionHtml yet) is
  // surfaced as the editor's initial content (HTML-escaped so its `<`/`&` are
  // literal text); the FIRST edit migrates it into descriptionHtml and clears
  // the same language's subheader so the runtime never double-renders.
  const descriptionHtml = getLangText(question.descriptionHtml, lang, defaultLang);
  const legacySubheader = getLangText(question.subheader, lang, defaultLang);
  const editorValue = descriptionHtml || escapeHtml(legacySubheader);

  // Imperative handle to the rich-text editor for caret-position recall insert.
  const editorApi = useRef<RichTextEditorApi | null>(null);

  function insertIntoHeadline(token: string) {
    const next = appendToken(headlineText, token);
    onChange({ ...question, headline: setLangText(question.headline, lang, next, defaultLang) });
  }

  /** Persist edited description HTML; clears this language's legacy subheader on migrate. */
  function setDescription(html: string) {
    const next: Question = {
      ...question,
      descriptionHtml: setOptionalLangText(question.descriptionHtml, lang, html, defaultLang),
    };
    // Once a description exists for this language, drop the legacy plain subheader
    // for the SAME language so it can't shadow/double-render (other languages kept).
    if (html !== "" && legacySubheader !== "") {
      next.subheader = setOptionalLangText(question.subheader, lang, "", defaultLang);
    }
    onChange(next);
  }

  function insertDescriptionToken(token: string) {
    if (editorApi.current) {
      editorApi.current.insertText(token);
    } else {
      // Fallback if the editor handle isn't ready: append to current HTML.
      const sep = editorValue.trim() === "" ? "" : " ";
      setDescription(`${editorValue}${sep}${token}`);
    }
  }

  return (
    <div className="space-y-3">
      <Field
        label="Headline"
        action={<RecallInsert sources={recallSources} onInsert={insertIntoHeadline} />}
      >
        <TextInput
          value={headlineText}
          onChange={(v) =>
            onChange({ ...question, headline: setLangText(question.headline, lang, v, defaultLang) })
          }
          placeholder="Your question"
        />
      </Field>
      <Field
        label="Description"
        hint="optional · rich text"
        action={<RecallInsert sources={recallSources} onInsert={insertDescriptionToken} />}
      >
        <RichTextEditor
          value={editorValue}
          onChange={setDescription}
          placeholder="Add a description"
          onReady={(api) => {
            editorApi.current = api;
          }}
        />
      </Field>
      <Checkbox
        label="Required"
        checked={question.required === true}
        onChange={(checked) => onChange({ ...question, required: checked })}
      />
    </div>
  );
}

/** Escape plain text so legacy subheaders surface as literal text in the rich-text editor. */
function escapeHtml(text: string): string {
  if (text === "") return "";
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/** Append a recall token to existing text (a space is inserted when the text is non-empty). */
function appendToken(current: string, token: string): string {
  if (current.trim() === "") return token;
  return current.endsWith(" ") ? `${current}${token}` : `${current} ${token}`;
}

/**
 * The "@ insert recall" control: a small button that opens a dropdown of recall sources
 * (earlier questions + hidden fields). Choosing one inserts the FROZEN recall token into
 * the active text field (current language). Hidden when there are no sources (e.g. the
 * first question with no hidden fields) so the single-question flow is unchanged.
 */
function RecallInsert({
  sources,
  onInsert,
}: {
  sources: RecallSource[];
  onInsert: (token: string) => void;
}) {
  const [open, setOpen] = useState(false);
  if (sources.length === 0) return null;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title="Insert a recalled answer or hidden field"
        className="rounded px-1.5 py-0.5 text-xs font-semibold text-brand hover:bg-brand/5"
      >
        @ Recall
      </button>
      {open && (
        <ul
          role="listbox"
          aria-label="Insert recall"
          className="absolute right-0 z-10 mt-1 max-h-56 w-60 overflow-auto rounded-lg border border-hairline bg-white py-1 shadow-lg"
        >
          {sources.map((s) => (
            <li key={`${s.kind}:${s.id}`}>
              <button
                type="button"
                role="option"
                aria-selected={false}
                onClick={() => {
                  onInsert(recallToken(s.id));
                  setOpen(false);
                }}
                className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-sm text-ink hover:bg-panel"
              >
                <span className="min-w-0 flex-1 truncate">{s.label}</span>
                <span
                  className={
                    "shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium uppercase " +
                    (s.kind === "hidden" ? "bg-secondary/10 text-secondary" : "bg-panel text-muted")
                  }
                >
                  {s.kind === "hidden" ? "hidden" : "answer"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Per-type config dispatch                                                   //
// --------------------------------------------------------------------------- //
function TypeConfig({ question, onChange, lang, defaultLang }: EditorProps) {
  const p = { question, onChange, lang, defaultLang };
  switch (question.type) {
    case "openText":
      return <OpenTextEditor {...p} />;
    case "singleSelect":
      return <SingleSelectEditor {...p} />;
    case "multiSelect":
      return <MultiSelectEditor {...p} />;
    case "pictureChoice":
      return <PictureChoiceEditor {...p} />;
    case "rating":
      return <RatingEditor {...p} />;
    case "nps":
      return <NpsEditor {...p} />;
    case "cta":
      return <CtaEditor {...p} />;
    case "consent":
      return <ConsentEditor {...p} />;
    case "date":
      return <DateEditor {...p} />;
    case "number":
      return <NumberEditor {...p} />;
    case "ranking":
      return <RankingEditor {...p} />;
    case "matrix":
      return <MatrixEditor {...p} />;
    case "contactInfo":
      return <ContactInfoEditor {...p} />;
    case "address":
      return <AddressEditor {...p} />;
    case "scheduleMeeting":
      return <ScheduleMeetingEditor {...p} />;
    case "fileUpload":
      return <FileUploadEditor {...p} />;
    default:
      return (
        <p className="rounded-lg border border-dashed border-hairline bg-panel px-3 py-2 text-sm text-muted">
          Settings for <code>{question.type}</code> are not available in this builder yet.
        </p>
      );
  }
}

// --- openText --- //
function OpenTextEditor({ question, onChange, lang, defaultLang }: EditorProps) {
  const cfg = (question.config ?? {}) as OpenTextConfig;
  return (
    <div className="space-y-3">
      <Field label="Input type">
        <Select
          value={cfg.inputType ?? "short"}
          options={[
            { value: "short", label: "Short (single line)" },
            { value: "long", label: "Long (paragraph)" },
          ]}
          onChange={(v) => onChange(patchConfig(question, { inputType: v }))}
        />
      </Field>
      <Field label="Placeholder" hint="optional">
        <TextInput
          value={getLangText(cfg.placeholder, lang, defaultLang)}
          onChange={(v) =>
            onChange(
              patchConfig(question, {
                placeholder: setOptionalLangText(cfg.placeholder, lang, v, defaultLang),
              }),
            )
          }
          placeholder="Type your answer…"
        />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Min length" hint="optional">
          <NumberInput
            value={cfg.minLength}
            min={0}
            onChange={(n) => onChange(patchConfig(question, { minLength: n }))}
          />
        </Field>
        <Field label="Max length" hint="optional">
          <NumberInput
            value={cfg.maxLength}
            min={1}
            onChange={(n) => onChange(patchConfig(question, { maxLength: n }))}
          />
        </Field>
      </div>
    </div>
  );
}

// --- choice editor (shared by single/multi) --- //
function ChoiceEditor({
  question,
  onChange,
  allowOther,
  lang,
  defaultLang,
}: EditorProps & { allowOther: boolean }) {
  const cfg = (question.config ?? {}) as { choices?: Choice[] };
  const choices = cfg.choices ?? [];
  const atMin = choices.length <= 2; // schema minItems: 2

  return (
    <Field label="Choices" hint="at least 2">
      <div className="space-y-2">
        {choices.map((c) => (
          <div key={c.id} className="flex items-center gap-2">
            <TextInput
              value={getLangText(c.label, lang, defaultLang)}
              onChange={(v) => onChange(updateChoiceLabel(question, c.id, v, lang, defaultLang))}
              placeholder="Option label"
            />
            {allowOther && (
              <label className="flex shrink-0 items-center gap-1 text-xs text-muted">
                <input
                  type="checkbox"
                  checked={c.isOther === true}
                  onChange={(e) => onChange(setChoiceIsOther(question, c.id, e.target.checked))}
                  className="h-3.5 w-3.5 accent-brand"
                />
                Other
              </label>
            )}
            <button
              type="button"
              onClick={() => onChange(deleteChoice(question, c.id))}
              disabled={atMin}
              aria-label="Remove choice"
              title={atMin ? "At least 2 choices are required" : "Remove choice"}
              className="shrink-0 rounded p-1 text-muted hover:bg-rose-50 hover:text-rose-600 disabled:opacity-30"
            >
              ✕
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => onChange(addChoice(question))}
          className="rounded-md border border-dashed border-hairline px-3 py-1.5 text-sm font-medium text-brand hover:border-brand hover:bg-brand/5"
        >
          + Add choice
        </button>
      </div>
    </Field>
  );
}

// --- singleSelect --- //
function SingleSelectEditor({ question, onChange, lang, defaultLang }: EditorProps) {
  const cfg = (question.config ?? {}) as SingleSelectConfig;
  return (
    <div className="space-y-3">
      <ChoiceEditor question={question} onChange={onChange} lang={lang} defaultLang={defaultLang} allowOther />
      <Field label="Display">
        <Select
          value={cfg.display ?? "list"}
          options={[
            { value: "list", label: "List (radio buttons)" },
            { value: "dropdown", label: "Dropdown" },
          ]}
          onChange={(v) => onChange(patchConfig(question, { display: v }))}
        />
      </Field>
    </div>
  );
}

// --- multiSelect --- //
function MultiSelectEditor({ question, onChange, lang, defaultLang }: EditorProps) {
  const cfg = (question.config ?? {}) as MultiSelectConfig;
  return (
    <div className="space-y-3">
      <ChoiceEditor question={question} onChange={onChange} lang={lang} defaultLang={defaultLang} allowOther={false} />
      <div className="grid grid-cols-2 gap-3">
        <Field label="Min selections" hint="optional">
          <NumberInput
            value={cfg.minSelections}
            min={0}
            onChange={(n) => onChange(patchConfig(question, { minSelections: n }))}
          />
        </Field>
        <Field label="Max selections" hint="optional">
          <NumberInput
            value={cfg.maxSelections}
            min={1}
            onChange={(n) => onChange(patchConfig(question, { maxSelections: n }))}
          />
        </Field>
      </div>
    </div>
  );
}

// --- pictureChoice --- //
function PictureChoiceEditor({ question, onChange, lang, defaultLang }: EditorProps) {
  const cfg = (question.config ?? {}) as PictureChoiceConfig;
  const options = cfg.options ?? [];
  const atMin = options.length <= 2; // schema minItems: 2
  return (
    <div className="space-y-3">
      <Checkbox
        label="Allow multiple selections"
        checked={cfg.allowMulti === true}
        onChange={(checked) => onChange(patchConfig(question, { allowMulti: checked }))}
      />
      <Field label="Images" hint="at least 2, each needs an image URL">
        <div className="space-y-3">
          {options.map((o) => (
            <div key={o.id} className="flex items-start gap-2 rounded-lg border border-hairline bg-white p-2">
              {o.imageUrl ? (
                // eslint-disable-next-line jsx-a11y/img-redundant-alt
                <img
                  src={o.imageUrl}
                  alt="preview"
                  className="h-12 w-12 shrink-0 rounded object-cover"
                />
              ) : (
                <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded bg-panel text-base text-muted">
                  🖼
                </span>
              )}
              <div className="min-w-0 flex-1 space-y-1.5">
                <TextInput
                  value={o.imageUrl}
                  onChange={(v) => onChange(updatePictureOptionImage(question, o.id, v))}
                  placeholder="https://…/image.png"
                />
                <TextInput
                  value={getLangText(o.label, lang, defaultLang)}
                  onChange={(v) =>
                    onChange(updatePictureOptionLabel(question, o.id, v, lang, defaultLang))
                  }
                  placeholder="Label (optional)"
                />
              </div>
              <button
                type="button"
                onClick={() => onChange(deletePictureOption(question, o.id))}
                disabled={atMin}
                aria-label="Remove image"
                title={atMin ? "At least 2 images are required" : "Remove image"}
                className="shrink-0 rounded p-1 text-muted hover:bg-rose-50 hover:text-rose-600 disabled:opacity-30"
              >
                ✕
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={() => onChange(addPictureOption(question))}
            className="rounded-md border border-dashed border-hairline px-3 py-1.5 text-sm font-medium text-brand hover:border-brand hover:bg-brand/5"
          >
            + Add image
          </button>
        </div>
      </Field>
    </div>
  );
}

// --- rating --- //
function RatingEditor({ question, onChange, lang, defaultLang }: EditorProps) {
  const cfg = (question.config ?? {}) as RatingConfig;
  const range = cfg.range ?? 5;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Scale">
          <Select
            value={cfg.scale ?? "star"}
            options={[
              { value: "star", label: "Stars" },
              { value: "number", label: "Numbers" },
              { value: "smiley", label: "Smileys" },
            ]}
            onChange={(v) => onChange(patchConfig(question, { scale: v }))}
          />
        </Field>
        <Field label="Steps" hint="3–10">
          <NumberInput
            value={range}
            min={3}
            max={10}
            onChange={(n) =>
              onChange(patchConfig(question, { range: clamp(n ?? 5, 3, 10) }))
            }
          />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Lower label" hint="optional">
          <TextInput
            value={getLangText(cfg.lowerLabel, lang, defaultLang)}
            onChange={(v) =>
              onChange(
                patchConfig(question, {
                  lowerLabel: setOptionalLangText(cfg.lowerLabel, lang, v, defaultLang),
                }),
              )
            }
            placeholder="e.g. Poor"
          />
        </Field>
        <Field label="Upper label" hint="optional">
          <TextInput
            value={getLangText(cfg.upperLabel, lang, defaultLang)}
            onChange={(v) =>
              onChange(
                patchConfig(question, {
                  upperLabel: setOptionalLangText(cfg.upperLabel, lang, v, defaultLang),
                }),
              )
            }
            placeholder="e.g. Excellent"
          />
        </Field>
      </div>
    </div>
  );
}

// --- nps --- //
function NpsEditor({ question, onChange, lang, defaultLang }: EditorProps) {
  const cfg = (question.config ?? {}) as { lowerLabel?: unknown; upperLabel?: unknown };
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted">Fixed 0–10 scale (11 buttons).</p>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Lower label" hint="optional">
          <TextInput
            value={getLangText(cfg.lowerLabel as never, lang, defaultLang)}
            onChange={(v) =>
              onChange(
                patchConfig(question, {
                  lowerLabel: setOptionalLangText(cfg.lowerLabel as never, lang, v, defaultLang),
                }),
              )
            }
            placeholder="Not at all likely"
          />
        </Field>
        <Field label="Upper label" hint="optional">
          <TextInput
            value={getLangText(cfg.upperLabel as never, lang, defaultLang)}
            onChange={(v) =>
              onChange(
                patchConfig(question, {
                  upperLabel: setOptionalLangText(cfg.upperLabel as never, lang, v, defaultLang),
                }),
              )
            }
            placeholder="Extremely likely"
          />
        </Field>
      </div>
    </div>
  );
}

// --- cta --- //
function CtaEditor({ question, onChange, lang, defaultLang }: EditorProps) {
  const cfg = (question.config ?? {}) as CtaConfig;
  return (
    <div className="space-y-3">
      <Field label="Body (HTML)" hint="optional">
        <TextArea
          value={getLangText(cfg.html, lang, defaultLang)}
          onChange={(v) =>
            onChange(
              patchConfig(question, { html: setOptionalLangText(cfg.html, lang, v, defaultLang) }),
            )
          }
          placeholder="<p>Thanks for your time!</p>"
        />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Button label" hint="optional">
          <TextInput
            value={getLangText(cfg.buttonLabel, lang, defaultLang)}
            onChange={(v) =>
              onChange(
                patchConfig(question, {
                  buttonLabel: setOptionalLangText(cfg.buttonLabel, lang, v, defaultLang),
                }),
              )
            }
            placeholder="Continue"
          />
        </Field>
        <Field label="Button URL" hint="optional">
          <TextInput
            value={cfg.buttonUrl ?? ""}
            onChange={(v) => onChange(patchConfig(question, { buttonUrl: v.trim() === "" ? undefined : v }))}
            placeholder="https://…"
          />
        </Field>
      </div>
    </div>
  );
}

// --- consent --- //
function ConsentEditor({ question, onChange, lang, defaultLang }: EditorProps) {
  const cfg = (question.config ?? {}) as { label?: unknown; html?: unknown };
  return (
    <div className="space-y-3">
      <Field label="Checkbox label">
        <TextInput
          value={getLangText(cfg.label as never, lang, defaultLang)}
          onChange={(v) =>
            onChange(patchConfig(question, { label: setLangText(cfg.label as never, lang, v, defaultLang) }))
          }
          placeholder="I agree to the terms"
        />
      </Field>
      <Field label="Consent text (HTML)" hint="optional">
        <TextArea
          value={getLangText(cfg.html as never, lang, defaultLang)}
          onChange={(v) =>
            onChange(
              patchConfig(question, { html: setOptionalLangText(cfg.html as never, lang, v, defaultLang) }),
            )
          }
          placeholder="<p>By checking this box…</p>"
        />
      </Field>
    </div>
  );
}

// --- date --- //
function DateEditor({ question, onChange }: EditorProps) {
  const cfg = (question.config ?? {}) as DateConfig;
  return (
    <div className="space-y-3">
      <Field label="Display format" hint="picker stores ISO YYYY-MM-DD">
        <Select
          value={cfg.format ?? "YYYY-MM-DD"}
          options={[
            { value: "YYYY-MM-DD", label: "YYYY-MM-DD" },
            { value: "DD-MM-YYYY", label: "DD-MM-YYYY" },
            { value: "MM-DD-YYYY", label: "MM-DD-YYYY" },
          ]}
          onChange={(v) => onChange(patchConfig(question, { format: v }))}
        />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Earliest date" hint="optional">
          <DateInput
            value={cfg.min}
            onChange={(v) => onChange(patchConfig(question, { min: v }))}
          />
        </Field>
        <Field label="Latest date" hint="optional">
          <DateInput
            value={cfg.max}
            onChange={(v) => onChange(patchConfig(question, { max: v }))}
          />
        </Field>
      </div>
    </div>
  );
}

// --- number --- //
function NumberEditor({ question, onChange, lang, defaultLang }: EditorProps) {
  const cfg = (question.config ?? {}) as NumberConfig;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-3">
        <Field label="Min" hint="optional">
          <NumberInput
            value={cfg.min}
            onChange={(n) => onChange(patchConfig(question, { min: n }))}
          />
        </Field>
        <Field label="Max" hint="optional">
          <NumberInput
            value={cfg.max}
            onChange={(n) => onChange(patchConfig(question, { max: n }))}
          />
        </Field>
        <Field label="Step" hint="optional">
          <NumberInput
            value={cfg.step}
            min={0}
            onChange={(n) => onChange(patchConfig(question, { step: n }))}
          />
        </Field>
      </div>
      <Field label="Placeholder" hint="optional">
        <TextInput
          value={getLangText(cfg.placeholder, lang, defaultLang)}
          onChange={(v) =>
            onChange(
              patchConfig(question, {
                placeholder: setOptionalLangText(cfg.placeholder, lang, v, defaultLang),
              }),
            )
          }
          placeholder="e.g. 0"
        />
      </Field>
    </div>
  );
}

// --- ranking (reuses the shared ChoiceEditor on config.choices) --- //
function RankingEditor({ question, onChange, lang, defaultLang }: EditorProps) {
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted">Respondents drag/reorder these into their preferred order.</p>
      <ChoiceEditor question={question} onChange={onChange} lang={lang} defaultLang={defaultLang} allowOther={false} />
    </div>
  );
}

// --- matrix --- //
function MatrixEditor({ question, onChange, lang, defaultLang }: EditorProps) {
  const cfg = (question.config ?? {}) as MatrixConfig;
  return (
    <div className="space-y-3">
      <Field label="Selection">
        <Select
          value={cfg.selection ?? "single"}
          options={[
            { value: "single", label: "One column per row (radio)" },
            { value: "multiple", label: "Many columns per row (checkbox)" },
          ]}
          onChange={(v) => onChange(patchConfig(question, { selection: v }))}
        />
      </Field>
      <NamedChoiceListEditor
        question={question}
        onChange={onChange}
        lang={lang}
        defaultLang={defaultLang}
        listKey="rows"
        label="Rows"
        hint="at least 1"
        minItems={1}
        addLabel="Row"
      />
      <NamedChoiceListEditor
        question={question}
        onChange={onChange}
        lang={lang}
        defaultLang={defaultLang}
        listKey="columns"
        label="Columns"
        hint="at least 2"
        minItems={2}
        addLabel="Column"
      />
    </div>
  );
}

// --- contactInfo --- //
const CONTACT_FIELD_META: Array<{ key: ContactFieldKey; label: string }> = [
  { key: "firstName", label: "First name" },
  { key: "lastName", label: "Last name" },
  { key: "email", label: "Email" },
  { key: "phone", label: "Phone" },
  { key: "company", label: "Company" },
];

function ContactInfoEditor({ question, onChange }: EditorProps) {
  const cfg = (question.config ?? {}) as ContactInfoConfig;
  const fields = cfg.fields ?? {};
  const shownCount = CONTACT_FIELD_META.filter(
    (f) => fields[f.key] && fields[f.key]!.show !== false,
  ).length;

  return (
    <Field label="Fields" hint="at least 1 shown">
      <div className="space-y-2">
        {CONTACT_FIELD_META.map(({ key, label }) => {
          const sub = fields[key];
          const shown = !!sub && sub.show !== false;
          const isLastShown = shown && shownCount <= 1;
          return (
            <div
              key={key}
              className="flex items-center justify-between gap-3 rounded-lg border border-hairline bg-white px-3 py-2"
            >
              <label className="flex items-center gap-2 text-sm text-ink">
                <input
                  type="checkbox"
                  checked={shown}
                  disabled={isLastShown}
                  title={isLastShown ? "At least one field must be shown" : undefined}
                  onChange={(e) =>
                    onChange(
                      setContactField(question, key, e.target.checked ? { show: true } : null),
                    )
                  }
                  className="h-4 w-4 accent-brand"
                />
                {label}
              </label>
              {shown && (
                <label className="flex shrink-0 items-center gap-1 text-xs text-muted">
                  <input
                    type="checkbox"
                    checked={sub?.required === true}
                    onChange={(e) =>
                      onChange(setContactField(question, key, { required: e.target.checked }))
                    }
                    className="h-3.5 w-3.5 accent-brand"
                  />
                  Required
                </label>
              )}
            </div>
          );
        })}
      </div>
    </Field>
  );
}

// --- address --- //
const ADDRESS_FIELD_META: Array<{ key: AddressFieldKey; label: string }> = [
  { key: "addressLine1", label: "Address line 1" },
  { key: "addressLine2", label: "Address line 2" },
  { key: "city", label: "City" },
  { key: "state", label: "State / Region" },
  { key: "zip", label: "ZIP / Postal code" },
  { key: "country", label: "Country" },
];

function AddressEditor({ question, onChange }: EditorProps) {
  const cfg = (question.config ?? {}) as AddressConfig;
  const fields = cfg.fields ?? {};
  const shownCount = ADDRESS_FIELD_META.filter(
    (f) => fields[f.key] && fields[f.key]!.show !== false,
  ).length;

  return (
    <Field label="Fields" hint="at least 1 shown">
      <div className="space-y-2">
        {ADDRESS_FIELD_META.map(({ key, label }) => {
          const sub = fields[key];
          const shown = !!sub && sub.show !== false;
          const isLastShown = shown && shownCount <= 1;
          return (
            <div
              key={key}
              className="flex items-center justify-between gap-3 rounded-lg border border-hairline bg-white px-3 py-2"
            >
              <label className="flex items-center gap-2 text-sm text-ink">
                <input
                  type="checkbox"
                  checked={shown}
                  disabled={isLastShown}
                  title={isLastShown ? "At least one field must be shown" : undefined}
                  onChange={(e) =>
                    onChange(
                      setContactField(question, key, e.target.checked ? { show: true } : null),
                    )
                  }
                  className="h-4 w-4 accent-brand"
                />
                {label}
              </label>
              {shown && (
                <label className="flex shrink-0 items-center gap-1 text-xs text-muted">
                  <input
                    type="checkbox"
                    checked={sub?.required === true}
                    onChange={(e) =>
                      onChange(setContactField(question, key, { required: e.target.checked }))
                    }
                    className="h-3.5 w-3.5 accent-brand"
                  />
                  Required
                </label>
              )}
            </div>
          );
        })}
      </div>
    </Field>
  );
}

// --- scheduleMeeting --- //
function ScheduleMeetingEditor({ question, onChange }: EditorProps) {
  const cfg = (question.config ?? {}) as ScheduleMeetingConfig;
  return (
    <div className="space-y-3">
      <Field label="Cal.com link" hint="username or username/event-type">
        <TextInput
          value={cfg.calLink ?? ""}
          onChange={(v) => onChange(patchConfig(question, { calLink: v.trim() }))}
          placeholder="jane or jane/30min"
        />
      </Field>
      <p className="rounded-lg border border-dashed border-hairline bg-panel px-3 py-2 text-xs text-muted">
        Respondents see the cal.com scheduling widget inline. The booking is recorded once the
        respondent confirms a time. Use the path part only — the runtime prepends{" "}
        <code>https://cal.com/</code>.
      </p>
    </div>
  );
}

// --- fileUpload --- //
function FileUploadEditor({ question, onChange }: EditorProps) {
  const cfg = (question.config ?? {}) as FileUploadConfig;
  const allowMultiple = cfg.allowMultiple === true;
  const extText = (cfg.allowedExtensions ?? []).join(", ");
  return (
    <div className="space-y-3">
      <p className="rounded-lg border border-dashed border-hairline bg-panel px-3 py-2 text-xs text-muted">
        Uploads aren’t wired to storage yet — the field renders in preview, but files aren’t
        submitted. Settings below are saved for when the upload backend lands.
      </p>
      <Checkbox
        label="Allow multiple files"
        checked={allowMultiple}
        onChange={(checked) => onChange(patchConfig(question, { allowMultiple: checked }))}
      />
      <div className="grid grid-cols-2 gap-3">
        <Field label="Max files" hint="when multiple">
          <NumberInput
            value={cfg.maxFiles}
            min={1}
            onChange={(n) => onChange(patchConfig(question, { maxFiles: n }))}
          />
        </Field>
        <Field label="Max size (MB)" hint="1–100">
          <NumberInput
            value={cfg.maxSizeMb}
            min={1}
            max={100}
            onChange={(n) => onChange(patchConfig(question, { maxSizeMb: n }))}
          />
        </Field>
      </div>
      <Field label="Allowed extensions" hint="comma-separated, e.g. .pdf, .png">
        <TextInput
          value={extText}
          onChange={(v) => {
            const list = v
              .split(",")
              .map((s) => s.trim())
              .filter((s) => s !== "");
            onChange(patchConfig(question, { allowedExtensions: list.length > 0 ? list : undefined }));
          }}
          placeholder=".pdf, .png"
        />
      </Field>
    </div>
  );
}

// --- named choice-list editor (matrix rows/columns) --- //
function NamedChoiceListEditor({
  question,
  onChange,
  lang,
  defaultLang,
  listKey,
  label,
  hint,
  minItems,
  addLabel,
}: EditorProps & {
  listKey: "rows" | "columns";
  label: string;
  hint: string;
  minItems: number;
  addLabel: string;
}) {
  const config = (question.config ?? {}) as Record<string, Choice[] | undefined>;
  const list = Array.isArray(config[listKey]) ? (config[listKey] as Choice[]) : [];
  const atMin = list.length <= minItems;
  return (
    <Field label={label} hint={hint}>
      <div className="space-y-2">
        {list.map((c) => (
          <div key={c.id} className="flex items-center gap-2">
            <TextInput
              value={getLangText(c.label, lang, defaultLang)}
              onChange={(v) =>
                onChange(updateNamedChoiceLabel(question, listKey, c.id, v, lang, defaultLang))
              }
              placeholder={`${addLabel} label`}
            />
            <button
              type="button"
              onClick={() => onChange(deleteNamedChoice(question, listKey, c.id))}
              disabled={atMin}
              aria-label={`Remove ${addLabel.toLowerCase()}`}
              title={atMin ? `At least ${minItems} required` : `Remove ${addLabel.toLowerCase()}`}
              className="shrink-0 rounded p-1 text-muted hover:bg-rose-50 hover:text-rose-600 disabled:opacity-30"
            >
              ✕
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => onChange(addNamedChoice(question, listKey, `New ${addLabel.toLowerCase()}`))}
          className="rounded-md border border-dashed border-hairline px-3 py-1.5 text-sm font-medium text-brand hover:border-brand hover:bg-brand/5"
        >
          + Add {addLabel.toLowerCase()}
        </button>
      </div>
    </Field>
  );
}

// --------------------------------------------------------------------------- //
//  Small form primitives (match the admin Tailwind aesthetic)                 //
// --------------------------------------------------------------------------- //
interface EditorProps {
  question: Question;
  onChange: (q: Question) => void;
  /** Active authoring language; `default` writes the i18nString `default` key. */
  lang: string;
  /** Survey default-language code (pairs with the `default` key). */
  defaultLang: string;
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

function Field({
  label,
  hint,
  action,
  children,
}: {
  label: string;
  hint?: string;
  /** Optional right-aligned control on the label row (e.g. the recall-insert button). */
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  // When there's an `action` we must NOT wrap it in the <label>: a nested interactive
  // control would absorb the label's text into its accessible name (e.g. "Headline …").
  // So we render the caption + action in a plain row and the input in its own <label>.
  if (action) {
    return (
      <div className="block">
        <div className="mb-1 flex items-center justify-between gap-2 text-xs font-medium text-muted">
          <span className="flex items-center gap-2">
            {label}
            {hint && <span className="font-normal text-muted/70">({hint})</span>}
          </span>
          {action}
        </div>
        <label className="block">{children}</label>
      </div>
    );
  }
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

const inputClass =
  "w-full rounded-lg border border-hairline bg-white px-3 py-2 text-sm text-ink " +
  "shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand";

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

function NumberInput({
  value,
  onChange,
  min,
  max,
}: {
  value: number | undefined;
  onChange: (n: number | undefined) => void;
  min?: number;
  max?: number;
}) {
  return (
    <input
      type="number"
      value={value ?? ""}
      min={min}
      max={max}
      onChange={(e) => {
        const raw = e.target.value;
        onChange(raw === "" ? undefined : Number(raw));
      }}
      className={inputClass}
    />
  );
}

function DateInput({
  value,
  onChange,
}: {
  value: string | undefined;
  onChange: (v: string | undefined) => void;
}) {
  return (
    <input
      type="date"
      value={value ?? ""}
      onChange={(e) => {
        const raw = e.target.value;
        onChange(raw === "" ? undefined : raw);
      }}
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

function Checkbox({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  const id = useId();
  return (
    <label htmlFor={id} className="flex cursor-pointer items-center gap-2 text-sm text-ink">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 accent-brand"
      />
      {label}
    </label>
  );
}
