from fastapi import FastAPI

from aitrpg.api import register_api
from aitrpg.application.platform import Platform
from aitrpg.config import Settings
from aitrpg.domain.models import new_id


def create_app(settings: Settings | None = None, is_ui: bool = True):
    platform = Platform(settings or Settings())
    app = FastAPI(title='AITRPG', version='0.1.0')
    app.state.platform = platform
    register_api(app, platform)
    if is_ui:
        from nicegui import ui

        from aitrpg.ui import build_ui

        build_ui(platform)
        storage = platform.store.get('setting', 'browser_storage')
        if not storage:
            storage = {'secret': new_id() + new_id()}
            platform.store.put('setting', 'browser_storage', storage)
        ui.run_with(app, storage_secret=storage['secret'])
    return app
