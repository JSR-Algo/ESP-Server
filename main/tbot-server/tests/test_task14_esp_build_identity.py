from websockets.datastructures import Headers

ELF = "a" * 64
APP = "b" * 64


def pairs(app=APP):
    return [
        ("x-tbot-build-schema", "1"),
        ("x-tbot-hil-profile", "task14-hil-v1"),
        ("x-tbot-project-name", "xiaozhi"),
        ("x-tbot-project-version", "2.2.75"),
        ("x-tbot-idf-version", "v5.4.1"),
        ("x-tbot-secure-version", "0"),
        ("x-tbot-elf-sha256", ELF),
        ("x-tbot-app-sha256", app),
        ("x-tbot-build-id", f"tbot-esp-v1:{ELF}"),
    ]


def test_esp_identity_requires_exact_single_headers_and_full_typed_allowlist():
    from core.lesson.esp_build_identity import evaluate_esp_build_identity, parse_esp_build_identity

    parsed = parse_esp_build_identity(Headers(pairs()))
    assert parsed.status == "valid"
    approved = evaluate_esp_build_identity(Headers(pairs()), [parsed.identity])
    assert approved.status == "approved"
    assert approved.identity_id.startswith("tbot-esp-approved-v1:")

    duplicate = parse_esp_build_identity(Headers(pairs() + [("X-TBOT-APP-SHA256", APP)]))
    assert duplicate.status == "rejected"
    assert duplicate.reason == "duplicate_header"


def test_esp_identity_fails_closed_for_missing_malformed_or_unapproved_records():
    from core.lesson.esp_build_identity import evaluate_esp_build_identity, parse_esp_build_identity

    assert parse_esp_build_identity(Headers()).status == "missing"
    parsed = parse_esp_build_identity(Headers(pairs()))
    malformed = dict(parsed.identity)
    malformed["secureVersion"] = False
    assert evaluate_esp_build_identity(Headers(pairs()), [malformed]).status == "unapproved"
    assert evaluate_esp_build_identity(Headers(pairs("c" * 64)), [parsed.identity]).status == "unapproved"


def test_esp_metrics_never_expose_identity_bytes():
    from core.lesson.esp_build_identity import esp_build_identity_metrics_fields, parse_esp_build_identity

    parsed = parse_esp_build_identity(Headers(pairs()))
    assert esp_build_identity_metrics_fields(Headers(pairs()), [parsed.identity]) == {
        "buildIdentityStatus": "approved"
    }
