import httpx

from app.model import OpenRouterChatModel


def test_openrouter_structured_assessment_and_usage():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["authorization"]
        body = request.read().decode()
        assert '"response_format"' in body
        assert '"json_schema"' in body
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"hypotheses":["card_testing"],"missing_evidence":["payment status"],"explanation":"The bounded sequence supports testing."}'}}],
            "usage": {"prompt_tokens": 31, "completion_tokens": 12, "total_tokens": 43},
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = OpenRouterChatModel("sk-or-test", http_client=client)
        result = model.assess({"case_id": "HHG-001", "pattern": "card_testing"})
    assert seen["authorization"] == "Bearer sk-or-test"
    assert result.hypotheses == ["card_testing"]
    assert result.tokens == 43
