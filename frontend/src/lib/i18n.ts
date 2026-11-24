// ============================================================================
// onForm — i18n string resolution.
//
// Every user-facing display string in survey-schema.json is an i18n map
// {langCode: text} that MUST contain a 'default' key (the schema-required
// fallback). The runtime renders the ACTIVE response language; this helper
// resolves the right text with the frozen fallback chain.
// ============================================================================

import type { I18nString, I18nStringOptional, Language } from "./types";

/**
 * Resolve a localized string for the active language.
 *
 * Fallback chain (FROZEN): activeLang -> defaultLangCode -> the reserved
 * 'default' key -> "" . `defaultLangCode` is the code of the survey's
 * default-language entry; it pairs with the 'default' i18nString key, but we
 * try both in case authors keyed only one of them.
 */
export function resolveI18n(
  str: I18nString | I18nStringOptional | undefined,
  activeLang: string,
  defaultLangCode?: string,
): string {
  if (!str) return "";
  if (activeLang && str[activeLang] !== undefined) return str[activeLang];
  if (defaultLangCode && str[defaultLangCode] !== undefined) return str[defaultLangCode];
  if (str.default !== undefined) return str.default;
  return "";
}

/** True if the optional i18n map has any usable text for the active language. */
export function hasI18n(
  str: I18nStringOptional | undefined,
  activeLang: string,
  defaultLangCode?: string,
): boolean {
  return resolveI18n(str, activeLang, defaultLangCode) !== "";
}

/** The code of the survey's default language (the entry with default=true), or "default". */
export function defaultLanguageCode(languages: Language[] | undefined): string {
  const def = (languages ?? []).find((l) => l.default);
  return def?.code ?? "default";
}

/**
 * Pick the active language code for a response.
 * Prefers an explicitly requested language IF it is a declared+enabled language;
 * otherwise falls back to the survey default.
 */
export function resolveActiveLanguage(
  requested: string | undefined | null,
  languages: Language[] | undefined,
): string {
  const langs = languages ?? [];
  if (requested) {
    const match = langs.find((l) => l.code === requested && l.enabled !== false);
    if (match) return match.code;
  }
  return defaultLanguageCode(languages);
}
