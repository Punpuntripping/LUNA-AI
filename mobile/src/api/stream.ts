import { fetch } from "expo/fetch";

import type { SendMessagePayload } from "@contracts";

import { expireSession, refreshAccessToken, SessionExpiredError } from "@/auth/refresh";
import { getAccessToken } from "@/auth/token-store";
import { API_PREFIX, API_URL } from "@/config/env";
import { useUpdateGate } from "@/stores/update-gate";

import { ApiError, errorFromBody, fallbackForStatus, MSG_EDGE, MSG_NETWORK } from "./errors";
import { appHeaders } from "./headers";
import { StreamNotSentError } from "./stream-errors";

// X1 — open the SSE answer stream for one send:
// `POST /conversations/{id}/messages` → `text/event-stream`.
//
// `expo/fetch` is imported explicitly: it is the WinterCG fetch whose
// `response.body.getReader()` streams on iOS + Android (React Native's own
// fetch buffers the whole body). No total timeout — a deep search runs for
// minutes; the idle watchdog lives in the reader (sse-reader.ts).
//
// Error contract for the caller (src/chat/run-send.ts):
// - StreamNotSentError → refused BEFORE any server write (every 4xx of this
//   route, a Cloudflare block, a dead session). Safe to mark failed.
// - anything else (ApiError `network`, 5xx) → the request MAY have been
//   processed. The caller must NEVER re-POST; it recovers read-only.

function looksLikeHtml(contentType: string | null): boolean {
  return !!contentType && contentType.toLowerCase().includes("text/html");
}

/** A dead session is a certain "not sent"; an outage during the refresh
 *  rethrows as-is (still pre-write here, so also a certain "not sent"). */
async function freshToken(): Promise<string> {
  try {
    return await refreshAccessToken();
  } catch (err) {
    if (err instanceof SessionExpiredError) {
      throw new StreamNotSentError(
        new ApiError({ kind: "http", status: 401, code: "AUTH_EXPIRED", message: fallbackForStatus(401) }),
      );
    }
    throw new StreamNotSentError(
      err instanceof ApiError ? err : new ApiError({ kind: "network", status: 0, message: MSG_NETWORK }),
    );
  }
}

export async function openMessageStream(
  conversationId: string,
  payload: SendMessagePayload,
  signal: AbortSignal,
): Promise<ReadableStream<Uint8Array>> {
  const url = `${API_URL}${API_PREFIX}/conversations/${encodeURIComponent(conversationId)}/messages`;
  const body = JSON.stringify({
    content: payload.content,
    ...(payload.attachment_ids?.length ? { attachment_ids: payload.attachment_ids } : {}),
  });

  const post = async (token: string) => {
    const { headers: base, requestId } = appHeaders();
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: {
          ...base,
          Accept: "text/event-stream",
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body,
        signal,
      });
      return { res, requestId };
    } catch {
      // Offline, DNS, TLS, reset, or our own abort — the request may or may
      // not have left the phone. Not a StreamNotSentError on purpose.
      throw new ApiError({ kind: "network", status: 0, message: MSG_NETWORK, requestId });
    }
  };

  let token = getAccessToken() ?? (await freshToken());
  let { res, requestId } = await post(token);

  // 401 comes from `get_current_user`, a route dependency that runs before the
  // generator writes anything (backend/app/api/messages.py) — the ONE place a
  // re-POST is safe. Exactly one refresh, exactly one re-POST.
  if (res.status === 401) {
    res.body?.cancel().catch(() => undefined);
    token = await freshToken();
    ({ res, requestId } = await post(token));
    if (res.status === 401) {
      res.body?.cancel().catch(() => undefined);
      await expireSession();
      throw new StreamNotSentError(
        new ApiError({ kind: "http", status: 401, code: "AUTH_EXPIRED", message: fallbackForStatus(401), requestId }),
      );
    }
  }

  const contentType = res.headers.get("content-type");
  if (res.headers.get("cf-mitigated")) {
    res.body?.cancel().catch(() => undefined);
    throw new StreamNotSentError(new ApiError({ kind: "edge", status: res.status, message: MSG_EDGE, requestId }));
  }

  if (!res.ok) {
    if (res.status === 426) useUpdateGate.getState().markUpdateRequired();
    let parsed: unknown = null;
    if (!looksLikeHtml(contentType)) {
      try {
        parsed = await res.json();
      } catch {
        parsed = null;
      }
    } else {
      res.body?.cancel().catch(() => undefined);
    }
    // A 5xx (or an HTML 502/504 from the edge) may come from a proxy AFTER
    // the origin got the request — unknown, so plain ApiError (no re-POST).
    if (res.status >= 500) {
      throw looksLikeHtml(contentType)
        ? new ApiError({ kind: "edge", status: res.status, message: MSG_EDGE, requestId })
        : errorFromBody(res.status, parsed, requestId);
    }
    const err = looksLikeHtml(contentType)
      ? new ApiError({ kind: "edge", status: res.status, message: MSG_EDGE, requestId })
      : errorFromBody(res.status, parsed, requestId);
    throw new StreamNotSentError(err);
  }

  // 200 + HTML = a captive-portal / edge page: the origin never saw the send.
  // Any other content type is read as the stream — calling a send that DID
  // start "not sent" would invite a manual double send.
  if (looksLikeHtml(contentType)) {
    res.body?.cancel().catch(() => undefined);
    throw new StreamNotSentError(new ApiError({ kind: "edge", status: res.status, message: MSG_EDGE, requestId }));
  }

  if (!res.body) {
    throw new ApiError({ kind: "network", status: res.status, message: MSG_NETWORK, requestId });
  }
  return res.body;
}
