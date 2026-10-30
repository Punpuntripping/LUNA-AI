/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { ApiError } from "@/api/errors";
import { StreamNotSentError } from "@/api/stream-errors";

import { placeholderPollMs } from "@contracts";

import { buildChatItems } from "../chat-items";
import { chronological } from "../message-cache";
import { MSG_SEND_FAILED } from "../run-send";
import { selectDeepSearchProgress, selectRunningAgent } from "../stream-store";
import { frame, harness, msg, tick } from "./helpers";

const C1 = "c1";
const C2 = "c2";
const START = { user_message_id: "u1", assistant_message_id: "a1", conversation_id: C1 };
const DONE = { message_id: "a1", usage: { prompt_tokens: 1, completion_tokens: 2 } };

async function startSend(h: ReturnType<typeof harness>, content = "ما حكم الشفعة؟", conv = C1) {
  let committed = 0;
  const promise = h.engine.send({ conversationId: conv, content, onCommitted: () => (committed += 1) });
  await tick();
  return { promise, committed: () => committed };
}

describe("send — happy path", () => {
  test("message_start → tokens → chips → done writes the final row before clearing", async () => {
    const h = harness();
    h.cache.seed(C1, [msg({ message_id: "old", role: "assistant", content: "سابق" })]);
    const { promise, committed } = await startSend(h);

    // Optimistic row first, chips not released yet.
    assert.equal(h.cache.rows(C1)[0].isOptimistic, true);
    assert.equal(committed(), 0);

    const s = h.streams[0];
    s.push(frame("message_start", START));
    await tick();
    assert.equal(committed(), 1);
    assert.equal(h.cache.rows(C1)[0].message_id, "u1");
    assert.equal(h.store.getState().streams[C1].phase, "streaming");

    s.push(frame("token", { text: "الشفعة " }));
    s.push(frame("token", { text: "حق" }));
    s.push(frame("templates_used", { items: [] }));
    s.push(
      frame("next_steps", {
        items: [{ kind: "draft", label: "صياغة مذكرة", prompt: "اكتب مذكرة" }],
      }),
    );
    s.push(frame("done", { ...DONE, artifact_ids: ["wi1"], referenced_item_ids: null }));
    const result = await promise;

    assert.deepEqual(result, { status: "done", assistantMessageId: "a1" });
    const answer = h.cache.rows(C1)[0];
    assert.equal(answer.message_id, "a1");
    assert.equal(answer.content, "الشفعة حق");
    assert.deepEqual(answer.artifact_ids, ["wi1"]);
    assert.equal(answer.metadata?.next_steps?.length, 1);
    assert.deepEqual(answer.metadata?.templates_used, []);
    assert.equal(h.store.getState().streams[C1], undefined);
    assert.equal(h.cache.count(`invalidate:messages:${C1}`), 1);
    assert.equal(h.cache.count("invalidate:lists"), 1);
    assert.equal(h.cache.count(`invalidate:workspace:${C1}`), 1);
    assert.equal(h.posts.length, 1);
    assert.equal(committed(), 1);
  });

  test("done fills an empty placeholder that a refetch already brought in", async () => {
    const h = harness();
    const { promise } = await startSend(h);
    const s = h.streams[0];
    s.push(frame("message_start", START));
    s.push(frame("token", { text: "جواب" }));
    await tick();
    h.cache.setMessages(C1, (d) => ({
      ...d!,
      pages: [{ ...d!.pages[0], messages: [msg({ message_id: "a1", role: "assistant" }), ...d!.pages[0].messages] }],
    }));
    s.push(frame("done", DONE));
    await promise;
    const rows = h.cache.rows(C1).filter((m) => m.message_id === "a1");
    assert.equal(rows.length, 1);
    assert.equal(rows[0].content, "جواب");
  });

  test("tokens are paced: several tokens inside one flush window = one store update", async () => {
    const h = harness();
    const { promise } = await startSend(h);
    const s = h.streams[0];
    s.push(frame("message_start", START));
    await tick();
    let updates = 0;
    const unsub = h.store.api.subscribe(() => (updates += 1));
    s.push(frame("token", { text: "أ" }) + frame("token", { text: "ب" }) + frame("token", { text: "ج" }));
    await tick();
    assert.equal(updates, 0, "nothing published before the flush tick");
    await new Promise((r) => setTimeout(r, 20));
    assert.equal(updates, 1);
    assert.equal(h.store.getState().streams[C1].content, "أبج");
    unsub();
    s.push(frame("done", DONE));
    await promise;
  });

  test("tokens before message_start are ignored", async () => {
    const h = harness();
    const { promise } = await startSend(h);
    const s = h.streams[0];
    s.push(frame("token", { text: "تسرّب" }));
    s.push(frame("message_start", START));
    s.push(frame("token", { text: "سليم" }));
    s.push(frame("done", DONE));
    await promise;
    assert.equal(h.cache.rows(C1)[0].content, "سليم");
  });
});

