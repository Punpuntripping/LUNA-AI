# Angle map UI — «الأوجه» inside «الملفات»

**Status:** rev 2 (rows map) APPROVED 2026-11-23; route + hook + wiring BUILT and real-data tested
2026-11-24 (§4) — all UNCOMMITTED in the worktree. Next: commit / deploy / switch on for the test
account, on the owner's go (§4.5). Nothing deployed. Worktree `C:\Programming\LUNA_AI_angle_ui`,
branch `feat/angle-map-ui`, on origin/master `39b20b4` (fast-forwarded 11-24).

**Related:** `relationship_map.md` (the map itself — angles-v5, migration 228 applied on prod; deployed
11-23 with the mapper switch ON for the test account only — first real map stored 13:33 UTC, 2 docs,
8 angles), `my_files.md` («الملفات»: `/files`, the pane's «الملفات» tab, `FileViewer`),
`extended_search.md` (the map's first consumer, phase 2).

## 1. Owner decisions (2026-11-23)

| # | Decision |
|---|---|
| U1 | The map lives in an opened file — the chat pane's file view AND `/files/<id>` (both are `FileViewer`) — as a tab «الأوجه» beside «الأقسام». Inspired by `relmap_2026-11-21\preview_v4\angle_map_preview.html`. |
| U2 | Visual map + cards: the message's documents side by side, each a column of sections; the angle drawn across them; the angle cards below. |
| U3 | Test account first (flag + user allowlist), then widen. |
| U4 | `/files`: a file in several maps (one per conversation it was sent in) shows its own conversation's map, with a picker «من محادثة: …» when there are 2+. |
| U5 | Shown on a card besides title / argument / «سؤال البحث» / sections: «ما يضعفه», the strength label, the gaps, the client-side line. «قد يكون خطأ قراءة» always shows on OCR-suspect angles. |
| U6 | "Build it internally first, I will check it then agree or add modifications" — a local preview on real map data before any route, flag or deploy. |
| U7 | Rev 1 map "not clear" → "the other option, simplify it": the mock-up's rows-across-documents layout (§2). |
| U8 | Rev 2 approved as it is: rows map, then cards, gaps, client line, «من محادثة» picker. |

## 2. What was built (internal)

- `frontend/lib/contracts/angles.ts` — the proposed wire shape (`AngleMap`, `FileAngleMapsResponse`),
  Arabic labels (the preview page's wording), pure helpers (`parseAngleRef`, `angleMapColumns`,
  `angleMapRows`, `pickAngleMap`…). Tests `lib/contracts/__tests__/angles.test.ts` (8 pass, `npx tsx --test`).
- `frontend/components/files/angles/` — `FileAngles` (tab body: picker, client box, counts, map,
  cards, gaps, left-out files), `AngleMapDiagram`, `AngleCard`, `AngleSectionDialog`, `copy.ts`,
  `angle-style.ts` (theme status families: يقوّي = success, يطعن = error, يجب الرد = warning).
- `FileViewer` — optional `angleMaps` / `initialTab` props; with maps, the outline becomes two tabs.
  No caller passes them yet → the product is unchanged.
- Dev-only harness `/dev-preview/angle-map` (+ `fixture/[name]` route, 127.0.0.1 only, 404 outside
  `next dev`, env `ANGLE_MAP_FIXTURE_DIR`).
- `scripts/angle_map_ui_fixture.py` — preview JSON → fixtures (labels computed with the backend's own
  `angle_mapper.labels`); refuses `--out` inside a repo. Fixtures (27 real groups, customer data):
  `C:\Programming\LUNA_AI_test_results\angle_map_ui\fixtures\`.

**Map design (rev 2, owner 2026-11-23: "it's not clear, we need the other option, simplify it"):**
the mock-up's layout — documents are the columns, each angle is a ROW; a row's cells hold the numbers
of the angle's sections in each document; one straight line in the angle's colour (يقوّي green /
يطعن red / يجب الرد amber) runs along the row from the first document it reaches to the last. Tap a
number → the section's text; tap a row's title → its card. A cell shows at most 6 numbers, then «+n»
(→ the card lists them all). Single-document maps: no document header, no line — each row is the
angle and its section numbers. Rev 1 (bar + branches per selected angle, chips row, importance
badges, «الكل» overview) was dropped as unclear. Also simplified: shorter intro, no counts line,
no row is highlighted until tapped.

## 3. Run the preview

```
cd C:\Programming\LUNA_AI_angle_ui\frontend
set "ANGLE_MAP_FIXTURE_DIR=C:\Programming\LUNA_AI_test_results\angle_map_ui\fixtures"
npx next dev -H 127.0.0.1 -p 3101
```
Open `http://127.0.0.1:3101/dev-preview/angle-map` — pick a group, the open file, the view (chat pane /
files page / phone), «محادثة ثانية» (a made-up second map to see the picker), light/dark.

## 4. Wiring (owner "go" 2026-11-24 for: route + connect + real test)

1. ~~Apply the owner's modifications to the preview.~~ Done — rev 2 approved (U8).
2. ~~Backend read route~~ BUILT 11-24 (uncommitted): `GET /files/{item_id}/angle-maps` →
   `FileAngleMapsResponse` in `backend/app/api/files.py`, logic in
   `backend/app/services/angle_maps_service.py`: the file → the caller's live conversation items of
   its `file_id` (just the item when it links none; a file id also accepted with the read switch on)
   → each item's map through the mapper's own `load_fresh_angle_map_for_item` (a stale map is NOT
   shown — its section numbers may no longer match) → sections via the segmenter's
   `load_segment_maps`, labels via `angle_mapper.labels` → only the sections angles/gaps use, with
   text; documents' title / type / WI via `with_file_id` + `overlay_files`; conversation title
   (`conversations.title_ar`); newest first. 404 not the caller's file; `{maps: []}` no fresh map;
   FEATURE_OFF 404 when the UI switch is off. Switch `ANGLE_MAP_UI_ENABLED` / `ANGLE_MAP_UI_USERS`
   (`shared/config.py`), separate from the mapper's own `ANGLE_MAP_*` (who SEES vs who gets maps
   BUILT); `angle_maps` added to `GET /files/status` (only true with «الملفات» on).
   Tests `backend/tests/test_file_angle_maps.py` (14, in the CI list); `test_files_service.py`
   status assertion updated; call-site registry entry added (`shared/files/tests/...call_sites.py`).
