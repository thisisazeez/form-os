// ============================================================================
// OpenSurvey — file-upload question.
// Answer shape: [fileUrl] (survey-schema.json #/$defs/answerShapes) — an array
// of the URLs of files uploaded to storage.
//
// BACKEND GAP (deferred, matched on both sides): the presigned `upload-url`
// endpoint is intentionally NOT implemented/routed yet (see
// apps/responses/public_views.py — "the frontend's file-upload deferral";
// contracts/openapi.yaml defines /upload-url but it is not wired). Without it
// there are NO file URLs to store. So this renderer honestly renders the field
// (respecting config: multiple, allowed extensions, size hint) and shows the
// chosen file names, but DOES NOT fabricate URLs into the answer — `onChange`
// is never called with placeholder strings a downstream consumer might mistake
// for real URLs. The answer therefore stays empty until the upload pipeline
// lands; a required fileUpload is correctly not satisfiable yet. See the report
// for the follow-up to wire onChange to the presigned-upload flow.
// ============================================================================
import { useState } from "react";

import type { FileUploadConfig } from "../types";
import type { QuestionRendererProps } from "./types";

export function FileUpload({
  config,
  inputId,
}: QuestionRendererProps<FileUploadConfig, string[]>) {
  const [names, setNames] = useState<string[]>([]);

  const accept = (config.allowedExtensions ?? []).join(",") || undefined;
  const multiple = config.allowMultiple === true;
  const maxFiles = config.maxFiles ?? 1;
  const maxSizeMb = config.maxSizeMb ?? 10;

  function onPick(files: FileList | null) {
    if (!files) {
      setNames([]);
      return;
    }
    const picked = Array.from(files).slice(0, multiple ? maxFiles : 1);
    setNames(picked.map((f) => f.name));
    // NOTE: no onChange — there is no upload endpoint to turn these Files into
    // the [fileUrl] answer shape yet (see the header). Display only.
  }

  return (
    <div className="space-y-3">
      <input
        type="file"
        id={inputId}
        accept={accept}
        multiple={multiple}
        onChange={(e) => onPick(e.target.files)}
        className={
          "block w-full rounded-lg border border-slate-300 bg-white text-base text-slate-900 shadow-sm " +
          "file:mr-4 file:rounded-md file:border-0 file:bg-indigo-50 file:px-4 file:py-2.5 " +
          "file:text-sm file:font-medium file:text-indigo-700 hover:file:bg-indigo-100 " +
          "focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200"
        }
      />
      <p className="text-xs text-slate-500">
        {multiple ? `Up to ${maxFiles} files` : "Single file"}, max {maxSizeMb} MB each
        {accept ? ` · ${accept}` : ""}.
      </p>
      {names.length > 0 && (
        <ul className="space-y-1 text-sm text-slate-700">
          {names.map((name, i) => (
            <li key={`${name}-${i}`} className="flex items-center gap-2">
              <span aria-hidden="true">📎</span>
              <span className="truncate">{name}</span>
            </li>
          ))}
        </ul>
      )}
      <p
        role="note"
        className="rounded-lg border border-dashed border-amber-400 bg-amber-50 px-3 py-2 text-xs text-amber-800"
      >
        File uploads are not yet wired to storage — selected files are shown for preview but not
        submitted.
      </p>
    </div>
  );
}