describe("send — deep search progress around done", () => {
  test("agent_progress(done) BEFORE done seals the chip", async () => {
    const h = harness();
    const { promise } = await startSend(h);
    const s = h.streams[0];
    s.push(frame("message_start", START));
    s.push(frame("agent_run_started", { agent_family: "deep_search" }));
    s.push(frame("agent_progress", { stage: "planning", text: "تخطيط" }));
    s.push(frame("status", { text: "بحث في الأنظمة: الشفعة" }));
    s.push(frame("agent_progress", { stage: "searching", text: "", data: { sources: 12 } }));
    s.push(frame("agent_progress", { stage: "done", text: "", data: { sources: 14 } }));
    s.push(frame("agent_run_finished", { agent_family: "deep_search" }));
    s.push(frame("token", { text: "نص" }));
    s.push(frame("done", DONE));
    await promise;
    const summary = h.store.getState().summaries.a1;
    assert.ok(summary);
    assert.equal(summary.sources, 14);
    assert.ok(summary.log.includes("بحث في الأنظمة: الشفعة"));
  });

  test("agent_run_finished parks the run so done still seals; a late agent_progress is a no-op", async () => {
    const h = harness();
    const { promise } = await startSend(h);
    const s = h.streams[0];
    s.push(frame("message_start", START));
    s.push(frame("agent_progress", { stage: "writing", text: "كتابة", data: { sources: 5 } }));
    s.push(frame("agent_run_finished", { agent_family: "deep_search" }));
    s.push(frame("token", { text: "نص" }));
    s.push(frame("done", DONE));
    s.push(frame("agent_progress", { stage: "done", text: "", data: { sources: 99 } }));
    await promise;
    assert.equal(h.store.getState().summaries.a1.sources, 5);
    assert.equal(h.store.getState().streams[C1], undefined);
  });

  test("stage never walks backwards and empty stage/text are tolerated", async () => {
    const h = harness();
    const { promise } = await startSend(h);
    const s = h.streams[0];
    s.push(frame("message_start", START));
    s.push(frame("agent_progress", { stage: "evaluating", text: "تقييم" }));
    s.push(frame("agent_progress", { stage: "searching", text: "متأخر", data: { queries: 4 } }));
    // The backend may send "" for stage/text.
    s.push(`event: agent_progress\ndata: {"stage":"","text":""}\n\n`);
    await tick();
    const p = h.store.getState().streams[C1].progress!;
    assert.equal(p.stage, "evaluating");
    assert.equal(p.text, "تقييم");
    assert.equal(p.queries, 4);
    s.push(frame("done", DONE));
    await promise;
  });
});

describe("send — agent_question", () => {
  test("agent_question then done → paused, live bubble gone, no row written", async () => {
    const h = harness();
    const { promise } = await startSend(h);
    const s = h.streams[0];
    s.push(frame("message_start", START));
    s.push(frame("agent_progress", { stage: "planning", text: "تخطيط" }));
    s.push(frame("token", { text: "جزء" }));
    s.push(frame("agent_question", { run_id: "r1", question: "أي محكمة؟", suggestions: ["العامة"] } as never));
    await tick();
    assert.equal(h.store.getState().streams[C1], undefined, "cleared on the question");
    s.push(frame("done", DONE));
    const result = await promise;
    assert.deepEqual(result, { status: "paused" });
    assert.equal(h.cache.rows(C1).some((m) => m.message_id === "a1"), false);
    assert.equal(h.store.getState().summaries.a1, undefined, "a pause seals nothing");
    assert.equal(h.cache.count(`invalidate:messages:${C1}`), 1);
  });
});

describe("send — refusals restore the composer", () => {
  test("duplicate: optimistic removed, notice, refetch, text returned", async () => {
    const h = harness();
    const { promise, committed } = await startSend(h, "سؤال مكرر");
    h.streams[0].push(
      frame("duplicate", { assistant_message_id: "a0", conversation_id: C1, detail: "ما زال يتم إنشاء الرد" }),
    );
    const result = await promise;
    assert.equal(result.status, "refused");
    assert.equal(result.status === "refused" && result.reason, "duplicate");
    assert.equal(result.status === "refused" && result.restoreText, "سؤال مكرر");
    assert.equal(h.cache.rows(C1).length, 0);
    assert.deepEqual(h.store.getState().notices[C1], { kind: "duplicate", detail: "ما زال يتم إنشاء الرد" });
    assert.equal(h.cache.count(`invalidate:messages:${C1}`), 1);
    assert.equal(committed(), 0, "chips stay");
  });

  test("quota_exceeded: nothing persisted, notice carries the payload", async () => {
    const h = harness();
    const { promise, committed } = await startSend(h, "نص المستخدم");
    const info = {
      meter: "ord" as const,
      period: "session" as const,
      used: 5,
      limit: 5,
      resets_at: "2026-10-15T15:00:00Z",
      message_ar: "وصلت إلى الحد",
    };
    h.streams[0].push(frame("quota_exceeded", info));
    const result = await promise;
    assert.equal(result.status === "refused" && result.reason, "quota");
    assert.equal(result.status === "refused" && result.restoreText, "نص المستخدم");
    assert.deepEqual(h.store.getState().notices[C1], { kind: "quota", info });
    assert.equal(h.cache.rows(C1).length, 0);
    assert.equal(committed(), 0);
    assert.equal(h.store.getState().streams[C1], undefined);
  });

  test("parallel_limit: refused with the server detail", async () => {
    const h = harness();
    const { promise } = await startSend(h);
    const info = { detail: "لديك محادثتان قيد الإجابة", limit: 2, running: 2, running_conversation_ids: [C2], upgrade_plan: null };
    h.streams[0].push(frame("parallel_limit", info));
    const result = await promise;
    assert.equal(result.status === "refused" && result.reason, "parallel_limit");
    assert.deepEqual(h.store.getState().notices[C1], { kind: "parallel_limit", info });
  });

  test("attachment-only send refused → nothing to restore (never «مرفق»)", async () => {
    const h = harness();
    const promise = h.engine.send({
      conversationId: C1,
      content: "  ",
      attachmentIds: ["wi9"],
      optimisticAttachments: [
        { id: "f1", document_id: "wi9", attachment_type: "pdf", filename: "عقد.pdf", kind: "attachment" },
      ],
    });
    await tick();
    assert.equal(h.posts[0].content, "مرفق");
    h.streams[0].push(frame("quota_exceeded", { meter: "ocr", period: "daily", used: 1, limit: 1, resets_at: "", message_ar: "حد" }));
    const result = await promise;
    assert.equal(result.status === "refused" && result.restoreText, null);
  });
});

