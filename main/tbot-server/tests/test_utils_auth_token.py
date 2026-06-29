import json
import time

import jwt

from core.utils.auth import AuthToken


def test_auth_token_round_trips_device_id():
    auth = AuthToken("secret")

    token = auth.generate_token("device-1")

    assert auth.verify_token(token) == (True, "device-1")


def test_auth_token_rejects_expired_inner_payload(monkeypatch):
    auth = AuthToken("secret")
    monkeypatch.setattr(time, "time", lambda: 1000.0)
    encrypted = auth._encrypt_payload({"device_id": "device-1", "exp": 999.0})
    token = jwt.encode({"data": encrypted}, auth.secret_key, algorithm="HS256")

    assert auth.verify_token(token) == (False, None)


def test_auth_token_rejects_invalid_jwt_and_bad_json(monkeypatch):
    auth = AuthToken("secret")

    assert auth.verify_token("not-a-token") == (False, None)

    token = jwt.encode({"data": "encrypted"}, auth.secret_key, algorithm="HS256")

    def bad_json(_encrypted):
        raise json.JSONDecodeError("bad", "{", 0)

    monkeypatch.setattr(auth, "_decrypt_payload", bad_json)
    assert auth.verify_token(token) == (False, None)


def test_auth_token_generic_decode_errors_return_false(capsys):
    auth = AuthToken("secret")
    token = jwt.encode({"missing": "data"}, auth.secret_key, algorithm="HS256")

    assert auth.verify_token(token) == (False, None)
    assert "Token verification failed" in capsys.readouterr().out
