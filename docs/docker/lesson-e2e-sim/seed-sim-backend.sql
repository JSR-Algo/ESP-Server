-- T5.3 — provision the simulated robot on the BACKEND side (Postgres).
--
-- Device provisioning is split across two databases and BOTH are required:
--   * manager-api (MySQL) — ai_device + ai_agent, or the ESP server refuses the
--     WebSocket (see seed-sim-device.sql);
--   * backend (Postgres)  — a `devices` row whose mac_address is linked to a
--     household, or POST /v1/internal/devices/mint-token answers 404
--     DEVICE_NOT_LINKED and the lesson dies at "backend identity unavailable".
--
-- The lesson-studio seed provisions neither, so a stack brought up from it alone
-- can never run a lesson end to end.

BEGIN;

-- Parent -> household -> child: the chain `devices.current_household_id` needs.
INSERT INTO parent_accounts (id, email, password_hash, name, coppa_verified)
VALUES (
  '22222222-2222-4222-8222-222222222222',
  'e2e-sim-parent@example.invalid',
  -- Never used for login on this path; the mint leg only needs the FK chain.
  'x-not-a-real-hash-e2e-sim',
  'E2E Sim Parent',
  TRUE
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO households (id, name, owner_id)
VALUES (
  '33333333-3333-4333-8333-333333333333',
  'E2E Sim Household',
  '22222222-2222-4222-8222-222222222222'
)
ON CONFLICT (id) DO NOTHING;

-- Owning the household is not enough: POST /v1/admin/lesson-assignments rejects with
-- 404 "Robot household has no active parent membership" unless the parent is also an
-- explicit member.
INSERT INTO household_memberships (parent_id, household_id, role)
VALUES (
  '22222222-2222-4222-8222-222222222222',
  '33333333-3333-4333-8333-333333333333',
  'owner'
)
ON CONFLICT (parent_id, household_id) DO NOTHING;

INSERT INTO child_profiles (id, household_id, display_name, birth_year, age_gate_passed)
VALUES (
  '44444444-4444-4444-8444-444444444444',
  '33333333-3333-4333-8333-333333333333',
  'Mai',
  2020,
  TRUE
)
ON CONFLICT (id) DO NOTHING;

-- The MAC must match LESSON_SIM_DEVICE_ID and the manager-api ai_device row.
INSERT INTO devices (
  id, serial_number, hardware_revision, mac_address,
  current_household_id, assigned_child_profile_id,
  state, lifecycle_state, status, display_name
)
VALUES (
  '55555555-5555-4555-8555-555555555555',
  'E2E-SIM-0001',
  'lcdwiki-es3c35p',
  '14:c1:9f:d1:a8:48',
  '33333333-3333-4333-8333-333333333333',
  '44444444-4444-4444-8444-444444444444',
  'ACTIVE', 'assigned', 'active',
  'E2E Sim Robot'
)
ON CONFLICT (id) DO UPDATE SET
  mac_address = EXCLUDED.mac_address,
  current_household_id = EXCLUDED.current_household_id,
  assigned_child_profile_id = EXCLUDED.assigned_child_profile_id,
  updated_at = now();

COMMIT;