3. ~~Hook + connect~~ BUILT 11-24: `filesApi.angleMaps`, `useFilesStatus().angleMaps`,
   `useFileAngleMaps(itemId)` (its own FEATURE_OFF handling turns off the TAB only, never «الملفات»);
   `FileViewer` asks for the maps itself when no `angleMaps` prop is given → the chat pane and
   `/files/<id>` both get the tab with no caller change; the dev preview still passes fixtures.
4. ~~Real test~~ DONE 11-24, locally and read-only: the files router alone (no app lifespan — the
   full backend starts the prod scheduler, so it was NOT run against prod), real service client, the
   test account (enterprise@, user 32c10d30…) as caller, UI switch on for it only. Its one map
   (953e4e1b: 2 documents, 8 angles, 6 gaps) came back from BOTH of its files; every ref resolved;
   16 sections sent, none empty; labels own 23 / neutral 17 / opp 3; a file with no map → `[]`;
   another user's mapped file → 404. Bug found + fixed: conversations has `title_ar`, not `title`.
   The real responses render in the preview as group `real0test` (fixtures folder; local only).
5. NOT DONE — needs the owner's go: commit (the dev preview + fixture script as a separate commit
   that can be dropped, like my-files), push, deploy in a quiet window (a deploy kills in-flight
   searches), then on luna-backend `ANGLE_MAP_UI_ENABLED=true` + `ANGLE_MAP_UI_USERS=<test account>`,
   check in the real app, then widen. NOTE: the mapper is live for every paying user and the
   Pro/Max backfill is done (≈138 maps on 11-24) — widening the UI switch shows the tab to all of them.
6. Mobile — the app must match the web (owner rule); not started.

## 5. Open for the owner

- The «الأوجه» copy (all first drafts — `components/files/angles/copy.ts`), esp. the intro line.
- Default tab when a file has a map: «الأقسام» (today's view) or «الأوجه».
- A count/badge on `/files` cards and pane rows for files that have a map.
- An action on a card («ابحث في هذا الوجه») — belongs to `extended_search.md` phase 2.
