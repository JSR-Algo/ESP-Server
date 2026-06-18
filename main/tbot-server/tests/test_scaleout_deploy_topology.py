from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_prod_compose_exposes_redis_to_python_ws_replicas():
    compose = (REPO_ROOT / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8")

    assert "REDIS_URL:" in compose
    assert "redis://" in compose
    assert "tbot-esp32-server-redis" in compose


def test_prod_compose_fronts_ws_with_hash_affinity_lb_and_multiple_replicas():
    compose = (REPO_ROOT / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    haproxy = (REPO_ROOT / "deploy" / "haproxy.cfg").read_text(encoding="utf-8")

    assert "tbot-wss-lb:" in compose
    assert "TBOT_SERVER_REPLICAS" in compose
    assert "replicas:" in compose
    assert "balance hdr(device-id)" in haproxy
    assert "hash-type consistent" in haproxy
    assert "server-template tbot" in haproxy
