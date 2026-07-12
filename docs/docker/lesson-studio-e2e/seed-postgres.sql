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

COMMIT;
