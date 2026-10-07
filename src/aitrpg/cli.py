import argparse
import webbrowser

import uvicorn

from aitrpg.app import create_app
from aitrpg.config import Settings


def main():
    parser = argparse.ArgumentParser(description='启动本地 AI 跑团平台')
    parser.add_argument('--host', default=None)
    parser.add_argument('--port', type=int, default=None)
    parser.add_argument('--data-dir', default=None)
    parser.add_argument('--no-browser', action='store_true')
    arguments = parser.parse_args()
    overrides = {
        name: value
        for name, value in {
            'host': arguments.host,
            'port': arguments.port,
            'data_dir': arguments.data_dir,
        }.items()
        if value is not None
    }
    settings = Settings(**overrides)
    app = create_app(settings)
    if settings.is_browser_open and not arguments.no_browser:
        webbrowser.open(f'http://127.0.0.1:{settings.port}')
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == '__main__':
    main()
