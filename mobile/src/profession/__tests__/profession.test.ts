/// <reference types="node" />
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, test } from "node:test";

import type { ProfessionAnswer } from "@contracts";

import { PROFESSION_GROUPS, STEP_PROFESSION } from "../content";
import {
  chipsFor,
  finishProfession,
  initialStep,
  isOtherActive,
  pickChip,
  pickDecline,
  pickGroup,
  pickOther,
  prefsGate,
  professionHold,
  professionToSave,
  shouldAutoOpen,
  typeOther,
  type AutoOpenInput,
  type ProfessionStepState,
} from "../logic";
import { useProfessionPrompt } from "../store";

const READY: AutoOpenInput = { prefsReady: true, professionGroup: null, reaskDone: false, resolvedThisSession: false };

function step(group: ProfessionStepState["selection"]["group"], label: string | null = null, otherOpen = false): ProfessionStepState {
  return { selection: { group, label }, otherOpen };
}

describe("prefsGate (web isHydrated / professionReaskDone)", () => {
  test("pending → not ready; the flag reads done (fail-closed default)", () => {
    assert.deepEqual(prefsGate("pending", undefined), { ready: false, reaskDone: true });
  });

  test("a failed read is ready and never re-asks", () => {
    assert.deepEqual(prefsGate("error", { profession_reask_done: false }), { ready: true, reaskDone: true });
  });

  test("a successful read: only an explicit true closes the re-ask", () => {
    assert.deepEqual(prefsGate("success", { profession_reask_done: true }), { ready: true, reaskDone: true });
    assert.deepEqual(prefsGate("success", {}), { ready: true, reaskDone: false });
    // A wrong-typed value off the wire (JSONB is free-form) is not "done".
    assert.deepEqual(prefsGate("success", JSON.parse('{"profession_reask_done":"true"}')), { ready: true, reaskDone: false });
    assert.deepEqual(prefsGate("success", undefined), { ready: true, reaskDone: false });
  });
});

describe("shouldAutoOpen", () => {
  test("never asked (exactly null) opens once the preferences read settled", () => {
    assert.equal(shouldAutoOpen(READY), true);
    assert.equal(shouldAutoOpen({ ...READY, prefsReady: false }), false);
    // …even when the re-ask flag is set: the column IS the gate.
    assert.equal(shouldAutoOpen({ ...READY, reaskDone: true }), true);
  });

  test("a stored «declined» is asked once more while the re-ask flag is unset", () => {
    assert.equal(shouldAutoOpen({ ...READY, professionGroup: "declined" }), true);
    assert.equal(shouldAutoOpen({ ...READY, professionGroup: "declined", reaskDone: true }), false);
  });

  test("an answer, the 'unknown' sentinel and a missing field never prompt", () => {
    for (const g of ["legal", "entrepreneur", "specialist", "individual", "unknown", undefined]) {
      assert.equal(shouldAutoOpen({ ...READY, professionGroup: g }), false, String(g));
    }
  });

  test("never twice in one session (a failed save + a later /auth/me must not reopen it)", () => {
    assert.equal(shouldAutoOpen({ ...READY, resolvedThisSession: true }), false);
    assert.equal(shouldAutoOpen({ ...READY, professionGroup: "declined", resolvedThisSession: true }), false);
  });
});

describe("professionHold (other popups stand down)", () => {
  test("while the question is open, always", () => {
    assert.equal(professionHold({ ...READY, professionGroup: "legal", isOpen: true }), true);
  });

  test("while it may still open (preferences not read yet)", () => {
    assert.equal(professionHold({ ...READY, prefsReady: false, isOpen: false }), true);
    assert.equal(professionHold({ ...READY, prefsReady: false, professionGroup: "declined", isOpen: false }), true);
    assert.equal(professionHold({ ...READY, prefsReady: false, professionGroup: "legal", isOpen: false }), false);
  });

  test("released once it is decided not to open, or resolved", () => {
    assert.equal(professionHold({ ...READY, professionGroup: "declined", reaskDone: true, isOpen: false }), false);
    assert.equal(professionHold({ ...READY, resolvedThisSession: true, isOpen: false }), false);
    assert.equal(professionHold({ ...READY, isOpen: false }), true);
  });
});

