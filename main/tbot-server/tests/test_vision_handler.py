import base64
import json

import pytest

from core.api import vision_handler as vision_module
from core.api.vision_handler import MAX_FILE_SIZE, VisionHandler


class _Logger:
    def __init__(self):
        self.messages = []

    def bind(self, **_kwargs):
        return self

    def debug(self, message):
        self.messages.append(("debug", message))

    def error(self, message):
        self.messages.append(("error", message))


class _Field:
    def __init__(self, *, text=None, data=None):
        self._text = text
        self._data = data

    async def text(self):
        return self._text

    async def read(self):
        return self._data


class _Reader:
    def __init__(self, fields):
        self.fields = list(fields)

    async def next(self):
        if not self.fields:
            return None
        return self.fields.pop(0)


class _Request:
    def __init__(self, *, headers=None, fields=None, multipart_error=None):
        self.headers = headers or {}
        self._fields = fields or []
        self._multipart_error = multipart_error

    async def multipart(self):
        if self._multipart_error is not None:
            raise self._multipart_error
        return _Reader(self._fields)


class _Vllm:
    def __init__(self):
        self.calls = []

    def response(self, question, image_base64):
        self.calls.append((question, image_base64))
        return "vision answer"


def _config(**overrides):
    cfg = {
        "server": {"auth_key": "secret", "vision_explain": "https://vision.test/explain"},
        "selected_module": {"VLLM": "demo"},
        "VLLM": {"demo": {"type": "demo_type", "model": "v"}},
        "read_config_from_api": False,
    }
    cfg.update(overrides)
    return cfg


def _handler(config=None, *, auth=(True, "dev1")):
    handler = VisionHandler(config or _config())
    handler.logger = _Logger()
    handler.auth.verify_token = lambda _token: auth
    return handler


def _headers(device_id="dev1", client_id="client1", auth="Bearer token"):
    return {"Authorization": auth, "Device-Id": device_id, "Client-Id": client_id}


def _fields(image=b"image-bytes", question="what is this?"):
    return [_Field(text=question), _Field(data=image)]


def _json_body(response):
    return json.loads(response.text)


@pytest.mark.asyncio
async def test_vision_post_success_uses_private_config_and_vllm(monkeypatch):
    vllm = _Vllm()
    private_config = _config(
        read_config_from_api=True,
        selected_module={"VLLM": "private_demo"},
        VLLM={"private_demo": {"model": "private"}},
    )
    handler = _handler(_config(read_config_from_api=True))

    async def fake_private_config(config, device_id, client_id):
        assert config is not handler.config
        assert (device_id, client_id) == ("dev1", "client1")
        return private_config

    monkeypatch.setattr(vision_module, "get_private_config_from_api", fake_private_config)
    monkeypatch.setattr(vision_module, "is_valid_image_file", lambda data: data == b"image-bytes")
    monkeypatch.setattr(
        vision_module,
        "create_instance",
        lambda vllm_type, cfg: (assert_vllm := (vllm_type, cfg)) and vllm,
    )

    response = await handler.handle_post(_Request(headers=_headers(), fields=_fields()))

    body = _json_body(response)
    assert response.status == 200
    assert body == {"success": True, "action": "RESPONSE", "response": "vision answer"}
    assert vllm.calls == [("what is this?", base64.b64encode(b"image-bytes").decode("utf-8"))]
    assert response.headers["Access-Control-Allow-Origin"] == "*"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fake_request", "auth", "message"),
    [
        (_Request(headers=_headers(auth="Token bad"), fields=_fields()), (True, "dev1"), "Invalid auth token or token expired"),
        (_Request(headers=_headers(), fields=_fields()), (False, None), "Invalid auth token or token expired"),
        (_Request(headers=_headers(device_id="other"), fields=_fields()), (True, "dev1"), "Device ID and token do not match"),
        (_Request(headers=_headers(), fields=[]), (True, "dev1"), "Missing question field"),
        (_Request(headers=_headers(), fields=[_Field(text="q")]), (True, "dev1"), "Missing image file"),
        (_Request(headers=_headers(), fields=_fields(image=b"")), (True, "dev1"), "Image data empty"),
        (_Request(headers=_headers(), fields=_fields(image=b"x" * (MAX_FILE_SIZE + 1))), (True, "dev1"), "Image size exceeds limit"),
    ],
)
async def test_vision_post_validation_errors_return_json(monkeypatch, fake_request, auth, message):
    handler = _handler(auth=auth)
    monkeypatch.setattr(vision_module, "is_valid_image_file", lambda _data: True)

    response = await handler.handle_post(fake_request)

    body = _json_body(response)
    assert body["success"] is False
    assert message in body["message"]
    assert response.headers["Access-Control-Allow-Headers"] == "client-id, content-type, device-id, authorization"


@pytest.mark.asyncio
async def test_vision_post_rejects_unsupported_image(monkeypatch):
    handler = _handler()
    monkeypatch.setattr(vision_module, "is_valid_image_file", lambda _data: False)

    response = await handler.handle_post(_Request(headers=_headers(), fields=_fields()))

    assert _json_body(response) == {
        "success": False,
        "message": "Unsupported file format. Upload valid image file (supports JPEG, PNG, GIF, BMP, TIFF, WEBP)",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "message"),
    [
        (_config(selected_module={}), "You have not set default visual analysis module"),
        (_config(selected_module="bad"), "You have not set default visual analysis module"),
        (_config(VLLM={"demo": {"type": ""}}), "Cannot find provider corresponding to VLLM module"),
    ],
)
async def test_vision_post_rejects_missing_vllm_configuration(monkeypatch, config, message):
    handler = _handler(config)
    monkeypatch.setattr(vision_module, "is_valid_image_file", lambda _data: True)

    response = await handler.handle_post(_Request(headers=_headers(), fields=_fields()))

    assert message in _json_body(response)["message"]


@pytest.mark.asyncio
async def test_vision_post_generic_exception_returns_safe_error(monkeypatch):
    handler = _handler()
    monkeypatch.setattr(vision_module, "is_valid_image_file", lambda _data: True)
    monkeypatch.setattr(vision_module, "create_instance", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    response = await handler.handle_post(_Request(headers=_headers(), fields=_fields()))

    assert _json_body(response) == {
        "success": False,
        "message": "Error occurred while handling request",
    }


@pytest.mark.asyncio
async def test_vision_get_reports_configured_and_missing_url(monkeypatch):
    handler = _handler()
    monkeypatch.setattr(vision_module, "get_vision_url", lambda _config: "https://vision.test/explain")

    response = await handler.handle_get(_Request())
    assert response.text == "MCP Vision API running normally, vision explanation API address: https://vision.test/explain"
    assert response.headers["Access-Control-Allow-Origin"] == "*"

    monkeypatch.setattr(vision_module, "get_vision_url", lambda _config: "null")
    response = await handler.handle_get(_Request())
    assert response.text.startswith("MCP Vision API not working")


@pytest.mark.asyncio
async def test_vision_get_generic_exception_returns_json_error(monkeypatch):
    handler = _handler()

    def raise_error(_config):
        raise RuntimeError("boom")

    monkeypatch.setattr(vision_module, "get_vision_url", raise_error)

    response = await handler.handle_get(_Request())

    assert _json_body(response) == {"success": False, "message": "Server internal error"}
    assert response.headers["Access-Control-Allow-Origin"] == "*"