describe("send — deep_search_gated (plan gate, migration 197)", () => {
  test("the turn is ANSWERED: refusal text saved as the reply, user row kept, banner set", async () => {
    const h = harness();
    const { promise, committed } = await startSend(h, "قارن بين نظامي العمل والخدمة المدنية");
    const info = {
      reason: "free_limit" as const,
      plan_id: "free",
      upgrade_options: ["basic", "pro", "max"],
      resets_at: "2026-11-19T09:00:00+00:00",
      message_ar: "استخدمت البحث المعمّق المتاح في باقتك لهذه الفترة.",
    };
    const s = h.streams[0];
    s.push(frame("message_start", START));
    s.push(frame("deep_search_gated", info));
    s.push(frame("token", { text: "يحتاج هذا الطلب إلى بحث معمّق." }));
    s.push(frame("done", DONE));
    const result = await promise;

    // Not a refusal: the send committed and the answer is the refusal text.
    assert.deepEqual(result, { status: "done", assistantMessageId: "a1" });
    assert.equal(committed(), 1);
    const [answer, user] = h.cache.rows(C1);
    assert.equal(answer.message_id, "a1");
    assert.equal(answer.content, "يحتاج هذا الطلب إلى بحث معمّق.");
    assert.equal(user.message_id, "u1");
    assert.deepEqual(h.store.getState().notices[C1], { kind: "deep_search_gated", info });
  });

  test("the next send clears the banner", async () => {
    const h = harness();
    h.store.setNotice(C1, {
      kind: "deep_search_gated",
      info: { reason: "low_points", plan_id: "basic", upgrade_options: [], resets_at: null, message_ar: "رصيدك المتبقي لا يكفي لبدء بحث معمّق جديد." },
    });
    await startSend(h);
    assert.equal(h.store.getState().notices[C1], undefined);
  });
});

describe("send — errors", () => {
  test("error before message_start → failed row + banner", async () => {
    const h = harness();
    const { promise } = await startSend(h);
    h.streams[0].push(frame("error", { detail: "الخدمة غير متاحة", code: "QUOTA_UNAVAILABLE" }));
    const result = await promise;
    assert.deepEqual(result, { status: "failed", message: "الخدمة غير متاحة" });
    assert.equal(h.cache.rows(C1)[0].isFailed, true);
    assert.equal(h.store.getState().errors[C1], "الخدمة غير متاحة");
  });

  test("error after message_start → banner, real row untouched, refetch", async () => {
    const h = harness();
    const { promise } = await startSend(h);
    const s = h.streams[0];
    s.push(frame("message_start", START));
    s.push(frame("token", { text: "بداية" }));
    s.push(frame("error", { detail: "Internal error" }));
    const result = await promise;
    assert.equal(result.status, "failed");
    assert.equal(h.store.getState().errors[C1], "حدث خطأ أثناء المعالجة", "never English");
    assert.equal(h.cache.rows(C1)[0].message_id, "u1");
    assert.equal(h.cache.rows(C1)[0].isFailed, undefined);
    assert.equal(h.cache.count(`invalidate:messages:${C1}`), 1);
  });

  test("StreamNotSentError (4xx / dead session) → failed at once, no recovery read", async () => {
    const h = harness({
      openStream: async () => {
        throw new StreamNotSentError(new ApiError({ kind: "http", status: 429, message: "تم تجاوز الحد المسموح من الطلبات" }));
      },
    });
    const result = await h.engine.send({ conversationId: C1, content: "سؤال" });
    assert.deepEqual(result, { status: "failed", message: "تم تجاوز الحد المسموح من الطلبات" });
    assert.equal(h.cache.count(`fetch:${C1}`), 0);
    assert.equal(h.cache.rows(C1)[0].isFailed, true);
  });
});

