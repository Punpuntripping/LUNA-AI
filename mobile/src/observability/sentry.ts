import * as Sentry from "@sentry/react-native";
import * as Updates from "expo-updates";

import { ApiError } from "@/api/errors";
import { SENTRY_DSN } from "@/config/env";

// Crash reporting — Sentry, EU region, no personal data (plan 04 §S).
// What leaves the phone: the error + stack, device/OS model, app version and
// EAS Update id. Never: email, IP, tokens, message text, URL query strings.
//
// No trace headers go to our API (`tracePropagationTargets: []`, no
// `traceparent`): a sampled-out `traceparent` would make the backend's OTel
// drop its spans, and the backend is out of scope for the app. The link to
// Logfire is the `request_id` tag (= the X-Request-ID the backend logs).

const EMAIL = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi;

function redact(text: string): string {
  return text.replace(EMAIL, "[email]");
}

function stripQuery(url: string): string {
  const cut = url.search(/[?#]/);
  return cut === -1 ? url : url.slice(0, cut);
}

function environment(): string {
  if (__DEV__) return "development";
  return Updates.channel || "production";
}

export function initSentry(): void {
  if (!SENTRY_DSN) return;

  Sentry.init({
    dsn: SENTRY_DSN,
    environment: environment(),
    sendDefaultPii: false,
    attachScreenshot: false,
    attachViewHierarchy: false,
    enableCaptureFailedRequests: false,
    tracesSampleRate: 0.1,
    tracePropagationTargets: [],
    propagateTraceparent: false,
    initialScope: { tags: { update_id: Updates.updateId ?? "embedded" } },

    beforeBreadcrumb(breadcrumb) {
      // console lines can carry chat text — never ship them.
      if (breadcrumb.category === "console") return null;
      if (breadcrumb.data && typeof breadcrumb.data.url === "string") {
        breadcrumb.data.url = stripQuery(breadcrumb.data.url);
      }
      if (breadcrumb.message) breadcrumb.message = redact(breadcrumb.message);
      return breadcrumb;
    },

    beforeSend(event, hint) {
      const original = hint.originalException;
      if (original instanceof ApiError && original.requestId) {
        event.tags = { ...event.tags, request_id: original.requestId };
      }
      delete event.user;
      if (event.request) {
        delete event.request.headers;
        delete event.request.cookies;
        delete event.request.data;
        delete event.request.query_string;
        if (event.request.url) event.request.url = stripQuery(event.request.url);
      }
      if (event.message) event.message = redact(event.message);
      for (const ex of event.exception?.values ?? []) {
        if (ex.value) ex.value = redact(ex.value);
      }
      return event;
    },
  });
}

export const wrapRoot = Sentry.wrap;
