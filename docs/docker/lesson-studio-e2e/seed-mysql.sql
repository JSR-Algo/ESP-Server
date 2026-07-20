INSERT INTO sys_user (
  id, username, password, super_admin, status, create_date, update_date
) VALUES (
  9000001,
  'lesson_admin_e2e',
  '$2a$12$SJJm6JQLREIyOd.p7NZ.8eOZwmL8CcRFkxd7a.aaDOOSJNqqsEjPS',
  1,
  1,
  NOW(),
  NOW()
)
ON DUPLICATE KEY UPDATE
  password = VALUES(password),
  super_admin = VALUES(super_admin),
  status = VALUES(status),
  update_date = NOW();
