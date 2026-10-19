/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  ANDROID_DOCUMENTS_URI,
  parseLastFolder,
  pickerStartUri,
  serializeLastFolder,
  treeToDocumentUri,
} from "../saf";

const TREE = "content://com.android.externalstorage.documents/tree/primary%3ADocuments%2FRayhan";

describe("treeToDocumentUri", () => {
  test("tree URI → document-under-tree URI", () => {
    assert.equal(
      treeToDocumentUri(TREE),
      "content://com.android.externalstorage.documents/tree/primary%3ADocuments%2FRayhan/document/primary%3ADocuments%2FRayhan",
    );
  });

  test("other providers keep their authority", () => {
    assert.equal(
      treeToDocumentUri("content://com.android.providers.downloads.documents/tree/downloads"),
      "content://com.android.providers.downloads.documents/tree/downloads/document/downloads",
    );
  });

  test("non-tree URIs → null", () => {
    assert.equal(treeToDocumentUri(ANDROID_DOCUMENTS_URI), null);
    assert.equal(treeToDocumentUri("file:///data/user/0/x/cache"), null);
    assert.equal(treeToDocumentUri(""), null);
  });
});

describe("pickerStartUri", () => {
  test("no previous folder → Documents (selectable on Android 11+, unlike Download)", () => {
    assert.equal(pickerStartUri(null), ANDROID_DOCUMENTS_URI);
  });

  test("previous folder → that folder", () => {
    assert.equal(pickerStartUri(TREE), treeToDocumentUri(TREE));
  });

  test("unusable previous value → Documents", () => {
    assert.equal(pickerStartUri("garbage"), ANDROID_DOCUMENTS_URI);
  });
});

describe("last-folder record", () => {
  test("round-trips", () => {
    assert.equal(parseLastFolder(serializeLastFolder(TREE)), TREE);
  });

  test("malformed → null", () => {
    assert.equal(parseLastFolder("not json"), null);
    assert.equal(parseLastFolder("null"), null);
    assert.equal(parseLastFolder('{"treeUri":42}'), null);
    assert.equal(parseLastFolder('{"treeUri":"file:///x"}'), null);
  });
});
