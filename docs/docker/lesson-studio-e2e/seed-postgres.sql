BEGIN;

INSERT INTO admin_users (
  id, email, password_hash, role, status, mfa_enabled, can_author_lessons
) VALUES (
  '11111111-1111-4111-8111-111111111111',
  'lesson-author-e2e@local.invalid',
  '$argon2id$v=19$m=65536,t=3,p=4$buMrj1xp5v2IigA10QPNYg$g7JEM+lwBYKk1SfuIAxk5q169z7fW8OWyxO5YFZK4qo',
  'super_admin',
  'active',
  false,
  true
)
ON CONFLICT (email) DO UPDATE SET
  password_hash = EXCLUDED.password_hash,
  role = EXCLUDED.role,
  status = EXCLUDED.status,
  mfa_enabled = EXCLUDED.mfa_enabled,
  can_author_lessons = EXCLUDED.can_author_lessons,
  updated_at = NOW();

INSERT INTO admin_role_assignments (
  admin_user_id, role, status, granted_by_admin_id, reason
)
SELECT id, 'super_admin', 'active', id, 'Lesson Studio local E2E fixture'
FROM admin_users
WHERE email = 'lesson-author-e2e@local.invalid'
ON CONFLICT (admin_user_id, role) WHERE status = 'active'
DO UPDATE SET
  granted_by_admin_id = EXCLUDED.granted_by_admin_id,
  reason = EXCLUDED.reason,
  updated_at = NOW();

INSERT INTO admin_users (
  id, email, password_hash, role, status, mfa_enabled, can_author_lessons
) VALUES (
  '11111111-1111-4111-8111-111111111112',
  'lesson-manager-e2e@local.invalid',
  '$argon2id$v=19$m=65536,t=3,p=4$buMrj1xp5v2IigA10QPNYg$g7JEM+lwBYKk1SfuIAxk5q169z7fW8OWyxO5YFZK4qo',
  'support',
  'active',
  false,
  true
)
ON CONFLICT (email) DO UPDATE SET
  password_hash = EXCLUDED.password_hash,
  role = EXCLUDED.role,
  status = EXCLUDED.status,
  mfa_enabled = EXCLUDED.mfa_enabled,
  can_author_lessons = EXCLUDED.can_author_lessons,
  updated_at = NOW();

INSERT INTO admin_role_assignments (
  admin_user_id, role, status, granted_by_admin_id, reason
)
SELECT id, 'support_agent', 'active', '11111111-1111-4111-8111-111111111111'::uuid,
       'Lesson Studio manager-denial E2E fixture'
FROM admin_users
WHERE email = 'lesson-manager-e2e@local.invalid'
ON CONFLICT (admin_user_id, role) WHERE status = 'active'
DO UPDATE SET
  granted_by_admin_id = EXCLUDED.granted_by_admin_id,
  reason = EXCLUDED.reason,
  updated_at = NOW();

INSERT INTO admin_users (
  id, email, password_hash, role, status, mfa_enabled, can_author_lessons
) VALUES (
  '11111111-1111-4111-8111-111111111113',
  'lesson-author-b-e2e@local.invalid',
  '$argon2id$v=19$m=65536,t=3,p=4$buMrj1xp5v2IigA10QPNYg$g7JEM+lwBYKk1SfuIAxk5q169z7fW8OWyxO5YFZK4qo',
  'support',
  'active',
  false,
  true
)
ON CONFLICT (email) DO UPDATE SET
  password_hash = EXCLUDED.password_hash,
  role = EXCLUDED.role,
  status = EXCLUDED.status,
  mfa_enabled = EXCLUDED.mfa_enabled,
  can_author_lessons = EXCLUDED.can_author_lessons,
  updated_at = NOW();

INSERT INTO admin_role_assignments (
  admin_user_id, role, status, granted_by_admin_id, reason
)
SELECT id, 'support_agent', 'active', '11111111-1111-4111-8111-111111111111'::uuid,
       'Lesson Studio second-author concurrency fixture'
FROM admin_users
WHERE email = 'lesson-author-b-e2e@local.invalid'
ON CONFLICT (admin_user_id, role) WHERE status = 'active'
DO UPDATE SET
  granted_by_admin_id = EXCLUDED.granted_by_admin_id,
  reason = EXCLUDED.reason,
  updated_at = NOW();

