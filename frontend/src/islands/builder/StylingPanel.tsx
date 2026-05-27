// ============================================================================
// OpenSurvey — Builder STYLE tab editor.
//
// Edits the survey's `Styling` object (the frozen contracts/survey-schema.json
// #/$defs/styling shape). Every control emits a PARTIAL patch; BuilderApp runs it
// through surveyModel.patchStyling, which cleans + clamps to a schema-valid object
// (blank colors dropped, roundness 0..32, defaults omitted). The shared lib/styling
// mappers turn the result into CSS, so the right-hand PreviewPane reflects each edit
// LIVE and identically to the public runtime ("preview == production").
//
// "Reset to default" is expressed as OMISSION: the swatches SEED from BRAND_DEFAULTS
// (so a native <input type=color> shows the effective color, not black) but a default
// value is never persisted — Reset clears the key.
// ============================================================================
import { BRAND_DEFAULTS } from "../../lib/styling";
import type { Styling } from "../../lib/types";

export interface StylingPanelProps {
  styling: Styling;
  /** Emit a partial patch (BuilderApp merges + cleans via patchStyling). */
  onChange: (patch: Partial<Styling>) => void;
}

const HEX6 = /^#[0-9a-fA-F]{6}$/;

/** A value usable by <input type=color> (must be #rrggbb); else fall back to a hex. */
function asHexInput(value: string | undefined, fallback: string): string {
  return value && HEX6.test(value) ? value : fallback;
}

const COLOR_FIELDS: Array<{ key: keyof typeof BRAND_DEFAULTS & keyof Styling; label: string; hint: string }> = [
  { key: "brandColor", label: "Brand color", hint: "Buttons, progress bar, accents" },
  { key: "questionColor", label: "Question text", hint: "Headlines & question copy" },
  { key: "backgroundColor", label: "Card background", hint: "The survey card surface" },
  { key: "buttonColor", label: "Button color", hint: "Overrides the brand color on buttons" },
  { key: "buttonTextColor", label: "Button text", hint: "Label color inside buttons" },
];

