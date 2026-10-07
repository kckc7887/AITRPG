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


@pytest.mark.parametrize(
    ('protocol', 'model', 'mode'),
    [
        ('openai', 'deepseek-flash', 'disabled'),
        ('anthropic', 'deepseek-flash', 'disabled'),
        ('anthropic', 'deepseek-flash', 'enabled'),
        ('anthropic', 'claude-sonnet-4-5', 'enabled'),
    ],
)
async def test_thinking_configuration_is_accepted_by_provider_protocol(
    monkeypatch, protocol, model, mode
):
    monkeypatch.setenv('TEST_MODEL_KEY', 'test-key')

    def respond(request):
        payload = json.loads(request.content)
        if protocol == 'openai':
            is_valid = payload.get('thinking') == {'type': mode}
        elif model.startswith('deepseek'):
            expected = 'none' if mode == 'disabled' else 'high'
            is_valid = payload.get('reasoning') == {'effort': expected}
            is_valid = is_valid and 'thinking' not in payload
        else:
            budget = payload.get('thinking', {}).get('budget_tokens', 0)
            is_valid = 1024 <= budget < payload['max_tokens']
            is_valid = is_valid and 'temperature' not in payload
        if not is_valid:
            return httpx.Response(400, json={'error': 'invalid thinking'})
        body = (
            {'choices': [{'message': {'content': '{"speech":"继续调查"}'}}]}
            if protocol == 'openai'
            else {
                'content': [{'type': 'text', 'text': '{"speech":"继续调查"}'}]
            }
        )
        return httpx.Response(200, json=body)

    provider = Provider(
        name='思考模式',
        protocol=protocol,
        model=model,
        base_url='https://model.test',
        key_env='TEST_MODEL_KEY',
        thinking_mode=mode,
    )
    result = await ProviderClient(httpx.MockTransport(respond)).generate(
        provider, '扮演调查员', {}, Result
    )
    assert result.value.speech == '继续调查'


async def test_thinking_tools_are_not_rejected_for_forced_choice(monkeypatch):
    monkeypatch.setenv('TEST_MODEL_KEY', 'test-key')

    def respond(request):
        payload = json.loads(request.content)
        if payload.get('tool_choice') != 'auto':
            return httpx.Response(400, json={'error': 'forced thinking tool'})
        return httpx.Response(
            200,
            json={
                'choices': [
                    {
                        'message': {
                            'tool_calls': [
                                {
                                    'function': {
                                        'name': 'submit_result',
                                        'arguments': '{"speech":"观察"}',
                                    }
                                }
                            ],
                        }
                    }
                ]
            },
        )

    provider = Provider(
        name='思考工具',
        base_url='https://model.test',
        model='deepseek-flash',
        key_env='TEST_MODEL_KEY',
        thinking_mode='enabled',
        is_tool_mode=True,
    )
    result = await ProviderClient(httpx.MockTransport(respond)).generate(
        provider, '扮演调查员', {}, Result
    )
    assert result.value.speech == '观察'


async def test_format_repair_does_not_resend_hidden_reasoning(monkeypatch):
    monkeypatch.setenv('TEST_MODEL_KEY', 'test-key')
    requests = []

    def respond(request):
        requests.append(request)
        value = '{"wrong":1}' if len(requests) == 1 else '{"speech":"观察"}'
        return httpx.Response(
            200,
            json={
                'choices': [
                    {
                        'message': {
                            'content': value,
                            'reasoning_content': 'DO_NOT_RESEND_REASONING',
                        }
                    }
                ]
            },
        )

    provider = Provider(
        name='格式修复',
        base_url='https://model.test',
        key_env='TEST_MODEL_KEY',
    )
    result = await ProviderClient(httpx.MockTransport(respond)).generate(
        provider, '扮演调查员', {}, Result
    )
    assert result.value.speech == '观察'
    assert len(requests) == 2
    assert b'DO_NOT_RESEND_REASONING' not in requests[1].content
