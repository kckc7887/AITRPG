from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from aitrpg.adapters.providers import ProviderClient
from aitrpg.adapters.storage import Store
from aitrpg.config import Settings
from aitrpg.config import environment_value
from aitrpg.domain.models import Actor
from aitrpg.domain.models import Game
from aitrpg.domain.models import Provider


class ProbeResult(BaseModel):
    reply: str


class Platform:
    def __init__(self, settings: Settings, provider_client=None):
        self.settings = settings
        self.settings.data_dir = settings.data_dir.resolve()
        self.store = Store(self.settings.data_dir / 'aitrpg.sqlite3')
        self.provider_client = provider_client or ProviderClient()
        self._characters = None
        self._scenarios = None
        self._games = None
        self._seed_providers()

    def _seed_providers(self):
        if self.store.list('provider'):
            return
        base = environment_value('DEEPSEEK_BASE_URL_OPENAI')
        if not base:
            return
        self.save_provider(
            Provider(
                name='DeepSeek V4.1 Flash · OpenAI',
                base_url=base,
                is_vision=True,
            )
        )
        if base.rstrip('/') in {
            'https://api.deepseek.com',
            'https://api.deepseek.com/v1',
        }:
            self.save_provider(
                Provider(
                    name='DeepSeek V4.1 Flash · Anthropic',
                    protocol='anthropic',
                    base_url='https://api.deepseek.com/anthropic',
                    is_vision=True,
                )
            )

    def list_providers(self) -> list[Provider]:
        return [
            Provider.model_validate(item)
            for item in self.store.list('provider')
        ]

    def save_provider(self, provider: Provider) -> Provider:
        if not provider.base_url.startswith(('https://', 'http://')):
            raise ValueError('接口地址必须以 http:// 或 https:// 开头')
        previous = self.store.get('provider', provider.id)
        if previous:
            provider = provider.model_copy(
                update={'version': previous['version'] + 1}
            )
        self.store.put(
            'provider', provider.id, provider.model_dump(mode='json')
        )
        return provider

    async def test_provider(self, identifier: str) -> dict:
        record = self.store.get('provider', identifier)
        if not record:
            raise ValueError('模型配置不存在')
        provider = Provider.model_validate(record)
        result = await self.provider_client.generate(
            provider,
            '检查接口连通性。请将 reply 设置为 OK。',
            {'task': 'connection_test'},
            ProbeResult,
        )
        return {'reply': result.value.reply, 'tokens': result.usage}

    def list_actors(self) -> list[Actor]:
        return [
            Actor.model_validate(item) for item in self.store.list('actor')
        ]

    def save_actor(self, actor: Actor) -> Actor:
        if actor.connection == 'api':
            if not actor.provider_id or not self.store.get(
                'provider', actor.provider_id
            ):
                raise ValueError('API 身份必须选择已保存的模型配置')
        self.store.put('actor', actor.id, actor.model_dump(mode='json'))
        return actor

    @property
    def characters(self):
        if self._characters is None:
            from aitrpg.application.characters import CharacterService

            self._characters = CharacterService(
                self.store, self.provider_client
            )
        return self._characters

    @property
    def scenarios(self):
        if self._scenarios is None:
            from aitrpg.application.scenarios import ScenarioService

            self._scenarios = ScenarioService(
                self.store, self.provider_client, self.settings.data_dir
            )
        return self._scenarios

    @property
    def games(self):
        if self._games is None:
            from aitrpg.application.games import GameService

            self._games = GameService(self)
        return self._games

    def list_games(self):
        return [Game.model_validate(item) for item in self.store.list('game')]

    def create_game(self, *args, **kwargs):
        return self.games.create(*args, **kwargs)

    def get_game(self, identifier):
        return self.games.get(identifier)

    def get_game_view(self, identifier, actor_id=None):
        return self.games.view(identifier, actor_id)

    async def step_game(self, identifier):
        return await self.games.step(identifier)

    async def run_game(self, identifier):
        return await self.games.run(identifier)

    async def pause_game(self, identifier):
        return await self.games.pause(identifier)

    async def intervene(self, identifier, text, character_id=None, patch=None):
        return await self.games.intervene(
            identifier, text, character_id, patch
        )

    def issue_agent_token(self, game_id, actor_id):
        return self.games.issue_token(game_id, actor_id)

    def resolve_upload(self, name: str) -> Path:
        destination = self.settings.data_dir / 'incoming'
        destination.mkdir(parents=True, exist_ok=True)
        return destination / Path(name).name