-- Make the canonical source lesson explicitly owned so the manager denial is
-- a resource-ownership check, rather than an incidental NULL-owner failure.
UPDATE lessons
   SET created_by = '11111111-1111-4111-8111-111111111111'::uuid,
       updated_at = NOW()
 WHERE id = '00000006-0002-0000-0000-000000000001'::uuid;

-- This manager-owned draft deliberately has no Course Mode contract. A 404 on
-- this lesson proves the same principal is authorized before the target 403.
INSERT INTO courses (
  id, course_key, title, locale, age_band, status, created_by
) VALUES (
  '00000006-0099-4000-8000-000000000001',
  'lesson-manager-e2e-owned',
  'Manager-owned authorization fixture',
  'en-US',
  '4-6',
  'draft',
  '11111111-1111-4111-8111-111111111112'
)
ON CONFLICT (id) DO UPDATE SET
  status = 'draft',
  created_by = EXCLUDED.created_by,
  updated_at = NOW();

INSERT INTO lessons (
  id, course_id, lesson_key, lesson_version, manifest_version, title, locale, age_band,
  manifest_checksum, status, created_by, lesson_type, estimated_duration_sec
) VALUES (
  '00000006-0099-4000-8000-000000000002',
  '00000006-0099-4000-8000-000000000001',
  'lesson-manager-e2e-owned-draft',
  1,
  'teebot-lesson-renderer.v5',
  'Manager-owned draft without Course Mode',
  'en-US',
  '4-6',
  'pending-course-mode',
  'draft',
  '11111111-1111-4111-8111-111111111112',
  'lesson',
  480
)
ON CONFLICT (id) DO UPDATE SET
  status = 'draft',
  created_by = EXCLUDED.created_by,
  updated_at = NOW();

-- Real tvideo response visuals are source assets for the browser round-trip.
-- The E2E lesson attaches them through the public authoring API, exactly like
-- an existing production asset, while Nginx serves the pinned bytes read-only.
INSERT INTO asset_bundles (id, lesson_id, lesson_version, profile)
VALUES (
  '00000006-0016-4000-8000-000000000001',
  '00000006-0016-4000-8000-000000000002',
  1,
  'espTft'
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO assets (
  id, bundle_id, asset_key, layer, role, path, sha256,
  is_critical, media_type, bytes, width, height
) VALUES
  (
    '00000006-0016-4000-8000-000000000011',
    '00000006-0016-4000-8000-000000000001',
    'feedback.correct.star', 'robotOverlay', 'pose',
    'esp-tft/robots-bright-alive-k3-glowface-192.png',
    '4e2f33a3eada6222b814bb226042e614fcd81f876efa42327b5c2196d1caa9c4',
    false, 'image/png', 32268, 140, 192
  ),
  (
    '00000006-0016-4000-8000-000000000012',
    '00000006-0016-4000-8000-000000000001',
    'feedback.near-miss.spark', 'robotOverlay', 'pose',
    'esp-tft/robots-bright-alive-k3-glowface-192.png',
    '4e2f33a3eada6222b814bb226042e614fcd81f876efa42327b5c2196d1caa9c4',
    false, 'image/png', 32268, 140, 192
  ),
  (
    '00000006-0016-4000-8000-000000000013',
    '00000006-0016-4000-8000-000000000001',
    'feedback.incorrect.try-again', 'robotOverlay', 'pose',
    'esp-tft/robots-bright-alive-k3-glowface-192.png',
    '4e2f33a3eada6222b814bb226042e614fcd81f876efa42327b5c2196d1caa9c4',
    false, 'image/png', 32268, 140, 192
  ),
  (
    '00000006-0016-4000-8000-000000000014',
    '00000006-0016-4000-8000-000000000001',
    'ending.farm.parade', 'robotOverlay', 'pose',
    'esp-tft/robots-bright-alive-k3-glowface-192.png',
    '4e2f33a3eada6222b814bb226042e614fcd81f876efa42327b5c2196d1caa9c4',
    false, 'image/png', 32268, 140, 192
  )
ON CONFLICT (id) DO UPDATE SET
  asset_key = EXCLUDED.asset_key,
  layer = EXCLUDED.layer,
  role = EXCLUDED.role,
  path = EXCLUDED.path,
  sha256 = EXCLUDED.sha256,
  is_critical = EXCLUDED.is_critical,
  media_type = EXCLUDED.media_type,
  bytes = EXCLUDED.bytes,
  width = EXCLUDED.width,
  height = EXCLUDED.height;

COMMIT;
