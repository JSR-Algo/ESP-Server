import json

import pytest
from websockets.datastructures import Headers

ELF = "a" * 64
APP = "b" * 64


def pairs():
    return [
        ("x-tbot-build-schema", "1"),
        ("x-tbot-hil-profile", "task14-hil-v1"),
        ("x-tbot-project-name", "xiaozhi"),
        ("x-tbot-project-version", "2.2.75"),
        ("x-tbot-idf-version", "v5.4.1"),
        ("x-tbot-secure-version", "0"),
        ("x-tbot-elf-sha256", ELF),
        ("x-tbot-app-sha256", APP),
        ("x-tbot-build-id", f"tbot-esp-v1:{ELF}"),
    ]


def identity():
    from core.lesson.esp_build_identity import parse_esp_build_identity

    return parse_esp_build_identity(Headers(pairs())).identity


@pytest.mark.parametrize("tool", ["self.lesson_assets.hil.status", "self.lesson_assets.hil.inspect"])
def test_esp_read_only_hil_result_receives_exact_attestation(tool):
    from core.api.device_mcp_admin_handler import _attest_hil_result

    rendered = _attest_hil_result(tool, '{"status":"idle"}', Headers(pairs()), [identity()], "conn-1")
    value = json.loads(rendered)
    assert value["identitySchemaVersion"] == 1
    assert value["buildIdentity"] == identity()
    assert value["buildIdentityId"].startswith("tbot-esp-approved-v1:")
    assert value["connectionBindingId"] == "conn-1"


def test_esp_attestation_rejects_pre_attested_or_unapproved_results():
    from core.api.device_mcp_admin_handler import _attest_hil_result

    with pytest.raises(ValueError):
        _attest_hil_result(
            "self.lesson_assets.hil.inspect",
            {"buildIdentity": {}},
            Headers(pairs()),
            [identity()],
            "conn-1",
        )
    with pytest.raises(ValueError, match="unapproved"):
        _attest_hil_result(
            "self.lesson_assets.hil.inspect", {}, Headers(pairs()), [], "conn-1"
        )


def test_esp_mutation_strips_and_validates_preinspection_bindings():
    from core.api.device_mcp_admin_handler import _prepare_hil_mutation_args
    from core.lesson.esp_build_identity import approved_build_identity_id

    approved = identity()
    args = {
        "cacheKey": "hil-task14/v1-" + "a" * 64,
        "expectedBuildIdentityId": approved_build_identity_id(approved),
        "expectedConnectionBindingId": "conn-1",
    }
    forwarded = _prepare_hil_mutation_args(
        "self.lesson_assets.hil.stage_fixture", args, Headers(pairs()), [approved], "conn-1"
    )
    assert forwarded == {"cacheKey": args["cacheKey"]}
    with pytest.raises(ValueError, match="connection changed"):
        _prepare_hil_mutation_args(
            "self.lesson_assets.hil.cleanup_fixture", args, Headers(pairs()), [approved], "conn-2"
        )
