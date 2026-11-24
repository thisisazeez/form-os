// ============================================================================
// onForm — schedule-a-meeting question (Cal.com inline embed).
//
// Answer shape (survey-schema.json #/$defs/answerShapes):
//   { booked: true, uid?: string }   once a meeting is booked; undefined before.
//
// config.calLink is a cal.com PATH ("username" or "username/event-type"), not a
// full URL. We render the inline scheduling embed as an <iframe> pointing at
// https://cal.com/<calLink> and listen for Cal's `BOOKING_SUCCESSFUL` postMessage
// to record the booking. This is dependency-free (no @calcom/embed-react), so it
// stays testable: tests assert the iframe src and post a synthetic booking event.
//
// VERIFICATION NOTE: the postMessage capture is verified synthetically (a fake
// BOOKING_SUCCESSFUL message). A full end-to-end booking needs a real cal.com
// link + a live booking flow, which is out of scope for unit tests — see the
// report's "deferred" note.
// ============================================================================
import { useEffect, useState } from "react";

import type { ScheduleMeetingConfig } from "../types";
import type { QuestionRendererProps } from "./types";

interface Booking {
  booked: true;
  uid?: string;
}

/** Cal.com's embed posts messages namespaced under `CAL:`; the success type is
 * `bookingSuccessful` / `BOOKING_SUCCESSFUL` depending on embed version. We accept
 * either spelling and pull a booking uid from common payload shapes. */
function extractBooking(data: unknown): Booking | null {
  if (!data || typeof data !== "object") return null;
  const msg = data as Record<string, unknown>;
  const type = String(msg.type ?? "").toLowerCase();
  const isSuccess =
    type === "booking_successful" ||
    type === "bookingsuccessful" ||
    type === "booking_successful_v2";
  if (!isSuccess) return null;
  // uid may live at data.data.uid (cal embed) or data.uid.
  let uid: string | undefined;
  const payload = msg.data;
  if (payload && typeof payload === "object") {
    const inner = payload as Record<string, unknown>;
    if (typeof inner.uid === "string") uid = inner.uid;
    else if (inner.booking && typeof inner.booking === "object") {
      const b = inner.booking as Record<string, unknown>;
      if (typeof b.uid === "string") uid = b.uid;
    }
  } else if (typeof msg.uid === "string") {
    uid = msg.uid;
  }
  return uid ? { booked: true, uid } : { booked: true };
}

export function ScheduleMeeting({
  config,
  value,
  onChange,
  inputId,
}: QuestionRendererProps<ScheduleMeetingConfig, Booking>) {
  const calLink = (config.calLink ?? "").trim().replace(/^\/+/, "");
  const booking = value && typeof value === "object" && (value as Booking).booked ? (value as Booking) : null;
  const [error, setError] = useState(false);

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      const result = extractBooking(event.data);
      if (result) onChange(result);
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [onChange]);

  if (!calLink) {
    return (
      <div
        role="alert"
        className="rounded-lg border border-dashed border-amber-400 bg-amber-50 px-4 py-3 text-sm text-amber-800"
      >
        No scheduling link is configured for this question.
      </div>
    );
  }

  const src = `https://cal.com/${calLink}?embed=true&embedType=inline`;

  return (
    <div className="space-y-3">
      {booking && (
        <div
          role="status"
          className="rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-3 text-sm text-emerald-800"
        >
          Your meeting is booked.
          {booking.uid ? <span className="ml-1 font-mono text-xs">({booking.uid})</span> : null}
        </div>
      )}
      {error ? (
        <div
          role="note"
          className="rounded-lg border border-dashed border-amber-400 bg-amber-50 px-4 py-3 text-sm text-amber-800"
        >
          The scheduling widget could not be loaded. Open{" "}
          <a
            href={`https://cal.com/${calLink}`}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-indigo-600 underline"
          >
            the booking page
          </a>{" "}
          to schedule.
        </div>
      ) : (
        <iframe
          id={inputId}
          title="Schedule a meeting"
          src={src}
          onError={() => setError(true)}
          className="h-[600px] w-full rounded-lg border border-slate-200 bg-white"
          loading="lazy"
        />
      )}
    </div>
  );
}
