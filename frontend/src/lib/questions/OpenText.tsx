// ============================================================================
// OpenSurvey — open text question (short input / long textarea).
// Answer shape: string (survey-schema.json #/$defs/answerShapes).
// ============================================================================
import { resolveI18n } from "../i18n";
import type { OpenTextConfig } from "../types";
import type { QuestionRendererProps } from "./types";

export function OpenText({
  config,
  value,
  onChange,
  lang,
  defaultLang,
  inputId,
}: QuestionRendererProps<OpenTextConfig, string>) {
  const placeholder = resolveI18n(config.placeholder, lang, defaultLang);
  const current = value ?? "";
  const isLong = config.inputType === "long";

  const common = {
    id: inputId,
    value: current,
    placeholder: placeholder || undefined,
    maxLength: config.maxLength,
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      const v = e.target.value;
      onChange(v === "" ? undefined : v);
    },
    className:
      "w-full rounded-lg border border-slate-300 bg-white px-4 py-3 text-base text-slate-900 " +
      "shadow-sm transition focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200",
  };

  return isLong ? (
    <textarea {...common} rows={5} />
  ) : (
    <input {...common} type="text" />
  );
}
