import json

import httpx
import pytest
from pydantic import BaseModel

from aitrpg.adapters.providers import ProviderClient
from aitrpg.domain.models import Provider


class Result(BaseModel):
    speech: str


@pytest.mark.parametrize('protocol', ['openai', 'anthropic'])
async def test_provider_receives_validated_result(monkeypatch, protocol):
    monkeypatch.setenv('TEST_MODEL_KEY', 'local-test-key')
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if protocol == 'openai':
            body = {
                'choices': [
                    {'message': {'content': '{"speech":"我检查船舱"}'}}
                ],
                'usage': {'total_tokens': 12},
            }
        else:
            body = {
                'content': [
                    {'type': 'text', 'text': '{"speech":"我检查船舱"}'}
                ],
                'usage': {'input_tokens': 8, 'output_tokens': 4},
            }
        return httpx.Response(200, json=body)

    client = ProviderClient(httpx.MockTransport(respond))
    provider = Provider(
        name='测试',
        protocol=protocol,
        base_url='https://model.test/v1',
        key_env='TEST_MODEL_KEY',
    )
    result = await client.generate(provider, '扮演调查员', {'day': 1}, Result)
    assert result.value.speech == '我检查船舱'
    assert result.usage == 12
    assert requests[0]['messages'][-1]['role'] == 'user'


async def test_invalid_fields_trigger_one_repair(monkeypatch):
    monkeypatch.setenv('TEST_MODEL_KEY', 'local-test-key')
    attempts = []

    def respond(request):
        attempts.append(request)
        content = '{"wrong":1}' if len(attempts) == 1 else '{"speech":"等待"}'
        return httpx.Response(
            200, json={'choices': [{'message': {'content': content}}]}
        )

    provider = Provider(
        name='测试', base_url='https://model.test', key_env='TEST_MODEL_KEY'
    )
    result = await ProviderClient(httpx.MockTransport(respond)).generate(
        provider, '扮演调查员', {}, Result
    )
    assert result.value.speech == '等待'
    assert len(attempts) == 2
