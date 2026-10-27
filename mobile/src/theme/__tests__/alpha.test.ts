/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { withAlpha } from "../alpha";

describe("withAlpha", () => {
  test("a hex token becomes rgba at the given opacity", () => {
    assert.equal(withAlpha("#4A6B5F", 0.05), "rgba(74, 107, 95, 0.05)");
    assert.equal(withAlpha("#5e9385", 0.6), "rgba(94, 147, 133, 0.6)");
  });

  test("anything else is returned unchanged", () => {
    assert.equal(withAlpha("rgba(74, 107, 95, 0.08)", 0.5), "rgba(74, 107, 95, 0.08)");
    assert.equal(withAlpha("#FFF", 0.5), "#FFF");
    assert.equal(withAlpha("transparent", 0.5), "transparent");
  });
});
