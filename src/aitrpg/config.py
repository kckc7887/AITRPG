import os
from pathlib import Path

import keyring
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from aitrpg.domain.models import Provider


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='AITRPG_')
    data_dir: Path = Path('data')
    host: str = '127.0.0.1'
    port: int = 8080
    invitation_seconds: int = 120
    is_browser_open: bool = True


def environment_value(name: str) -> str:
    value = os.environ.get(name, '')
    if value or os.name != 'nt':
        return value
    import winreg

    locations = [
        (winreg.HKEY_CURRENT_USER, 'Environment'),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
        ),
    ]
    for hive, location in locations:
        try:
            with winreg.OpenKey(hive, location) as registry:
                value, _ = winreg.QueryValueEx(registry, name)
            if value:
                return str(value)
        except OSError:
            continue
    return ''


def provider_key(provider: Provider) -> str:
    if provider.keyring_id:
        value = keyring.get_password('aitrpg', provider.keyring_id)
    else:
        value = environment_value(provider.key_env)
    if not value:
        raise ValueError('未找到模型密钥，请检查环境变量或系统凭据库')
    return value
