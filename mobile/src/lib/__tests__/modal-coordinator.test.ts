/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { createModalCoordinator, MODAL_SETTLE_MS } from "../modal-coordinator";

function fakeClock() {
  let t = 1_000;
  const pending: { at: number; fn: () => void; id: number }[] = [];
  let nextId = 1;
  return {
    now: () => t,
    timers: {
      set: (fn: () => void, ms: number) => {
        const id = nextId++;
        pending.push({ at: t + ms, fn, id });
        return id;
      },
      clear: (h: unknown) => {
        const i = pending.findIndex((p) => p.id === h);
        if (i >= 0) pending.splice(i, 1);
      },
    },
    advance(ms: number) {
      t += ms;
      for (;;) {
        pending.sort((a, b) => a.at - b.at);
        const due = pending[0];
        if (!due || due.at > t) break;
        pending.shift();
        due.fn();
      }
    },
  };
}

describe("modalCoordinator", () => {
  test("runs at once when no modal was ever shown", () => {
    const clock = fakeClock();
    const c = createModalCoordinator(clock.now, clock.timers);
    let ran = 0;
    c.whenSettled(() => ran++);
    assert.equal(ran, 1);
  });

  test("waits while another modal is open, then for its dismissal to settle", () => {
    const clock = fakeClock();
    const c = createModalCoordinator(clock.now, clock.timers);
    const release = c.acquire();
    let ran = 0;
    c.whenSettled(() => ran++);
    clock.advance(1_000);
    assert.equal(ran, 0);
    release();
    clock.advance(MODAL_SETTLE_MS - 1);
    assert.equal(ran, 0);
    clock.advance(200);
    assert.equal(ran, 1);
  });

  test("a recent dismissal delays; cancel stops it", () => {
    const clock = fakeClock();
    const c = createModalCoordinator(clock.now, clock.timers);
    c.acquire()();
    let ran = 0;
    const cancel = c.whenSettled(() => ran++);
    cancel();
    clock.advance(MODAL_SETTLE_MS * 2);
    assert.equal(ran, 0);
  });

  test("release is idempotent", () => {
    const clock = fakeClock();
    const c = createModalCoordinator(clock.now, clock.timers);
    const a = c.acquire();
    const b = c.acquire();
    a();
    a();
    assert.ok(c.msUntilSettled() > 0, "b is still open");
    b();
    clock.advance(MODAL_SETTLE_MS);
    assert.equal(c.msUntilSettled(), 0);
  });

  test("a full-screen sheet blocks whenClear but never whenSettled", () => {
    const clock = fakeClock();
    const c = createModalCoordinator(clock.now, clock.timers);
    const release = c.acquireSheet();
    let settled = 0;
    let clear = 0;
    c.whenSettled(() => settled++);
    c.whenClear(() => clear++);
    assert.equal(settled, 1, "actions inside the sheet still run");
    clock.advance(1_000);
    assert.equal(clear, 0);
    release();
    release();
    clock.advance(MODAL_SETTLE_MS - 1);
    assert.equal(clear, 0, "waits for the sheet's dismissal to settle");
    clock.advance(200);
    assert.equal(clear, 1);
  });

  test("whenClear also waits for ordinary modals", () => {
    const clock = fakeClock();
    const c = createModalCoordinator(clock.now, clock.timers);
    const release = c.acquire();
    let clear = 0;
    const cancel = c.whenClear(() => clear++);
    clock.advance(1_000);
    assert.equal(clear, 0);
    release();
    clock.advance(MODAL_SETTLE_MS + 200);
    assert.equal(clear, 1);
    cancel();
  });

  test("subscribers see openCount changes on acquire and release", () => {
    const c = createModalCoordinator();
    const seen: number[] = [];
    const unsubscribe = c.subscribe(() => seen.push(c.openCount()));
    const release = c.acquire();
    release();
    release();
    unsubscribe();
    c.acquire();
    assert.deepEqual(seen, [1, 0]);
  });
});
