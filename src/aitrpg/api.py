from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import Field

from aitrpg.application.platform import Platform
from aitrpg.domain.models import Actor
from aitrpg.domain.models import Character
from aitrpg.domain.models import Model
from aitrpg.domain.models import Provider
from aitrpg.domain.models import Scenario
from aitrpg.domain.models import Seat


class CharacterGeneration(Model):
    actor_id: str
    concept: str = Field(min_length=1, max_length=12000)
    public_scenario: str = Field(default='', max_length=12000)


class CharacterImport(Model):
    actor_id: str
    path: str


class ScenarioImport(Model):
    path: str
    provider_id: str | None = None


class GameCreation(Model):
    name: str
    scenario_id: str
    keeper_actor_id: str
    seats: list[Seat]
    budget_nodes: int = 1000
    budget_tokens: int = 10000000


class Intervention(Model):
    text: str
    character_id: str | None = None
    patch: dict | None = None


class BudgetChange(Model):
    budget_nodes: int = Field(ge=1)
    budget_tokens: int = Field(ge=1000)


class MapChange(Model):
    positions: dict = Field(default_factory=dict)
    revealed_regions: dict[str, list[str]] = Field(default_factory=dict)


class MapAnnotationChange(Model):
    nodes: list[dict]
    regions: list[dict]
    name: str | None = None
    caption: str | None = None
    is_map: bool | None = None
    visibility: str | None = None
    role_ids: list[str] | None = None
    scene_ids: list[str] | None = None
    is_fog_enabled: bool | None = None


def register_api(app: FastAPI, platform: Platform):
    @app.exception_handler(ValueError)
    async def value_error_handler(request, error):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=422, content={'detail': str(error)})

    @app.get('/api/v1/health')
    def health():
        return {'status': 'ok', 'version': '0.1.0'}

    @app.get('/api/v1/providers')
    def providers():
        return platform.list_providers()

    @app.post('/api/v1/providers')
    def save_provider(provider: Provider):
        return platform.save_provider(provider)

    @app.post('/api/v1/providers/{provider_id}/test')
    async def test_provider(provider_id: str):
        return await platform.test_provider(provider_id)

    @app.get('/api/v1/actors')
    def actors():
        return platform.list_actors()

    @app.post('/api/v1/actors')
    def save_actor(actor: Actor):
        return platform.save_actor(actor)

    @app.get('/api/v1/actors/{actor_id}')
    def get_actor(actor_id: str):
        record = platform.store.get('actor', actor_id)
        if record is None:
            raise HTTPException(404, 'AI 身份不存在')
        return record

    @app.get('/api/v1/characters')
    def characters(actor_id: str | None = None):
        return platform.characters.list(actor_id)

    @app.post('/api/v1/characters')
    def save_character(character: Character):
        return platform.characters.save(character)

    @app.post('/api/v1/characters/generate')
    async def generate_character(request: CharacterGeneration):
        return await platform.characters.generate(
            request.actor_id, request.concept, request.public_scenario
        )

    @app.post('/api/v1/characters/import')
    def import_character(request: CharacterImport):
        return platform.characters.import_xlsx(request.path, request.actor_id)

    @app.post('/api/v1/characters/{character_id}/clone')
    def clone_character(character_id: str):
        return platform.characters.clone(character_id)

    @app.get('/api/v1/scenarios')
    def scenarios():
        return platform.scenarios.list()

    @app.post('/api/v1/scenarios/import')
    async def import_scenario(request: ScenarioImport):
        return await platform.scenarios.import_path(
            request.path, request.provider_id
        )

    @app.post('/api/v1/scenarios')
    def save_scenario(scenario: Scenario):
        return platform.scenarios.save(scenario)

    @app.post('/api/v1/scenarios/{scenario_id}/approve')
    def approve_scenario(scenario_id: str):
        return platform.scenarios.approve(scenario_id)

    @app.post('/api/v1/scenarios/{scenario_id}/assets/{asset_id}/annotations')
    def update_map_annotations(
        scenario_id: str, asset_id: str, request: MapAnnotationChange
    ):
        return platform.scenarios.update_map_annotations(
            scenario_id, asset_id, **request.model_dump()
        )

    @app.get('/api/v1/scenarios/{scenario_id}/assets/{asset_id}')
    def preview_scenario_asset(scenario_id: str, asset_id: str):
        scenario = platform.scenarios.get(scenario_id)
        try:
            path = platform.scenarios.asset_for_viewer(
                scenario, asset_id, is_keeper=True
            )
        except (PermissionError, FileNotFoundError) as error:
            raise HTTPException(
                status_code=404, detail='素材不存在'
            ) from error
        return FileResponse(path, headers={'Cache-Control': 'no-store'})

    @app.get('/api/v1/games')
    def games():
        return platform.list_games()

    @app.post('/api/v1/games')
    def create_game(request: GameCreation):
        return platform.create_game(**request.model_dump())

    @app.get('/api/v1/games/{game_id}')
    def game_view(game_id: str, actor_id: str | None = None):
        return platform.get_game_view(game_id, actor_id)

    @app.post('/api/v1/games/{game_id}/run')
    async def run_game(game_id: str):
        return await platform.run_game(game_id)

    @app.post('/api/v1/games/{game_id}/step')
    async def step_game(game_id: str):
        return await platform.step_game(game_id)

    @app.post('/api/v1/games/{game_id}/pause')
    async def pause_game(game_id: str):
        return await platform.pause_game(game_id)

    @app.post('/api/v1/games/{game_id}/finish')
    async def finish_game(game_id: str):
        return await platform.games.finish(game_id)

    @app.post('/api/v1/games/{game_id}/synchronise')
    async def synchronise_game(game_id: str):
        return await platform.games.synchronise_controls(game_id)

    @app.post('/api/v1/games/{game_id}/intervene')
    async def intervene(game_id: str, request: Intervention):
        return await platform.intervene(game_id, **request.model_dump())

    @app.post('/api/v1/games/{game_id}/budget')
    def update_budget(game_id: str, request: BudgetChange):
        return platform.games.update_budget(game_id, **request.model_dump())

    @app.post('/api/v1/games/{game_id}/maps/{asset_id}')
    def update_map(game_id: str, asset_id: str, request: MapChange):
        return platform.games.update_map(
            game_id, asset_id, **request.model_dump()
        )

    @app.get('/api/v1/games/{game_id}/assets/{asset_id}')
    def asset(
        game_id: str,
        asset_id: str,
        actor_id: str | None = None,
        markers: bool = True,
    ):
        path = platform.games.asset_path(
            game_id, asset_id, actor_id, include_markers=markers
        )
        return FileResponse(
            path, headers={'Cache-Control': 'private, no-store'}
        )