export function StylingPanel({ styling, onChange }: StylingPanelProps) {
  const bgType = styling.background?.type ?? "none";
  const bgValue = styling.background?.value ?? "";

  return (
    <div className="space-y-6">
      {/* ---- Colors ---- */}
      <Group title="Colors">
        <div className="space-y-3">
          {COLOR_FIELDS.map((f) => (
            <ColorRow
              key={f.key}
              label={f.label}
              hint={f.hint}
              value={styling[f.key] as string | undefined}
              fallback={BRAND_DEFAULTS[f.key]}
              onChange={(v) => onChange({ [f.key]: v } as Partial<Styling>)}
            />
          ))}
        </div>
      </Group>

      {/* ---- Page background ---- */}
      <Group title="Page background" hint="Behind the survey card">
        <div className="space-y-3">
          <Segmented
            ariaLabel="Page background type"
            value={bgType}
            options={[
              { value: "none", label: "Default" },
              { value: "color", label: "Solid color" },
              { value: "image", label: "Image" },
            ]}
            onChange={(next) => {
              if (next === "none") {
                onChange({ background: { type: "none" } });
              } else if (next === "color") {
                onChange({ background: { type: "color", value: bgValue || BRAND_DEFAULTS.pageBackground } });
              } else {
                onChange({ background: { type: "image", value: bgType === "image" ? bgValue : "" } });
              }
            }}
          />

          {bgType === "color" && (
            <ColorRow
              label="Background color"
              value={bgValue}
              fallback={BRAND_DEFAULTS.pageBackground}
              hideReset
              onChange={(v) =>
                onChange({ background: { type: "color", value: v || BRAND_DEFAULTS.pageBackground } })
              }
            />
          )}

          {bgType === "image" && (
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-muted">Image URL</span>
              <input
                type="url"
                value={bgValue}
                placeholder="https://…/background.jpg"
                onChange={(e) => onChange({ background: { type: "image", value: e.target.value } })}
                className={inputClass}
              />
            </label>
          )}
        </div>
      </Group>

      {/* ---- Logo ---- */}
      <Group title="Logo" hint="Shown above the card">
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted">Logo URL</span>
          <input
            type="url"
            value={styling.logoUrl ?? ""}
            placeholder="https://…/logo.png"
            onChange={(e) => onChange({ logoUrl: e.target.value })}
            className={inputClass}
          />
        </label>
        {styling.logoUrl && (
          <div className="mt-2 flex items-center gap-2">
            <img src={styling.logoUrl} alt="" className="max-h-8 w-auto object-contain" />
            <button type="button" onClick={() => onChange({ logoUrl: "" })} className={resetClass}>
              Remove
            </button>
          </div>
        )}
      </Group>

      {/* ---- Corners (roundness) ---- */}
      <Group title="Corner roundness" hint="Card & control corners">
        <div className="flex items-center gap-3">
          <input
            type="range"
            min={0}
            max={32}
            step={1}
            value={styling.roundness ?? 16}
            onChange={(e) => onChange({ roundness: Number(e.target.value) })}
            aria-label="Corner roundness"
            className="h-1.5 flex-1 cursor-pointer accent-brand"
          />
          <span className="w-12 text-right text-sm tabular-nums text-ink">
            {styling.roundness ?? 16}px
          </span>
          {styling.roundness !== undefined && (
            <button type="button" onClick={() => onChange({ roundness: undefined })} className={resetClass}>
              Reset
            </button>
          )}
        </div>
      </Group>

      {/* ---- Card layout ---- */}
      <Group title="Card layout">
        <Segmented
          ariaLabel="Card arrangement"
          value={styling.cardArrangement ?? "straight"}
          options={[
            { value: "straight", label: "Straight" },
            { value: "casual", label: "Casual" },
          ]}
          onChange={(next) => onChange({ cardArrangement: next as "straight" | "casual" })}
        />
      </Group>

      {/* ---- Progress bar ---- */}
      <Group title="Progress bar">
        <label className="flex cursor-pointer items-center gap-2.5">
          <input
            type="checkbox"
            checked={styling.hideProgressBar === true}
            onChange={(e) => onChange({ hideProgressBar: e.target.checked })}
            className="h-4 w-4 rounded border-hairline text-brand accent-brand focus:ring-brand"
          />
          <span className="text-sm text-ink">Hide the progress bar</span>
        </label>
      </Group>
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  building blocks                                                            //
// --------------------------------------------------------------------------- //
const inputClass =
  "w-full rounded-lg border border-hairline bg-white px-3 py-2 text-sm text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand";
const resetClass =
  "rounded-md px-2 py-1 text-xs font-medium text-muted transition-colors hover:bg-panel hover:text-ink";

function Group({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
        {title}
        {hint && <span className="ml-2 font-normal normal-case tracking-normal text-muted/70">{hint}</span>}
      </h3>
      {children}
    </section>
  );
}

/**
 * A single color control: a native swatch (seeded from the effective color so it's
 * never an empty black) + a free-text CSS color field (for rgb()/named colors) + an
 * optional Reset that clears the key (back to the brand default).
 */
function ColorRow({
  label,
  hint,
  value,
  fallback,
  hideReset,
  onChange,
}: {
  label: string;
  hint?: string;
  value: string | undefined;
  fallback: string;
  hideReset?: boolean;
  onChange: (value: string) => void;
}) {
  const overridden = typeof value === "string" && value.trim() !== "";
  return (
    <div className="flex items-center gap-3">
      <input
        type="color"
        value={asHexInput(value, fallback)}
        onChange={(e) => onChange(e.target.value)}
        aria-label={`${label} swatch`}
        className="h-9 w-10 shrink-0 cursor-pointer rounded-md border border-hairline bg-white p-0.5"
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-medium text-ink">{label}</span>
          {overridden && !hideReset && (
            <button type="button" onClick={() => onChange("")} className={resetClass}>
              Reset
            </button>
          )}
        </div>
        {hint && <p className="text-xs text-muted/70">{hint}</p>}
      </div>
      <input
        type="text"
        value={value ?? ""}
        placeholder={fallback}
        onChange={(e) => onChange(e.target.value)}
        aria-label={`${label} value`}
        className="w-28 shrink-0 rounded-lg border border-hairline bg-white px-2.5 py-1.5 text-xs tabular-nums text-ink shadow-sm transition-colors focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
      />
    </div>
  );
}

/** A small segmented control (Tailwind brand tokens) used for enum-ish settings. */
function Segmented<T extends string>({
  ariaLabel,
  value,
  options,
  onChange,
}: {
  ariaLabel: string;
  value: T;
  options: Array<{ value: T; label: string }>;
  onChange: (value: T) => void;
}) {
  return (
    <div role="radiogroup" aria-label={ariaLabel} className="inline-flex items-center gap-1 rounded-lg bg-panel p-1">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(o.value)}
            className={
              "rounded-md px-3 py-1.5 text-sm font-medium transition-colors " +
              (active ? "bg-white text-brand shadow-sm" : "text-muted hover:text-ink")
            }
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
