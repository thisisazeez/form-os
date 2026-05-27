// ============================================================================
// OpenSurvey — question-renderer dispatcher.
//
// Renders the question shell (headline + optional subheader + required marker)
// and dispatches to the per-type component for the supported question types.
//
// SUPPORTED (implemented): openText, singleSelect, multiSelect, pictureChoice,
//   rating, nps, cta, consent, date, number, ranking, matrix, contactInfo,
//   address, scheduleMeeting, fileUpload
//   (fileUpload renders its field but the upload backend is deferred — see
//   FileUpload.tsx; it cannot record an answer yet).
// DEFERRED (render a clear "not supported yet" placeholder so they fail loudly
//   without crashing the flow): hidden.
//   ('hidden' is never rendered by the runtime — it is prefilled from URL/link.)
// ============================================================================
import { resolveI18n } from "../i18n";
import { resolveRecall } from "../recall";
import { hasRichText, sanitizeHtml } from "../sanitizeHtml";
import type { AnswerMap, AnswerValue, HiddenField, Question } from "../types";
import { Address } from "./Address";
import { Consent } from "./Consent";
import { ContactInfo } from "./ContactInfo";
import { Cta } from "./Cta";
import { DatePicker } from "./DatePicker";
import { FileUpload } from "./FileUpload";
import { Matrix } from "./Matrix";
import { MultiSelect } from "./MultiSelect";
import { Nps } from "./Nps";
import { NumberInput } from "./NumberInput";
import { OpenText } from "./OpenText";
import { PictureChoice } from "./PictureChoice";
import { Ranking } from "./Ranking";
import { Rating } from "./Rating";
import { ScheduleMeeting } from "./ScheduleMeeting";
import { SingleSelect } from "./SingleSelect";

export interface QuestionRendererProps {
  question: Question;
  value: AnswerValue | undefined;
  onChange: (value: AnswerValue | undefined) => void;
  lang: string;
  defaultLang: string;
  /** Whether this question is required for THIS respondent (static or logic-required). */
  required: boolean;
  /**
   * Answers so far (+ captured hidden-field values) — used to resolve recall/piping
   * tokens in the headline/subheader. Optional + defaults to {} so a headline with no
   * recall token renders unchanged and existing callers/tests are unaffected.
   */
  answers?: AnswerMap;
  /** Declared survey-level hidden fields (recall source metadata). Optional. */
  hiddenFields?: HiddenField[];
}

/** The set of question types this runtime can render. */
export const SUPPORTED_TYPES = new Set([
  "openText",
  "singleSelect",
  "multiSelect",
  "pictureChoice",
  "rating",
  "nps",
  "cta",
  "consent",
  "date",
  "number",
  "ranking",
  "matrix",
  "contactInfo",
  "address",
  "scheduleMeeting",
  "fileUpload",
]);

export function isSupported(type: string): boolean {
  return SUPPORTED_TYPES.has(type);
}

export function QuestionRenderer({
  question,
  value,
  onChange,
  lang,
  defaultLang,
  required,
  answers,
  hiddenFields,
}: QuestionRendererProps) {
  const inputId = `q-${question.id}`;
  // Resolve i18n FIRST (active language), then recall/piping on the resolved string so
  // tokens render with live prior-answer / hidden-field values (or their fallback). No
  // token → resolveRecall returns the string unchanged.
  const headline = resolveRecall(
    resolveI18n(question.headline, lang, defaultLang),
    answers,
    hiddenFields,
    lang,
  );
  const subheader = resolveRecall(
    resolveI18n(question.subheader, lang, defaultLang),
    answers,
    hiddenFields,
    lang,
  );
  // Rich-text DESCRIPTION (optional). ORDER (security-critical): resolve i18n →
  // resolve recall tokens → SANITIZE last, so a recalled answer value cannot
  // smuggle markup into trusted HTML. Renders IN PLACE OF the plain subheader
  // when it has visible content for the active language; otherwise the plain
  // subheader path below is used (byte-identical to pre-rich-text behavior).
  const descriptionRaw = resolveRecall(
    resolveI18n(question.descriptionHtml, lang, defaultLang),
    answers,
    hiddenFields,
    lang,
  );
  const descriptionHtml = hasRichText(descriptionRaw) ? sanitizeHtml(descriptionRaw) : "";
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const config = (question.config ?? {}) as any;

  // The shared prop shape for the per-type components.
  const common = { question, config, value, onChange, lang, defaultLang, inputId } as const;

  let body: React.ReactNode;
  switch (question.type) {
    case "openText":
      body = <OpenText {...common} value={value as string | undefined} onChange={onChange} />;
      break;
    case "singleSelect":
      body = <SingleSelect {...common} value={value as never} onChange={onChange as never} />;
      break;
    case "multiSelect":
      body = <MultiSelect {...common} value={value as string[] | undefined} onChange={onChange as never} />;
      break;
    case "pictureChoice":
      body = <PictureChoice {...common} value={value as never} onChange={onChange as never} />;
      break;
    case "rating":
      body = <Rating {...common} value={value as number | undefined} onChange={onChange as never} />;
      break;
    case "nps":
      body = <Nps {...common} value={value as number | undefined} onChange={onChange as never} />;
      break;
    case "cta":
      body = <Cta {...common} value={value as boolean | undefined} onChange={onChange as never} />;
      break;
    case "consent":
      body = <Consent {...common} value={value as boolean | undefined} onChange={onChange as never} />;
      break;
    case "date":
      body = <DatePicker {...common} value={value as string | undefined} onChange={onChange as never} />;
      break;
    case "number":
      body = <NumberInput {...common} value={value as number | undefined} onChange={onChange as never} />;
      break;
    case "ranking":
      body = <Ranking {...common} value={value as string[] | undefined} onChange={onChange as never} />;
      break;
    case "matrix":
      body = <Matrix {...common} value={value as never} onChange={onChange as never} />;
      break;
    case "contactInfo":
      body = <ContactInfo {...common} value={value as never} onChange={onChange as never} />;
      break;
    case "address":
      body = <Address {...common} value={value as never} onChange={onChange as never} />;
      break;
    case "scheduleMeeting":
      body = <ScheduleMeeting {...common} value={value as never} onChange={onChange as never} />;
      break;
    case "fileUpload":
      body = <FileUpload {...common} value={value as string[] | undefined} onChange={onChange as never} />;
      break;
    default:
      body = (
        <div
          role="alert"
          className="rounded-lg border border-dashed border-amber-400 bg-amber-50 px-4 py-3 text-sm text-amber-800"
        >
          Question type <code className="font-mono">{question.type}</code> is not supported in this
          runtime yet.
        </div>
      );
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 id={`${inputId}-label`} className="text-xl font-semibold text-slate-900">
          {headline}
          {required && (
            <span aria-hidden="true" className="ml-1 text-rose-500" title="Required">
              *
            </span>
          )}
        </h2>
        {descriptionHtml ? (
          // Sanitized author rich text (see lib/sanitizeHtml.ts — script/handlers/
          // dangerous URLs already stripped). Takes precedence over plain subheader.
          <div
            className="prose prose-sm mt-1 max-w-none text-slate-600"
            // eslint-disable-next-line react/no-danger
            dangerouslySetInnerHTML={{ __html: descriptionHtml }}
          />
        ) : (
          subheader && <p className="mt-1 text-sm text-slate-600">{subheader}</p>
        )}
      </div>
      {body}
    </div>
  );
}