describe("send — drop BEFORE message_start (never re-POST)", () => {
  test("server has our row + placeholder → adopt, then poll fills it and auto-opens the output", async () => {
    const h = harness();
    h.cache.seed(C1, [msg({ message_id: "old-a", role: "assistant", content: "قديم" })]);
    h.cache.server = async () => ({
      messages: [
        msg({ message_id: "a1", role: "assistant", content: "" }),
        msg({ message_id: "u1", role: "user", content: "ما حكم الشفعة؟" }),
        msg({ message_id: "old-a", role: "assistant", content: "قديم" }),
      ],
      has_more: false,
      run_active: true,
    });
    const { promise, committed } = await startSend(h);
    h.streams[0].fail();
    const result = await promise;

    assert.deepEqual(result, { status: "adopted", settled: false });
    assert.equal(h.posts.length, 1, "never re-POSTed");
    assert.equal(committed(), 1);
    assert.deepEqual(
      h.cache.rows(C1).map((m) => m.message_id),
      ["a1", "u1", "old-a"],
      "optimistic row replaced by the server rows",
    );

    // The poll lands with the finished answer → auto-open its agent output.
    h.cache.workspace = [{ item_id: "wi1", kind: "agent_search", title: "بحث" }];
    h.engine.noteMessagesPage(C1, {
      messages: [msg({ message_id: "a1", role: "assistant", content: "تم", artifact_ids: ["wi1"] })],
      has_more: false,
    });
    await tick();
    assert.equal(h.store.getState().autoOpen[C1]?.itemId, "wi1");
  });

  test("clean close without any event before message_start → recovery too", async () => {
    const h = harness();
    h.cache.server = async () => ({
      messages: [
        msg({ message_id: "a1", role: "assistant", content: "جاهز" }),
        msg({ message_id: "u1", role: "user", content: "ما حكم الشفعة؟" }),
      ],
      has_more: false,
    });
    const { promise } = await startSend(h);
    h.streams[0].close();
    assert.deepEqual(await promise, { status: "adopted", settled: true });
    assert.equal(h.posts.length, 1);
  });

  test("server does not have it → failed with retry, still one POST", async () => {
    const h = harness();
    h.cache.server = async () => ({ messages: [], has_more: false });
    const { promise, committed } = await startSend(h);
    h.streams[0].fail();
    const result = await promise;
    assert.deepEqual(result, { status: "failed", message: MSG_SEND_FAILED });
    assert.equal(h.cache.rows(C1)[0].isFailed, true);
    assert.equal(h.posts.length, 1);
    assert.equal(committed(), 0);
  });

  test("an identical OLDER message is not adopted (baseline ids)", async () => {
    const h = harness();
    const old = [
      msg({ message_id: "a0", role: "assistant", content: "جواب" }),
      msg({ message_id: "u0", role: "user", content: "ما حكم الشفعة؟" }),
    ];
    h.cache.seed(C1, old);
    h.cache.server = async () => ({ messages: old, has_more: false });
    const { promise } = await startSend(h);
    h.streams[0].fail();
    assert.equal((await promise).status, "failed");
  });

  test("offline during the check: retries the read, adopts when it answers", async () => {
    const h = harness();
    let calls = 0;
    h.cache.server = async () => {
      calls += 1;
      if (calls < 3) throw new Error("offline");
      return {
        messages: [
          msg({ message_id: "a1", role: "assistant", content: "" }),
          msg({ message_id: "u1", role: "user", content: "ما حكم الشفعة؟" }),
        ],
        has_more: false,
      };
    };
    const { promise } = await startSend(h);
    h.streams[0].fail();
    assert.equal((await promise).status, "adopted");
    assert.equal(calls, 3);
    assert.equal(h.posts.length, 1);
  });

  test("network error opening the POST (unknown if sent) → recovery, not failure", async () => {
    const h = harness({
      openStream: async () => {
        throw new ApiError({ kind: "network", status: 0, message: "x" });
      },
    });
    h.cache.server = async () => ({
      messages: [
        msg({ message_id: "a1", role: "assistant", content: "" }),
        msg({ message_id: "u1", role: "user", content: "سؤال" }),
      ],
      has_more: false,
    });
    const result = await h.engine.send({ conversationId: C1, content: "سؤال" });
    assert.equal(result.status, "adopted");
    assert.equal(h.posts.length, 1);
  });
});