describe("initialStep (stored answer pre-selected)", () => {
  test("a stored answer and its label", () => {
    assert.deepEqual(initialStep({ profession_group: "specialist", profession_label: "محاسب" }), step("specialist", "محاسب"));
    assert.deepEqual(initialStep({ profession_group: "declined", profession_label: null }), step("declined"));
  });

  test("null, 'unknown', garbage and no user start untouched", () => {
    for (const user of [
      { profession_group: null },
      { profession_group: "unknown", profession_label: "x" },
      { profession_group: "admin" },
      {},
      null,
      undefined,
    ]) {
      assert.deepEqual(initialStep(user), step(null), JSON.stringify(user));
    }
  });
});

describe("professionToSave (every exit saves)", () => {
  test("an untouched step on a never-asked account records «declined»", () => {
    assert.deepEqual(professionToSave(step(null).selection, { profession_group: null }), { group: "declined", label: null });
  });

  test("an untouched step on an answered / unknown account saves nothing", () => {
    assert.equal(professionToSave(step(null).selection, { profession_group: "legal" }), null);
    assert.equal(professionToSave(step(null).selection, { profession_group: "unknown" }), null);
    assert.equal(professionToSave(step(null).selection, {}), null);
    assert.equal(professionToSave(step(null).selection, null), null);
  });

  test("a new pick is saved; the label is trimmed and blank → null", () => {
    assert.deepEqual(professionToSave(step("legal").selection, { profession_group: null }), { group: "legal", label: null });
    assert.deepEqual(professionToSave(step("individual", "  سائق  ").selection, { profession_group: "individual" }), {
      group: "individual",
      label: "سائق",
    });
    assert.deepEqual(professionToSave(step("specialist", "   ").selection, { profession_group: "legal" }), {
      group: "specialist",
      label: null,
    });
  });

  test("an unchanged answer is not re-saved (the «تغيير المهنة» reopen)", () => {
    assert.equal(
      professionToSave(step("specialist", "محاسب").selection, { profession_group: "specialist", profession_label: "محاسب" }),
      null,
    );
    assert.equal(professionToSave(step("legal").selection, { profession_group: "legal", profession_label: null }), null);
    assert.equal(professionToSave(step("declined").selection, { profession_group: "declined" }), null);
  });

  test("a label-only change is saved", () => {
    assert.deepEqual(
      professionToSave(step("specialist", "مهندس").selection, { profession_group: "specialist", profession_label: "محاسب" }),
      { group: "specialist", label: "مهندس" },
    );
  });
});

describe("finishProfession", () => {
  function run(selection: ProfessionStepState["selection"], user: { profession_group?: string | null }) {
    const calls: string[] = [];
    const saved: [ProfessionAnswer, string | null][] = [];
    const result = finishProfession({
      selection,
      user,
      save: (group, label) => {
        calls.push("save");
        saved.push([group, label]);
        return Promise.resolve(true);
      },
      markReaskDone: () => {
        calls.push("reask");
        return Promise.resolve();
      },
      close: () => calls.push("close"),
    });
    return { calls, saved, result };
  }

  test("dismissing an untouched step: «declined» saved, re-ask closed, then closed", async () => {
    const { calls, saved, result } = run(step(null).selection, { profession_group: null });
    assert.deepEqual(calls, ["save", "reask", "close"]);
    assert.deepEqual(saved, [["declined", null]]);
    assert.equal(await result, true);
  });

  test("nothing to save still closes the re-ask and the sheet", () => {
    const { calls, result } = run(step("legal").selection, { profession_group: "legal" });
    assert.deepEqual(calls, ["reask", "close"]);
    assert.equal(result, null);
  });

  test("the declined re-ask answered with a pick", () => {
    const { calls, saved } = run(step("entrepreneur").selection, { profession_group: "declined" });
    assert.deepEqual(calls, ["save", "reask", "close"]);
    assert.deepEqual(saved, [["entrepreneur", null]]);
  });
});

