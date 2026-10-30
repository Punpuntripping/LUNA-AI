/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { ApiError, errorFromBody, errorMessage, isWiPreviewLockedError } from "../errors";

describe("WI preview lock errors (402 WI_PREVIEW_LOCKED)", () => {
  const detail = "هذا المستند معروض معاينةً في الباقة المجانية، وتعديل نصّه متاح في الباقات المدفوعة.";

  test("recognised from the server envelope; its Arabic message is what the user sees", () => {
    const err = errorFromBody(402, { error: { code: "WI_PREVIEW_LOCKED", message: detail } }, null);
    assert.equal(isWiPreviewLockedError(err), true);
    assert.equal(errorMessage(err), detail);
  });

  test("other refusals and non-API errors are not the preview lock", () => {
    assert.equal(isWiPreviewLockedError(errorFromBody(402, { error: { code: "LIBRARY_QUOTA_EXCEEDED", message: detail } }, null)), false);
    assert.equal(isWiPreviewLockedError(new ApiError({ kind: "network", status: 0, message: "تعذّر" })), false);
    assert.equal(isWiPreviewLockedError(new Error("WI_PREVIEW_LOCKED")), false);
    assert.equal(isWiPreviewLockedError(null), false);
  });
});