describe("send — drop AFTER message_start", () => {
  test("network drop → detached: «ريحان يفكّر» placeholder + refetch, no re-POST, no adopt read", async () => {
    const h = harness();
    const { promise } = await startSend(h);
    const s = h.streams[0];
    s.push(frame("message_start", START));
    s.push(frame("token", { text: "جزء من الجواب" }));
    await tick();
    s.fail();
    const result = await promise;
    assert.deepEqual(result, { status: "detached" });
    const top = h.cache.rows(C1)[0];
    assert.equal(top.message_id, "a1");
    assert.equal(top.content, "", "back to thinking — the run fills it");
    assert.equal(h.cache.count(`fetch:${C1}`), 0);
    assert.equal(h.cache.count(`invalidate:messages:${C1}`), 1);
    assert.equal(h.posts.length, 1);
    assert.equal(h.store.getState().streams[C1], undefined);
  });

  test("idle watchdog (no byte for the timeout) → detached", async () => {
    const h = harness({ idleTimeoutMs: 30 });
    const { promise } = await startSend(h);
    h.streams[0].push(frame("message_start", START));
    const result = await promise;
    assert.deepEqual(result, { status: "detached" });
    assert.ok(h.streams[0].cancelled);
  });

  test("heartbeats keep the watchdog quiet", async () => {
    const h = harness({ idleTimeoutMs: 40 });
    const { promise } = await startSend(h);
    const s = h.streams[0];
    s.push(frame("message_start", START));
    for (let i = 0; i < 4; i++) {
      await new Promise((r) => setTimeout(r, 20));
      s.push(frame("heartbeat", {}));
    }
    s.push(frame("token", { text: "تم" }));
    s.push(frame("done", DONE));
    assert.equal((await promise).status, "done");
  });

  test("AppState active: a silent stream is recovered, a fresh one is left alone", async () => {
    const h = harness({ resumeStaleMs: 10_000 });
    const a = await startSend(h, "أول", C1);
    const b = await startSend(h, "ثان", C2);
    h.streams[0].push(frame("message_start", START));
    await tick();
    h.clock.now += 15_000; // backgrounded
    h.streams[1].push(frame("message_start", { user_message_id: "u2", assistant_message_id: "a2", conversation_id: C2 }));
    await tick();
    h.engine.onAppActive();
    assert.deepEqual(await a.promise, { status: "detached" });
    assert.equal(h.engine.isSending(C2), true);
    h.streams[1].push(frame("done", { ...DONE, message_id: "a2" }));
    assert.equal((await b.promise).status, "done");
  });
});

describe("QA N2 — airplane mode mid-answer never produces a ghost send", () => {
  const Q = "ما هي مدة الإشعار؟";

  /** The conversation as it was opened: an answered turn, nothing running. */
  function seedIdle(h: ReturnType<typeof harness>) {
    h.cache.data.set(C1, {
      pages: [
        {
          messages: [
            msg({ message_id: "a0", role: "assistant", content: "جواب سابق" }),
            msg({ message_id: "u0", role: "user", content: "سؤال سابق" }),
          ],
          has_more: false,
          run_active: false,
        },
      ],
      pageParams: [undefined],
    });
  }

  async function dropMidAnswer(h: ReturnType<typeof harness>) {
    const { promise } = await startSend(h, Q);
    const s = h.streams[0];
    s.push(frame("message_start", START));
    s.push(frame("agent_run_started", { agent_family: "deep_search" }));
    s.push(frame("agent_progress", { stage: "searching", text: "البحث", data: { sources: 7 } }));
    await tick();
    s.fail(); // airplane mode
    return promise;
  }

  function items(h: ReturnType<typeof harness>) {
    const data = h.cache.getMessages(C1);
    const live = h.store.getState().streams[C1];
    return buildChatItems(
      chronological(data),
      { phase: live?.phase ?? null, assistantMessageId: live?.assistantMessageId ?? null },
      data?.pages[0]?.run_active,
      h.clock.now,
    );
  }

  test("the stale `run_active: false` of the last fetch no longer turns the placeholder into a failed bubble", async () => {
    const h = harness();
    seedIdle(h);
    assert.deepEqual(await dropMidAnswer(h), { status: "detached" });

    const list = items(h);
    assert.equal(list[list.length - 1].kind, "thinking", "«ريحان يفكّر», not «تعذّر إكمال الرد» + retry");
    assert.equal(list.filter((i) => i.kind === "failed").length, 0);
    const users = list.filter((i) => i.kind === "user" && i.message.content === Q);
    assert.equal(users.length, 1, "exactly one bubble for the question");
    assert.equal(users[0].kind === "user" && users[0].message.message_id, "u1");
    assert.equal(h.store.getState().errors[C1], undefined, "no failure banner");
    assert.equal(h.posts.length, 1);

    // The poll keeps running (fast cadence) until the network returns.
    const first = h.cache.getMessages(C1)?.pages[0];
    assert.equal(placeholderPollMs(first?.messages[0], first?.run_active, h.clock.now), 4000);
  });

  test("regenerate on the detached placeholder is refused — the server already has that send", async () => {
    const h = harness();
    seedIdle(h);
    await dropMidAnswer(h);
    assert.deepEqual(await h.engine.regenerate(C1, "a1"), { status: "skipped" });
    assert.equal(h.posts.length, 1, "no second POST");
    assert.equal(h.cache.rows(C1).filter((m) => m.role === "user" && m.content === Q).length, 1);
  });

  test("once the server says nothing runs, the dead placeholder's retry re-sends (the run is really gone)", async () => {
    const h = harness();
    seedIdle(h);
    await dropMidAnswer(h);
    h.cache.data.set(C1, {
      pages: [
        {
          messages: [
            msg({ message_id: "a1", role: "assistant", content: "" }),
            msg({ message_id: "u1", role: "user", content: Q }),
          ],
          has_more: false,
          run_active: false,
        },
      ],
      pageParams: [undefined],
    });
    const p = h.engine.regenerate(C1, "a1");
    await tick();
    assert.equal(h.posts.length, 2);
    h.streams[1].push(frame("duplicate", { detail: "مكرر", assistant_message_id: "ax", conversation_id: C1 }));
    await p;
  });

  test("the answer fills in from the poll — no reopen, no extra rows", async () => {
    const h = harness();
    seedIdle(h);
    await dropMidAnswer(h);
    const page = {
      messages: [
        msg({ message_id: "a1", role: "assistant", content: "مدة الإشعار ستون يوماً" }),
        msg({ message_id: "u1", role: "user", content: Q }),
        msg({ message_id: "a0", role: "assistant", content: "جواب سابق" }),
        msg({ message_id: "u0", role: "user", content: "سؤال سابق" }),
      ],
      has_more: false,
      run_active: false,
    };
    h.cache.data.set(C1, { pages: [page], pageParams: [undefined] }); // the refetch
    h.engine.noteMessagesPage(C1, page);
    assert.deepEqual(
      items(h).map((i) => i.kind),
      ["user", "assistant", "user", "assistant"],
    );
  });
});

