-- T5.3 / F-T53-09 — give the fixture a curriculum lesson so a generation index can exist.
--
-- WHY THIS FILE IS NEEDED
--
-- `POST /v1/admin/lesson-assignments` refuses to assign a lesson whose exact pack is not in
-- `lesson_asset_generations` (409 ASSET_PACK_NOT_READY). The only supported way to populate that
-- table is `npm run lesson:generation-rebuild`, whose validator demands BOTH:
--     * at least one curriculum lesson (`--expected-curriculum-count=0` is a usage error), and
--     * the canonical demo present as a demo (`--expected-demo-key`).
--
-- The lesson-studio seed ships exactly ONE lesson — `w01-d01-barn-say-it`, demo-scoped through
-- `courses.asset_sync_scope`. It can satisfy one requirement or the other, never both, so no
-- index was buildable and the assign step was unreachable.
--
-- The self-healing route does not apply either: assignment auto-bootstrap
-- (`shouldBootstrapAssignmentMaterialization`) explicitly skips `CANONICAL_DEMO_LESSON_KEY`, and
-- separately requires `manifest_version = teebot-lesson-renderer.v4` while the seeded demo is v1.
--
-- So the fixture needs a second, curriculum-scoped lesson. This adds the smallest real one: its
-- own course (`asset_sync_scope='curriculum'`), a published lessons row, an espTft asset bundle
-- whose `manifest_checksum` matches the lesson's, and a full asset set.
--
-- The assets deliberately reuse the demo bundle's `path` + `sha256` values. Those bytes really
-- are served by the stack's nginx at /tvideo-demo, so preload and checksum attestation see real
-- media rather than a 404 or an HTML shell. Only the ids differ.

BEGIN;

INSERT INTO courses (id, course_key, title, locale, age_band, status, asset_sync_scope)
VALUES (
  '00000006-0101-4000-8000-000000000001',
  'w01-farm-curriculum',
  'Farm Words (curriculum)',
  'en-US',
  '4-6',
  'published',
  'curriculum'
)
ON CONFLICT (id) DO UPDATE SET
  status = 'published',
  asset_sync_scope = 'curriculum';

INSERT INTO lessons (
  id, course_id, lesson_key, lesson_version, manifest_version, title, locale, age_band,
  manifest_checksum, status, published_at, lesson_type
) VALUES (
  '00000006-0102-4000-8000-000000000001',
  '00000006-0101-4000-8000-000000000001',
  'w01-d02-farm-curriculum',
  1,
  'teebot-lesson-renderer.v1',
  'On the Farm',
  'en-US',
  '4-6',
  -- Distinct from the demo's checksum: two lessons sharing one checksum would collide in the
  -- pack index (cacheKey is lessonKey/vN-<checksum>, but the manifest identity must still differ).
  'aa11bb22cc33dd44ee55ff6677889900aabbccddeeff00112233445566778899',
  'published',
  NOW(),
  'lesson'
)
ON CONFLICT (id) DO UPDATE SET
  status = 'published',
  manifest_checksum = EXCLUDED.manifest_checksum,
  updated_at = NOW();

-- The bundle checksum must equal the lesson's: discovery selects both and the pack builder
-- treats them as one manifest identity.
INSERT INTO asset_bundles (id, lesson_id, lesson_version, profile, manifest_checksum)
VALUES (
  '00000006-0103-4000-8000-000000000001',
  '00000006-0102-4000-8000-000000000001',
  1,
  'espTft',
  'aa11bb22cc33dd44ee55ff6677889900aabbccddeeff00112233445566778899'
)
ON CONFLICT (id) DO UPDATE SET manifest_checksum = EXCLUDED.manifest_checksum;

INSERT INTO assets (
  id, bundle_id, asset_key, layer, role, path, sha256,
  is_critical, media_type, bytes, width, height
) VALUES
  ('00000006-0104-4000-8000-000000000001', '00000006-0103-4000-8000-000000000001',
   'backgroundScene.poster', 'backgroundScene', 'poster',
   'assets/background/barn-round-field-poster.jpg',
   'd5cdaba9f9086ef56a5f41c5fddf2e32b91ecfe141cc346f3221c7b221a3a357',
   TRUE, 'image/jpeg', 18482, 320, 180),
  ('00000006-0104-4000-8000-000000000002', '00000006-0103-4000-8000-000000000001',
   'teachingObject.barn', 'teachingObject', 'primarySubject',
   'assets/objects/barn.png',
   'bf3d88d17867f02872e3e6aff31b5d4d0a94977a5efa4214b7e831122938511b',
   TRUE, 'image/png', 42107, 192, 192),
  ('00000006-0104-4000-8000-000000000003', '00000006-0103-4000-8000-000000000001',
   'teachingObject.farm', 'teachingObject', 'supportSubject',
   'assets/objects/farm.png',
   'e84ac5ae133e6bfa5cdcca88fe2c6d91e30b6a7f952adcc9bbf29c60c5ab8138',
   FALSE, 'image/png', 38451, 192, 192),
  ('00000006-0104-4000-8000-000000000004', '00000006-0103-4000-8000-000000000001',
   'robotOverlay.teach', 'robotOverlay', 'pose',
   'assets/robot/poses/bright-teach.png',
   '576d86a75686f6eab606295529593da14b01554e21e0601c8f29aedbc1ba4965',
   FALSE, 'image/png', 45408, 186, 192),
  ('00000006-0104-4000-8000-000000000005', '00000006-0103-4000-8000-000000000001',
   'robotOverlay.listening', 'robotOverlay', 'pose',
   'assets/robot/poses/bright-listening.png',
   '572a61f140eca17968a85f61704967d03a1a3311222335e32b94b1ab370e2419',
   FALSE, 'image/png', 43615, 169, 192),
  ('00000006-0104-4000-8000-000000000006', '00000006-0103-4000-8000-000000000001',
   'robotOverlay.celebrate', 'robotOverlay', 'pose',
   'assets/robot/poses/bright-celebrate.png',
   '8392fb31c53030147d27fbd96c5b2dd1a4e5c33efd35f8727bee6dabda62605d',
   FALSE, 'image/png', 44193, 192, 190)
ON CONFLICT (id) DO NOTHING;

COMMIT;
