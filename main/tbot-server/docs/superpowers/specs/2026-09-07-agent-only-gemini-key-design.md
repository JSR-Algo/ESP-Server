# Agent-only Gemini credentials

Status: behavior approved; written specification awaiting user review.

## Scope and decision

The user requested that Gemini credentials come only from the current agent's
Role Config on the admin service. The user approved failing explicitly when
that key is missing instead of falling back to another credential.

The only runtime source is `google_live.api_key` returned for the current
agent. This policy applies to Google Live and Gemini ASR, LLM, TTS and VLLM
modules served by the ESP server. Non-Gemini credentials are unchanged.

Removing only the environment override is insufficient: the client resolver,
legacy module migration and TTS override can independently select another key.
Removing deployment environment variables alone is also insufficient because a
later deployment could restore them. Enforce the rule in the runtime code.

## Data flow and boundaries

1. Fetch the current agent's private configuration through the existing admin
   client. Do not use a global/base configuration credential when the agent
   response omits its key or the configuration fetch fails.
2. Normalize and validate the explicit agent key without resolving environment
   expressions. Reject empty values, non-string values and placeholder values.
3. Propagate that key to the agent's Gemini modules. Module-local historical
   keys must not survive when the canonical agent key is missing.
4. Construct the Google Live client with the explicit validated key. Do not let
   the credential resolver or SDK infer credentials from its environment.

Remove credential selection from all four Live/Gemini environment aliases,
`TBOT_GEMINI_TTS_API_KEY`, and legacy module-key migration in the affected
runtime path. Preserve unrelated environment settings.

Do not cache credentials across agents. A new connection must fetch the current
agent configuration through the existing lifecycle. This change does not add
hot rotation of an already-open Google Live session.

## Error handling and compatibility

Missing or invalid agent credentials prevent Gemini client initialization with
a clear, sanitized configuration error. Never include key bytes, raw private
configuration, prompts or user speech in diagnostics. Do not retry a missing
credential indefinitely or silently switch credential sources.

Preserve model, voice, language, prompt, conversation, lesson, interruption and
provider fallback policies. This change removes credential fallback only; it
must not introduce a different provider fallback policy.

Global server bootstrap must remain possible before any agent configuration is
available. Enforce the missing-key error at the agent/provider boundary, not
by requiring a global key during startup.

Environment-only installations will need an agent Role Config key. Standalone
test tools may accept explicit synthetic/test credentials, but may not weaken
the production runtime's agent-only rule.

## Verification

Write failing regression tests before implementation for:

- An agent key wins despite every credential environment alias being set.
- Missing, blank, placeholder or environment-template agent keys never use an
  environment, base-config, module-local or TTS-specific key.
- Two agents with distinct keys remain isolated; missing configuration for one
  never inherits the other's credential.
- A new connection observes an updated agent configuration.
- All affected Gemini modules receive the canonical agent key; non-Gemini
  credentials and non-credential voice settings remain unchanged.
- Missing credentials fail before the SDK connects, without exposing secrets.
- Server bootstrap without an agent key remains valid.

Run focused config-loader, credential/client, Gemini provider and connection
regressions, then relevant Google Live conversation/lesson tests. Update stale
tests and documentation that intentionally describe the removed fallback.
Report exact results and any evidence-manifest mismatch separately; do not
relax release verification to make it pass.

## Delivery boundaries

This scope is a local code/test/documentation change, not authorization to
deploy, restart production, alter admin credentials, reset or flash the robot.
After separately authorized deployment, verify the actual server/robot
identities and run attended real Google Live E2E. Local test success alone is
not production-ready evidence and does not prove the new key is accepted by
Google.