describe("QA N4 — the tracker survives a dropped / backgrounded stream", () => {
  async function deepSearchUntilDrop(h: ReturnType<typeof harness>, drop: "network" | "resume") {
    const { promise } = await startSend(h);
    const s = h.streams[0];
    s.push(frame("message_start", START));
    s.push(frame("agent_run_started", { agent_family: "deep_search" }));
    s.push(frame("agent_progress", { stage: "planning", text: "تخطيط" }));
    s.push(frame("agent_progress", { stage: "searching", text: "البحث في الأنظمة", data: { sources: 7 } }));
    await tick();
    if (drop === "network") {
      s.fail();
    } else {
      h.clock.now += 60_000; // backgrounded, the OS killed the socket
      h.engine.onAppActive();
    }
    return promise;
  }

  for (const drop of ["network", "resume"] as const) {
    test(`${drop}: the placeholder keeps the last tracker; the answer seals it into the chip`, async () => {
      const h = harness();
      assert.deepEqual(await deepSearchUntilDrop(h, drop), { status: "detached" });
      const state = h.store.getState();
      assert.equal(state.streams[C1], undefined, "live entry gone (the poll takes over)");
      assert.equal(selectRunningAgent(state, C1), "deep_search");
      const progress = selectDeepSearchProgress(state, C1);
      assert.equal(progress?.stage, "searching");
      assert.equal(progress?.sources, 7);

      h.engine.noteMessagesPage(C1, {
        messages: [msg({ message_id: "a1", role: "assistant", content: "الجواب" })],
        has_more: false,
      });
      const after = h.store.getState();
      assert.equal(after.parked[C1], undefined);
      assert.equal(selectDeepSearchProgress(after, C1), null);
      assert.equal(after.summaries.a1?.sources, 7);
    });
  }

  test("Stop parks it too; a new send in the conversation clears it", async () => {
    const h = harness();
    const { promise } = await startSend(h);
    h.streams[0].push(frame("message_start", START));
    h.streams[0].push(frame("agent_run_started", { agent_family: "deep_search" }));
    h.streams[0].push(frame("agent_progress", { stage: "evaluating", text: "تقييم" }));
    await tick();
    h.engine.stop(C1);
    await promise;
    assert.equal(selectDeepSearchProgress(h.store.getState(), C1)?.stage, "evaluating");

    const next = await startSend(h, "سؤال آخر");
    assert.equal(h.store.getState().parked[C1], undefined);
    assert.equal(selectDeepSearchProgress(h.store.getState(), C1), null, "the new send's own (empty) tracker");
    h.streams[1].push(frame("duplicate", { detail: "مكرر", assistant_message_id: "ax", conversation_id: C1 }));
    await next.promise;
  });

  test("a run without an agent parks nothing (generic phrase, as before)", async () => {
    const h = harness();
    const { promise } = await startSend(h);
    h.streams[0].push(frame("message_start", START));
    h.streams[0].push(frame("token", { text: "نص" }));
    await tick();
    h.streams[0].fail();
    await promise;
    assert.equal(h.store.getState().parked[C1], undefined);
    assert.equal(selectRunningAgent(h.store.getState(), C1), null);
  });

  test("a placeholder that disappears (paused turn) drops the parked tracker without a chip", async () => {
    const h = harness();
    await deepSearchUntilDrop(h, "network");
    h.engine.noteMessagesPage(C1, { messages: [], has_more: false });
    assert.equal(h.store.getState().parked[C1], undefined);
    assert.equal(h.store.getState().summaries.a1, undefined);
  });
});

