from fastapi import FastAPI
from fastapi import HTTPException

from aitrpg.application.platform import Platform
from aitrpg.domain.models import Actor
from aitrpg.domain.models import Provider


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
