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