describe("Stop", () => {
  test("after message_start: abort + placeholder + invalidate; partial text not saved", async () => {
    const h = harness();
    const { promise } = await startSend(h);
    const s = h.streams[0];
    s.push(frame("message_start", START));
    s.push(frame("token", { text: "نص جزئي" }));
    await tick();
    h.engine.stop(C1);
    const result = await promise;
    assert.deepEqual(result, { status: "stopped" });
    assert.equal(h.cache.rows(C1)[0].content, "");
    assert.equal(h.cache.count(`invalidate:messages:${C1}`), 1);
    assert.equal(h.posts.length, 1);
  });

  test("during the pre-start recovery wait → stopped, no failure banner", async () => {
    let release!: () => void;
    const h = harness({
      sleep: (_ms, signal) =>
        new Promise((resolve, reject) => {
          release = resolve;
          signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
        }),
    });
    const { promise } = await startSend(h);
    h.streams[0].fail();
    await tick();
    assert.equal(h.store.getState().streams[C1].phase, "recovering");
    h.engine.stop(C1);
    assert.deepEqual(await promise, { status: "stopped" });
    assert.equal(h.store.getState().errors[C1], undefined);
    void release;
  });
});

describe("parallel conversations", () => {
  test("two conversations stream side by side without crossing", async () => {
    const h = harness();
    const a = await startSend(h, "سؤال أ", C1);
    const b = await startSend(h, "سؤال ب", C2);
    const [s1, s2] = h.streams;
    s1.push(frame("message_start", START));
    s2.push(frame("message_start", { user_message_id: "u2", assistant_message_id: "a2", conversation_id: C2 }));
    s1.push(frame("token", { text: "ألف" }));
    s2.push(frame("token", { text: "باء" }));
    await new Promise((r) => setTimeout(r, 20));
    assert.equal(h.store.getState().streams[C1].content, "ألف");
    assert.equal(h.store.getState().streams[C2].content, "باء");

    s1.push(frame("done", DONE));
    assert.equal((await a.promise).status, "done");
    assert.equal(h.store.getState().streams[C1], undefined);
    assert.equal(h.store.getState().streams[C2].content, "باء", "C2 keeps streaming");

    s2.push(frame("token", { text: " تاء" }));
    s2.push(frame("done", { ...DONE, message_id: "a2" }));
    assert.equal((await b.promise).status, "done");
    assert.equal(h.cache.rows(C2)[0].content, "باء تاء");
    assert.equal(h.cache.rows(C1)[0].content, "ألف");
  });

  test("a re-send in the SAME conversation supersedes the first", async () => {
    const h = harness();
    const first = await startSend(h, "الأول");
    const second = await startSend(h, "الثاني");
    assert.deepEqual(await first.promise, { status: "superseded" });
    assert.equal(h.cache.rows(C1).some((m) => m.content === "الأول"), false);
    h.streams[1].push(frame("message_start", START));
    h.streams[1].push(frame("token", { text: "رد" }));
    h.streams[1].push(frame("done", DONE));
    assert.equal((await second.promise).status, "done");
  });
});

describe("auto-open", () => {
  test("agent outputs open once per item; notes / attachments / null kinds never", async () => {
    const h = harness();
    const { promise } = await startSend(h);
    const s = h.streams[0];
    s.push(frame("message_start", START));
    s.push(frame("workspace_item_created", { item_id: "n1", kind: "note", title: "ملاحظة", created_by: "agent" }));
    s.push(`event: workspace_item_created\ndata: {"item_id":"x1","kind":null,"title":"","created_by":null}\n\n`);
    s.push(frame("workspace_item_created", { item_id: "wi1", kind: "agent_search", title: "بحث", created_by: "agent" }));
    await tick();
    const first = h.store.getState().autoOpen[C1];
    assert.equal(first?.itemId, "wi1");
    h.store.consumeAutoOpen(C1, "wi1");

    s.push(frame("workspace_item_created", { item_id: "wi1", kind: "agent_search", title: "بحث", created_by: "agent" }));
    s.push(frame("token", { text: "نص" }));
    s.push(frame("done", { ...DONE, artifact_ids: ["wi1"] }));
    await promise;
    assert.equal(h.store.getState().autoOpen[C1], undefined, "same item never re-opens");

    // A later recovery/refetch of the same turn does not reopen it either.
    h.cache.workspace = [{ item_id: "wi1", kind: "agent_search", title: "بحث" }];
    h.engine.noteMessagesPage(C1, {
      messages: [msg({ message_id: "a1", role: "assistant", content: "نص", artifact_ids: ["wi1"] })],
      has_more: false,
    });
    await tick();
    assert.equal(h.store.getState().autoOpen[C1], undefined);
  });

  test("agent_writing opens too", async () => {
    const h = harness();
    const { promise } = await startSend(h);
    const s = h.streams[0];
    s.push(frame("message_start", START));
    s.push(frame("workspace_item_created", { item_id: "d1", kind: "agent_writing", title: "مذكرة", created_by: "agent" }));
    s.push(frame("done", DONE));
    await promise;
    assert.deepEqual(h.store.getState().autoOpen[C1], { itemId: "d1", kind: "agent_writing", title: "مذكرة", seq: 1 });
  });
});

describe("workspace item events", () => {
  test("updated / locked / unlocked refresh the list AND the item's detail", async () => {
    const h = harness();
    const { promise } = await startSend(h);
    const s = h.streams[0];
    s.push(frame("message_start", START));
    s.push(frame("workspace_item_locked", { item_id: "d1", locked_until: "2026-10-15T10:05:00Z" }));
    s.push(frame("workspace_item_updated", { item_id: "d1" }));
    s.push(frame("workspace_item_unlocked", { item_id: "d1" }));
    s.push(frame("workspace_item_updated", { item_id: "d2" }));
    s.push(frame("done", DONE));
    await promise;
    assert.equal(h.cache.count("invalidate:item:d1"), 3);
    assert.equal(h.cache.count("invalidate:item:d2"), 1);
    assert.ok(h.cache.count(`invalidate:workspace:${C1}`) >= 4);
  });
});

