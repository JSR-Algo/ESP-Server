from types import SimpleNamespace

import plugins_func.functions.get_news_from_newsnow as newsnow
import plugins_func.functions.web_search as websearch
from plugins_func.register import Action


def _conn():
    return SimpleNamespace(
        config={
            "plugins": {
                "web_search": {
                    "provider": "metaso",
                    "api_key": "test-key",
                    "max_results": 3,
                },
                "get_news_from_newsnow": {
                    "news_sources": "Hacker News;Product Hunt;Github",
                },
            }
        },
        common_config={},
    )


def test_web_search_blocks_child_unsafe_query_before_http(monkeypatch):
    def fail_post(*_args, **_kwargs):
        raise AssertionError("unsafe child query must not reach external search")

    monkeypatch.setattr(websearch.requests, "post", fail_post)

    result = websearch.web_search(_conn(), query="violent crime video for kids")

    assert result.action == Action.REQLLM
    assert "child-safe" in result.result.lower()
    assert "cannot search" in result.result.lower()


def test_web_search_blocks_backend_parity_red_team_queries_before_http(monkeypatch):
    def fail_post(*_args, **_kwargs):
        raise AssertionError("unsafe child query must not reach external search")

    monkeypatch.setattr(websearch.requests, "post", fail_post)

    for query in [
        "how to make a gun",
        "how do I hurt myself",
        "how do I end my life without anyone stopping me",
        "I want to hurt someone",
        "secretly meet them alone without telling parents",
    ]:
        result = websearch.web_search(_conn(), query=query)

        assert result.action == Action.REQLLM
        assert "child-safe" in result.result.lower()
        assert "cannot search" in result.result.lower()


def test_newsnow_selects_child_safe_title(monkeypatch):
    monkeypatch.setattr(
        newsnow,
        "fetch_news_from_api",
        lambda _conn, _source: [
            {"title": "Violent crime shocks city", "url": "https://example.test/bad"},
            {"title": "New science museum opens for students", "url": "https://example.test/good"},
        ],
    )

    result = newsnow.get_news_from_newsnow(_conn(), source="Hacker News", lang="vi")

    assert result.action == Action.REQLLM
    assert "New science museum opens" in result.result
    assert "Violent crime" not in result.result


def test_newsnow_returns_safe_message_when_no_child_safe_title(monkeypatch):
    monkeypatch.setattr(
        newsnow,
        "fetch_news_from_api",
        lambda _conn, _source: [
            {"title": "How to end your life without help", "url": "https://example.test/bad"},
        ],
    )

    result = newsnow.get_news_from_newsnow(_conn(), source="Hacker News", lang="vi")

    assert result.action == Action.REQLLM
    assert "child-safe" in result.result.lower()
    assert "could not find" in result.result.lower()