describe("step taps (web StepProfession)", () => {
  test("chips exist only for مختص and فرد", () => {
    assert.equal(chipsFor("legal"), undefined);
    assert.equal(chipsFor("entrepreneur"), undefined);
    assert.equal(chipsFor("declined"), undefined);
    assert.equal(chipsFor(null), undefined);
    assert.equal(chipsFor("specialist")?.length, 5);
    assert.equal(chipsFor("individual")?.length, 5);
  });

  test("picking a group resets the label and «أخرى»; the same group is a no-op", () => {
    const s = step("specialist", "محاسب", true);
    assert.equal(pickGroup(s, "specialist"), s);
    assert.deepEqual(pickGroup(s, "individual"), step("individual"));
  });

  test("«أفضل عدم الإجابة» is a recorded answer", () => {
    assert.deepEqual(pickDecline(), step("declined"));
  });

  test("a chip toggles; tapping the selected chip clears it", () => {
    const picked = pickChip(step("individual"), "متقاعد");
    assert.deepEqual(picked, step("individual", "متقاعد"));
    assert.deepEqual(pickChip(picked, "متقاعد"), step("individual"));
    assert.deepEqual(pickChip(step("individual", "x", true), "طالب"), step("individual", "طالب"));
  });

  test("«أخرى» opens the field with an empty label; typing keeps it open", () => {
    const other = pickOther(step("specialist", "محاسب"));
    assert.deepEqual(other, step("specialist", null, true));
    assert.equal(isOtherActive(other), true);
    const typed = typeOther(other, "مدقق");
    assert.deepEqual(typed, step("specialist", "مدقق", true));
    assert.deepEqual(typeOther(typed, ""), step("specialist", null, true));
    assert.equal(isOtherActive(typeOther(typed, "")), true);
  });

  test("a stored free-text label shows as «أخرى»; a chip label does not", () => {
    assert.equal(isOtherActive(step("individual", "سائق")), true);
    assert.equal(isOtherActive(step("individual", "طالب")), false);
    assert.equal(isOtherActive(step("individual")), false);
    assert.equal(isOtherActive(step("legal", "سائق", true)), false);
  });
});

describe("store", () => {
  test("open bumps once, resolve closes for the session, reset clears it", () => {
    const store = useProfessionPrompt;
    store.getState().reset();
    const before = store.getState().openCount;
    store.getState().open();
    store.getState().open();
    assert.equal(store.getState().isOpen, true);
    assert.equal(store.getState().openCount, before + 1);
    store.getState().resolve();
    assert.equal(store.getState().isOpen, false);
    assert.equal(store.getState().resolvedThisSession, true);
    store.getState().reset();
    assert.equal(store.getState().resolvedThisSession, false);
  });
});

describe("copy parity with the web step", () => {
  const webFile = resolve(__dirname, "../../../../frontend/components/onboarding/onboarding-content.ts");
  const ARABIC = /[؀-ۿ]/;

  test("every Arabic string matches onboarding-content.ts byte-for-byte, in order", { skip: !existsSync(webFile) }, () => {
    const web = readFileSync(webFile, "utf8");
    const slice = web.slice(web.indexOf("export const PROFESSION_GROUPS"), web.indexOf("export const CORPUS_STATS"));
    assert.ok(slice.length > 0, "anchors not found in the web file");
    const webStrings = [...slice.matchAll(/"([^"]*)"/g)].map((m) => m[1]).filter((s) => ARABIC.test(s));
    const appStrings = [
      ...PROFESSION_GROUPS.flatMap((g) => [g.label, g.hint, ...(g.options ?? [])]),
      ...Object.values(STEP_PROFESSION),
    ];
    assert.deepEqual(appStrings, webStrings);
    const webKeys = [...slice.matchAll(/key: "(\w+)"/g)].map((m) => m[1]);
    assert.deepEqual(
      PROFESSION_GROUPS.map((g) => g.key),
      webKeys,
    );
  });
});