describe("retry / regenerate", () => {
  test("retry removes the failed row and sends its text as a new POST", async () => {
    const h = harness();
    h.cache.seed(C1, [msg({ message_id: "f1", role: "user", content: "أعد", isFailed: true })]);
    const p = h.engine.retry(C1, "f1");
    await tick();
    assert.equal(h.cache.rows(C1).some((m) => m.message_id === "f1"), false);
    assert.equal(h.posts[0].content, "أعد");
    h.streams[0].push(frame("quota_exceeded", { meter: "ord", period: "daily", used: 1, limit: 1, resets_at: "", message_ar: "حد" }));
    await p;
  });

  test("retry refused by the server keeps the failed row — its text is never lost (QA F4)", async () => {
    const h = harness();
    h.cache.seed(C1, [msg({ message_id: "f1", role: "user", content: "نص لا يضيع", isFailed: true })]);
    const p = h.engine.retry(C1, "f1");
    await tick();
    h.streams[0].push(frame("quota_exceeded", { meter: "ord", period: "monthly", used: 6, limit: 5, resets_at: "", message_ar: "حد" }));
    const result = await p;
    assert.equal(result.status, "refused");
    // The composer may hold newer text: nothing is restored there…
    assert.equal(result.status === "refused" && result.restoreText, null);
    // …the failed bubble comes back instead (once — the optimistic retry row is gone).
    const users = h.cache.rows(C1).filter((m) => m.role === "user");
    assert.equal(users.length, 1);
    assert.equal(users[0].message_id, "f1");
    assert.equal(users[0].content, "نص لا يضيع");
    assert.equal(users[0].isFailed, true);
    assert.equal(users[0].isOptimistic, false);
    assert.deepEqual(h.store.getState().notices[C1]?.kind, "quota");
  });

  test("retry refused by the parallel cap also keeps the failed row", async () => {
    const h = harness();
    h.cache.seed(C1, [msg({ message_id: "f1", role: "user", content: "نص ثان", isFailed: true })]);
    const p = h.engine.retry(C1, "f1");
    await tick();
    h.streams[0].push(frame("parallel_limit", { detail: "حد التوازي", limit: 1, running: 1, running_conversation_ids: [], upgrade_plan: null }));
    const result = await p;
    assert.equal(result.status === "refused" && result.reason, "parallel_limit");
    const users = h.cache.rows(C1).filter((m) => m.role === "user");
    assert.deepEqual(
      users.map((m) => [m.message_id, m.content, m.isFailed]),
      [["f1", "نص ثان", true]],
    );
  });

  test("retry answered «duplicate» does not resurrect the failed row (the server has it)", async () => {
    const h = harness();
    h.cache.seed(C1, [msg({ message_id: "f1", role: "user", content: "مكرر", isFailed: true })]);
    const p = h.engine.retry(C1, "f1");
    await tick();
    h.streams[0].push(frame("duplicate", { assistant_message_id: "a0", conversation_id: C1, detail: "رسالة مكررة" }));
    const result = await p;
    assert.equal(result.status === "refused" && result.reason, "duplicate");
    assert.equal(h.cache.rows(C1).some((m) => m.message_id === "f1"), false);
  });

  test("regenerate refused → no text restored (the question is still listed)", async () => {
    const h = harness();
    h.cache.seed(C1, [
      msg({ message_id: "a0", role: "assistant", content: "جواب" }),
      msg({ message_id: "u0", role: "user", content: "السؤال الأصلي" }),
    ]);
    const p = h.engine.regenerate(C1, "a0");
    await tick();
    h.streams[0].push(frame("quota_exceeded", { meter: "ord", period: "monthly", used: 6, limit: 5, resets_at: "", message_ar: "حد" }));
    const result = await p;
    assert.equal(result.status === "refused" && result.restoreText, null);
  });

  test("regenerate re-sends the preceding user message", async () => {
    const h = harness();
    h.cache.seed(C1, [
      msg({ message_id: "a0", role: "assistant", content: "جواب" }),
      msg({ message_id: "u0", role: "user", content: "السؤال الأصلي" }),
    ]);
    const p = h.engine.regenerate(C1, "a0");
    await tick();
    assert.equal(h.posts[0].content, "السؤال الأصلي");
    h.streams[0].push(frame("parallel_limit", { detail: "حد", limit: 1, running: 1, running_conversation_ids: [], upgrade_plan: null }));
    await p;
  });
});

test("reset aborts in-flight sends and clears state", async () => {
  const h = harness();
  const { promise } = await startSend(h);
  h.streams[0].push(frame("message_start", START));
  await tick();
  h.engine.reset();
  assert.equal((await promise).status, "superseded");
  assert.deepEqual(h.store.getState().streams, {});
  assert.equal(h.engine.isSending(C1), false);
});
