import asyncio
import inspect
import json
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import keyring
from nicegui import ui
from nicegui.elements.dialog import Dialog

from aitrpg.config import environment_value
from aitrpg.domain.models import Actor
from aitrpg.domain.models import Character
from aitrpg.domain.models import Game
from aitrpg.domain.models import Provider
from aitrpg.domain.models import Scenario
from aitrpg.domain.models import Seat
from aitrpg.styles import APP_CSS
from aitrpg.ui_maps import open_game_map_editor
from aitrpg.ui_maps import open_scenario_map_editor
from aitrpg.ui_maps import render_map

NAVIGATION = (
    ('概览', '/', 'space_dashboard'),
    ('模型', '/providers', 'hub'),
    ('角色', '/characters', 'badge'),
    ('模组', '/scenarios', 'folder_open'),
    ('开团与历史', '/games', 'auto_stories'),
)
STATUS_LABELS = {
    'paused': '已暂停',
    'running': '自动进行中',
    'waiting': '等待回应',
    'ended': '已结束',
    'error': '需要处理',
    'draft': '待审核',
    'approved': '已审核',
}
BACKGROUND_LABELS = {
    'personal_description': '外貌与气质',
    'ideology_beliefs': '思想与信念',
    'significant_people': '重要之人',
    'meaningful_locations': '意义非凡之地',
    'treasured_possessions': '宝贵之物',
    'traits': '特质',
    'injuries_scars': '伤痕',
    'phobias_manias': '恐惧与躁狂',
    'tomes_spells_artifacts': '典籍、法术与神器',
    'strange_encounters': '神秘经历',
    'life_story': '人生经历',
    'key_connection': '关键连接的背景字段名',
}
CHECK_LABELS = {
    'critical': '大成功',
    'extreme': '极难成功',
    'hard': '困难成功',
    'regular': '成功',
    'failure': '失败',
    'fumble': '大失败',
}
CONDITION_LABELS = {
    'dead': '死亡',
    'unconscious': '昏迷',
    'dying': '濒死',
    'major_wound': '重伤',
    'stabilized': '伤势已稳定',
    'temporary_insanity': '临时疯狂',
    'indefinite_insanity': '不定性疯狂',
    'permanent_insanity': '永久疯狂',
}


def _data(value: Any) -> dict:
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    return dict(value)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        default=lambda item: (
            item.model_dump(mode='json')
            if hasattr(item, 'model_dump')
            else str(item)
        ),
    )


async def _perform(
    action: Callable, success: str = '', refresh: Callable | None = None
) -> Any:
    try:
        result = action()
        if inspect.isawaitable(result):
            result = await result
        if success:
            ui.notify(success, type='positive')
        if refresh:
            refresh()
        return result
    except Exception as error:
        ui.notify(str(error), type='negative', timeout=10000)
        return None


def _heading(title: str, subtitle: str, eyebrow: str = '调查档案') -> None:
    ui.label(eyebrow).classes('eyebrow')
    ui.label(title).classes('page-title')
    ui.label(subtitle).classes('muted mb-6')


def _empty(message: str, action: str, href: str | None = None) -> None:
    with ui.column().classes('paper empty-state w-full gap-3'):
        ui.icon('folder_open', size='28px').classes('muted')
        ui.label(message).classes('record-title')
        if href:
            ui.button(action, on_click=lambda: ui.navigate.to(href))
        else:
            ui.label(action).classes('muted')


def _shell(title: str, path: str) -> None:
    ui.add_css(APP_CSS)
    ui.colors(primary='#456d88', secondary='#9d6544', accent='#9d6544')
    with ui.left_drawer(value=False).classes('paper') as drawer:
        ui.label('AITRPG').classes('brand text-xl m-4')
        for label, href, icon in NAVIGATION:
            ui.button(
                label,
                icon=icon,
                on_click=lambda destination=href: ui.navigate.to(destination),
            ).props('flat align=left').classes('w-full')
    with ui.header().classes('h-[68px] items-center px-5 gap-5'):
        ui.button(icon='menu', on_click=drawer.toggle, color=None).props(
            'flat round text-color=white aria-label="打开导航"'
        )
        ui.link('AITRPG', '/').classes('brand text-lg no-underline text-white')
        with ui.row().classes('desktop-navigation items-center gap-1'):
            for label, href, icon in NAVIGATION:
                button = ui.button(
                    label,
                    icon=icon,
                    color=None,
                    on_click=lambda destination=href: ui.navigate.to(
                        destination
                    ),
                ).props('flat text-color=white')
                if href == path:
                    button.classes('nav-selected')
        ui.space()
        ui.label(title).classes('text-sm opacity-70')


def _provider_dialog(platform: Any, refresh: Callable, item=None) -> None:
    provider = item or Provider(
        name='',
        base_url=environment_value('DEEPSEEK_BASE_URL_OPENAI'),
        model='deepseek-flash',
    )
    with ui.dialog() as dialog, ui.card().classes('w-[760px] max-w-full p-6'):
        ui.label('编辑模型' if item else '接入模型').classes('record-title')
        ui.label('密钥使用环境变量或系统凭据库保存。').classes('muted')
        with ui.grid().classes('form-grid w-full'):
            name = ui.input('模型名称', value=provider.name)
            protocol = ui.select(
                {'openai': 'OpenAI 格式', 'anthropic': 'Anthropic 格式'},
                value=provider.protocol,
                label='接口格式',
            )
            base_url = ui.input('接口地址', value=provider.base_url)
            model = ui.input('模型标识', value=provider.model)
            key_env = ui.input('密钥环境变量', value=provider.key_env)
            secret = ui.input(
                '或输入新密钥，存入系统凭据库',
                password=True,
                password_toggle_button=True,
            )
            temperature = ui.number(
                '温度',
                value=provider.temperature,
                min=0,
                max=2,
                step=0.1,
            )
            output_limit = ui.number(
                '单次输出上限',
                value=provider.max_output_tokens,
                min=128,
                max=65536,
                step=128,
            )
            timeout = ui.number(
                '请求超时 / 秒',
                value=provider.timeout_seconds,
                min=5,
                max=600,
            )
            enabled = ui.switch('启用模型', value=provider.is_enabled)
            thinking = ui.select(
                {
                    'default': '采用模型默认',
                    'enabled': '开启思考',
                    'disabled': '关闭思考',
                },
                value=provider.thinking_mode,
                label='思考模式',
            )
        with ui.row().classes('gap-5'):
            json_mode = ui.checkbox('JSON 模式', value=provider.is_json_mode)
            tool_mode = ui.checkbox('工具调用', value=provider.is_tool_mode)
            vision = ui.checkbox('可读取图片', value=provider.is_vision)

        async def save() -> None:
            values = _data(provider)
            values.update(
                name=name.value.strip(),
                protocol=protocol.value,
                base_url=base_url.value.strip(),
                model=model.value.strip(),
                key_env=key_env.value.strip(),
                temperature=temperature.value,
                max_output_tokens=int(output_limit.value or 0),
                timeout_seconds=timeout.value,
                is_enabled=enabled.value,
                is_json_mode=json_mode.value,
                is_tool_mode=tool_mode.value,
                is_vision=vision.value,
                thinking_mode=thinking.value,
            )
            if not values['name'] or not values['model']:
                ui.notify('请填写模型名称和模型标识', type='warning')
                return

            def persist() -> None:
                configured = Provider.model_validate(values)
                if secret.value:
                    configured.keyring_id = configured.id
                    keyring.set_password('aitrpg', configured.id, secret.value)
                platform.save_provider(configured)

            result = await _perform(lambda: (persist(), True)[1], '模型已保存')
            if result:
                dialog.close()
                refresh()

        with ui.row().classes('w-full justify-end mt-3'):
            ui.button('取消', on_click=dialog.close).props('flat')
            ui.button('保存模型', on_click=save, icon='save')
    dialog.open()


def _actor_dialog(platform: Any, refresh: Callable, item=None) -> None:
    actor = item or Actor(name='')
    providers = {item.id: item.name for item in platform.list_providers()}
    with ui.dialog() as dialog, ui.card().classes('w-[620px] max-w-full p-6'):
        ui.label('编辑 AI 席位' if item else '创建 AI 席位').classes(
            'record-title'
        )
        name = ui.input('席位名称', value=actor.name).classes('w-full')
        connection = ui.select(
            {'api': '平台调用模型', 'mcp': '外部 Agent 通过 MCP 连入'},
            label='连接方式',
            value=actor.connection,
        ).classes('w-full')
        provider = ui.select(
            providers,
            value=actor.provider_id,
            label='使用模型',
        ).classes('w-full')
        provider.bind_visibility_from(
            connection, 'value', backward=lambda value: value == 'api'
        )
        style = ui.textarea('扮演风格', value=actor.style).classes('w-full')
        ui.label(
            'MCP 席位需要外部 Agent 持续领取邀请；开团后生成接入凭据。'
        ).classes('muted')

        async def save() -> None:
            if not name.value.strip():
                ui.notify('请填写席位名称', type='warning')
                return
            if connection.value == 'api' and not provider.value:
                ui.notify('请为 API 席位选择模型', type='warning')
                return
            values = _data(actor)
            values.update(
                name=name.value.strip(),
                connection=connection.value,
                provider_id=(
                    provider.value if connection.value == 'api' else None
                ),
                style=style.value,
            )
            result = await _perform(
                lambda: (
                    platform.save_actor(Actor.model_validate(values)),
                    True,
                )[1],
                'AI 席位已保存',
            )
            if result:
                dialog.close()
                refresh()

        with ui.row().classes('w-full justify-end'):
            ui.button('取消', on_click=dialog.close).props('flat')
            ui.button('保存席位', on_click=save)
    dialog.open()


def _providers_page(platform: Any) -> None:
    _shell('模型配置', '/providers')
    with ui.column().classes('workspace gap-2'):
        _heading('给每位参与者一个声音', '接入模型，再创建主持人或玩家席位。')

        @ui.refreshable
        def provider_list() -> None:
            providers = platform.list_providers()
            if not providers:
                _empty('尚未接入模型', '使用右侧按钮接入你的第一个模型。')
            with ui.grid().classes('library-grid'):
                for provider in providers:
                    with ui.card().classes('paper panel gap-3'):
                        ui.label(provider.name).classes('record-title')
                        ui.label(provider.model).classes('muted')
                        ui.label(provider.base_url).classes(
                            'text-xs break-all muted'
                        )
                        with ui.row().classes('gap-2'):
                            ui.badge(provider.protocol)
                            ui.badge(
                                '启用' if provider.is_enabled else '已停用'
                            ).props('outline')

                        async def test(item=provider) -> None:
                            response = await _perform(
                                lambda: platform.test_provider(item.id)
                            )
                            if response is not None:
                                ui.notify('模型连接测试通过', type='positive')

                        with ui.row().classes('gap-2'):
                            ui.button('测试连接', on_click=test).props('flat')
                            ui.button(
                                '编辑',
                                on_click=lambda item=provider: (
                                    _provider_dialog(
                                        platform, provider_list.refresh, item
                                    )
                                ),
                            ).props('flat')

        with ui.row().classes('w-full items-center justify-between mb-4'):
            ui.label('模型接口').classes('record-title')
            ui.button(
                '接入模型',
                icon='add',
                on_click=lambda: _provider_dialog(
                    platform, provider_list.refresh
                ),
            )
        provider_list()
        ui.separator().classes('my-6')

        @ui.refreshable
        def actor_list() -> None:
            actors = platform.list_actors()
            if not actors:
                _empty('尚未创建 AI 席位', '一个席位可以持有多份角色卡。')
            provider_names = {
                item.id: item.name for item in platform.list_providers()
            }
            with ui.grid().classes('library-grid'):
                for actor in actors:
                    with ui.card().classes('paper panel'):
                        ui.label(actor.name).classes('record-title')
                        ui.label(
                            '外部 MCP Agent'
                            if actor.connection == 'mcp'
                            else provider_names.get(
                                actor.provider_id, '未选模型'
                            )
                        ).classes('muted')
                        ui.label(actor.style or '尚未设置扮演风格').classes(
                            'text-sm'
                        )
                        ui.button(
                            '编辑席位',
                            on_click=lambda item=actor: _actor_dialog(
                                platform, actor_list.refresh, item
                            ),
                        ).props('flat')
                        if actor.connection == 'mcp':
                            ui.button(
                                '建卡接入凭据',
                                icon='key',
                                on_click=lambda item=actor: (
                                    _actor_token_dialog(platform, item)
                                ),
                            ).props('flat')

        with ui.row().classes('w-full items-center justify-between mb-4'):
            ui.label('AI 席位').classes('record-title')
            ui.button(
                '创建席位',
                icon='person_add',
                on_click=lambda: _actor_dialog(platform, actor_list.refresh),
            )
        actor_list()


def _actor_token_dialog(platform: Any, actor: Actor) -> None:
    with ui.dialog() as dialog, ui.card().classes('w-[720px] max-w-full p-6'):
        ui.label(f'{actor.name} · 建卡接入').classes('record-title')
        ui.label(
            '这份凭据只管理本人角色卡。开团后需使用游戏席位凭据领取邀请。'
        ).classes('notice text-sm')
        ui.input(
            '本机 MCP 地址',
            value=f'http://127.0.0.1:{platform.settings.port}/mcp',
        ).props('readonly').classes('w-full')
        token = (
            ui.textarea('建卡接入凭据')
            .props('readonly rows=2')
            .classes('w-full json-editor')
        )

        async def issue() -> None:
            result = await _perform(
                lambda: platform.issue_actor_token(actor.id)
            )
            if result:
                token.set_value(result)

        ui.button('生成建卡凭据', on_click=issue, icon='key')
        ui.button('关闭', on_click=dialog.close).props('flat')
    dialog.open()


def _home_page(platform: Any) -> None:
    _shell('运行概览', '/')
    with ui.column().classes('workspace gap-2'):
        _heading(
            '故事开始于一张桌子', '安排参与者、整理线索，然后把舞台交给他们。'
        )
        providers = platform.list_providers()
        characters = platform.characters.list()
        scenarios = platform.scenarios.list()
        games = platform.list_games()
        with ui.card().classes('paper panel w-full gap-5'):
            ui.label('准备你的调查').classes('record-title')
            with ui.row().classes('w-full gap-6 flex-wrap'):
                for title, detail, href in (
                    ('接入模型', f'{len(providers)} 个模型接口', '/providers'),
                    ('整理角色', f'{len(characters)} 份角色卡', '/characters'),
                    ('审核模组', f'{len(scenarios)} 份调查档案', '/scenarios'),
                ):
                    with ui.column().classes('flex-1 min-w-[180px] gap-1'):
                        ui.link(title, href).classes('record-title')
                        ui.label(detail).classes('muted')
            ui.button(
                '安排一场游戏',
                icon='auto_stories',
                on_click=lambda: ui.navigate.to('/games'),
            )
        ui.label('最近的游戏').classes('record-title mt-6 mb-3')
        if not games:
            _empty(
                '桌边还没有故事', '选择模组与角色，创建第一场游戏。', '/games'
            )
        else:
            with ui.grid().classes('library-grid'):
                for game in games[:6]:
                    _game_record(game)


def _game_record(game: Any) -> None:
    with ui.card().classes('paper panel gap-3'):
        ui.label(game.name).classes('record-title')
        ui.badge(STATUS_LABELS.get(game.status, game.status)).props('outline')
        ui.label(
            f'{len(game.seats)} 位调查员 · {game.node_count} 个叙事节点'
        ).classes('muted')
        ui.link('进入阅读台', f'/games/{game.id}')


async def _save_upload(platform: Any, event: Any) -> Path:
    incoming = platform.settings.data_dir / 'incoming'
    incoming.mkdir(parents=True, exist_ok=True)
    filename = Path(event.file.name).name
    target = incoming / f'{uuid4().hex}_{filename}'
    await event.file.save(target)
    return target


def _character_dialog(
    platform: Any, refresh: Callable, item: Character | None = None
) -> None:
    actors = {actor.id: actor.name for actor in platform.list_actors()}
    if not actors:
        ui.notify('先在模型页创建 AI 席位', type='warning')
        return
    character = item or Character(
        actor_id=next(iter(actors)),
        name='',
        attributes=dict.fromkeys(
            ('STR', 'CON', 'SIZ', 'DEX', 'APP', 'INT', 'POW', 'EDU'), 50
        ),
        max_hp=10,
        current_hp=10,
        max_mp=10,
        current_mp=10,
        current_san=50,
    )
    with ui.dialog() as dialog, ui.card().classes('w-[900px] max-w-full p-6'):
        ui.label('角色档案').classes('record-title')
        with ui.tabs().classes('w-full') as tabs:
            profile_tab = ui.tab('身份与数值')
            background_tab = ui.tab('技能与背景')
            json_tab = ui.tab('完整数据')
        with ui.tab_panels(tabs, value=profile_tab).classes('w-full'):
            with ui.tab_panel(profile_tab):
                with ui.grid().classes('form-grid w-full'):
                    name = ui.input('姓名', value=character.name)
                    actor = ui.select(
                        actors, label='所属 AI 席位', value=character.actor_id
                    )
                    if item is not None:
                        actor.disable()
                    occupation = ui.input('职业', value=character.occupation)
                    age = ui.number(
                        '年龄', value=character.age, min=1, max=120
                    )
                    era = ui.input('年代', value=character.era)
                    residence = ui.input('住址', value=character.residence)
                    birthplace = ui.input('出生地', value=character.birthplace)
                    sex = ui.input('性别', value=character.sex)
                ui.label('属性').classes('record-title mt-4')
                attribute_inputs = {}
                with ui.grid().classes('attribute-grid w-full'):
                    for key, value in character.attributes.items():
                        attribute_inputs[key] = ui.number(
                            key, value=value, min=1, precision=0
                        )
                with ui.grid().classes('attribute-grid w-full mt-4'):
                    hp = ui.number('当前 HP', value=character.current_hp)
                    mp = ui.number('当前 MP', value=character.current_mp)
                    san = ui.number('当前 SAN', value=character.current_san)
                    luck = ui.number('幸运', value=character.luck)
            with ui.tab_panel(background_tab):
                ui.label(
                    '背景可以记录外貌、信念、重要之人、地点、宝贵之物与特质。'
                ).classes('muted mb-3')
                skills = (
                    ui.textarea(
                        '技能与数值 / JSON', value=_json(character.skills)
                    )
                    .classes('w-full json-editor')
                    .props('rows=6')
                )
                background_inputs = {}
                for key, label in BACKGROUND_LABELS.items():
                    background_inputs[key] = (
                        ui.textarea(
                            label,
                            value=character.background.get(key, ''),
                        )
                        .classes('w-full')
                        .props('rows=2 autogrow')
                    )
                conditions = (
                    ui.select(
                        {
                            **CONDITION_LABELS,
                            **{
                                value: value
                                for value in character.conditions
                                if value not in CONDITION_LABELS
                            },
                        },
                        label='角色状态',
                        value=character.conditions[:],
                        multiple=True,
                    )
                    .props('use-chips')
                    .classes('w-full')
                )
            with ui.tab_panel(json_tab):
                ui.label(
                    '这里保留装备、武器、职业配点、关系和创建骰点等完整字段。'
                    '使用下方单独的按钮保存这份数据。'
                ).classes('muted')
                editor = ui.textarea(value=character.model_dump_json(indent=2))
                editor.classes('w-full json-editor').props('rows=16')

                async def save_json() -> None:
                    def persist() -> bool:
                        edited = Character.model_validate_json(editor.value)
                        if edited.id != character.id:
                            raise ValueError('编辑现有角色时不能修改角色 ID')
                        platform.characters.save(edited)
                        return True

                    if await _perform(persist, '完整角色数据已保存'):
                        dialog.close()
                        refresh()

                ui.button('保存完整数据', on_click=save_json, icon='save')

        async def save() -> None:
            def persist() -> bool:
                attributes = {
                    key: int(field.value or 0)
                    for key, field in attribute_inputs.items()
                }
                values = _data(character)
                values.update(
                    name=name.value.strip(),
                    actor_id=actor.value,
                    occupation=occupation.value,
                    age=int(age.value or 0),
                    era=era.value,
                    residence=residence.value,
                    birthplace=birthplace.value,
                    sex=sex.value,
                    attributes=attributes,
                    current_hp=int(hp.value or 0),
                    current_mp=int(mp.value or 0),
                    current_san=int(san.value or 0),
                    luck=int(luck.value or 0),
                    skills=json.loads(skills.value),
                    background={
                        **character.background,
                        **{
                            key: field.value
                            for key, field in background_inputs.items()
                        },
                    },
                    conditions=conditions.value,
                )
                if not values['name']:
                    raise ValueError('请填写调查员姓名')
                if item is None:
                    values['max_hp'] = (
                        attributes['CON'] + attributes['SIZ']
                    ) // 10
                    values['max_mp'] = attributes['POW'] // 5
                platform.characters.save(Character.model_validate(values))
                return True

            if await _perform(persist, '角色已保存'):
                dialog.close()
                refresh()

        with ui.row().classes('w-full justify-end'):
            ui.button('关闭', on_click=dialog.close).props('flat')
            ui.button('保存身份、数值与背景', on_click=save, icon='save')
    dialog.open()


def _character_detail(character: Any) -> None:
    ui.label(character.name).classes('record-title')
    ui.label(
        f'{character.occupation or "职业未填"} · {character.age} 岁 · '
        f'{character.era}'
    ).classes('muted')
    with ui.row().classes('stat-line w-full'):
        for label, current, maximum in (
            ('HP', character.current_hp, character.max_hp),
            ('MP', character.current_mp, character.max_mp),
            ('SAN', character.current_san, character.max_san),
        ):
            with ui.column().classes('gap-0'):
                ui.label(label).classes('muted')
                ui.label(f'{current} / {maximum}').classes('text-lg')
    if character.conditions:
        with ui.row().classes('gap-1'):
            for condition in character.conditions:
                ui.badge(
                    CONDITION_LABELS.get(condition, condition),
                    color='secondary',
                )


def _character_view_dialog(character: Character) -> None:
    with ui.dialog() as dialog, ui.card().classes('w-[820px] max-w-full p-6'):
        with ui.row().classes('w-full items-center justify-between'):
            ui.label(f'{character.name} · 角色档案').classes('record-title')
            ui.button(icon='close', on_click=dialog.close).props(
                'flat round aria-label="关闭角色档案"'
            )
        _character_detail(character)
        with ui.tabs().classes('w-full') as tabs:
            stats_tab = ui.tab('属性与技能')
            background_tab = ui.tab('背景与经历')
            items_tab = ui.tab('装备与资产')
        with ui.tab_panels(tabs, value=stats_tab).classes('w-full'):
            with ui.tab_panel(stats_tab):
                with ui.grid().classes('attribute-grid w-full'):
                    for key, value in character.attributes.items():
                        ui.label(f'{key}  {value}').classes('notice text-sm')
                ui.label(
                    f'幸运 {character.luck} · 移动 {character.movement} · '
                    f'体格 {character.build} · 伤害加值 '
                    f'{character.damage_bonus}'
                ).classes('muted my-4')
                ui.table(
                    columns=[
                        {
                            'name': 'skill',
                            'label': '技能',
                            'field': 'skill',
                            'align': 'left',
                            'sortable': True,
                        },
                        {
                            'name': 'value',
                            'label': '数值',
                            'field': 'value',
                            'sortable': True,
                        },
                        {
                            'name': 'marked',
                            'label': '成长标记',
                            'field': 'marked',
                        },
                    ],
                    rows=[
                        {
                            'skill': key,
                            'value': value,
                            'marked': '✓'
                            if key in character.skill_marks
                            else '',
                        }
                        for key, value in character.skills.items()
                    ],
                    row_key='skill',
                    pagination=12,
                ).classes('w-full')
            with ui.tab_panel(background_tab):
                for key, value in character.background.items():
                    if value:
                        ui.label(BACKGROUND_LABELS.get(key, key)).classes(
                            'eyebrow mt-4'
                        )
                        ui.label(value).classes('whitespace-pre-wrap')
                ui.label('调查经历').classes('record-title mt-5')
                if not character.experiences:
                    ui.label('尚未记录调查经历。').classes('muted')
                for text in character.experiences:
                    ui.markdown(text).classes('notice my-2')
                if character.relationships:
                    ui.label('人际关系').classes('record-title mt-5')
                    for text in character.relationships:
                        ui.label(text).classes('whitespace-pre-wrap')
            with ui.tab_panel(items_tab):
                for title, items in (
                    ('随身物品', character.inventory),
                    ('武器', character.weapons),
                ):
                    ui.label(title).classes('record-title mt-3')
                    if not items:
                        ui.label('未记录').classes('muted')
                    for item in items:
                        with ui.expansion(
                            item.get('name', '未命名物品')
                        ).classes('w-full'):
                            ui.label(_json(item)).classes(
                                'text-sm whitespace-pre-wrap'
                            )
                ui.label('资产').classes('record-title mt-4')
                ui.label(_json(character.assets)).classes(
                    'text-sm whitespace-pre-wrap'
                )
        ui.button('关闭', on_click=dialog.close).props('flat')
    dialog.open()


def _character_generate_dialog(platform: Any, refresh: Callable) -> None:
    actors = {
        actor.id: actor.name
        for actor in platform.list_actors()
        if actor.connection == 'api'
    }
    if not actors:
        ui.notify('先创建一个使用 API 模型的席位', type='warning')
        return
    with ui.dialog() as dialog, ui.card().classes('w-[680px] max-w-full p-6'):
        ui.label('生成完整调查员').classes('record-title')
        actor = ui.select(
            actors, label='所属 AI 席位', value=next(iter(actors))
        ).classes('w-full')
        concept = ui.textarea('角色概念').classes('w-full').props('rows=4')
        concept.props(
            'placeholder="例如：1920 年代的报社摄影师，怕水但善于交际"'
        )
        public_scenario = ui.textarea('允许角色知道的模组背景，可选').classes(
            'w-full'
        )
        status = ui.label(
            '模型将生成身份、数值、配点、装备与完整背景。'
        ).classes('muted')

        async def generate() -> None:
            if not concept.value.strip():
                ui.notify('请描述角色概念', type='warning')
                return
            button.disable()
            status.set_text('正在生成并核对角色卡，请稍候……')
            try:
                result = await _perform(
                    lambda: platform.characters.generate(
                        actor.value, concept.value, public_scenario.value
                    ),
                    '调查员已生成',
                )
                if result is not None:
                    dialog.close()
                    refresh()
                    _character_dialog(platform, refresh, result)
            finally:
                button.enable()
                status.set_text('可修改概念后重试。')

        with ui.row().classes('justify-end w-full'):
            ui.button('取消', on_click=dialog.close).props('flat')
            button = ui.button(
                '生成调查员', on_click=generate, icon='auto_awesome'
            )
    dialog.open()


def _character_import_dialog(platform: Any, refresh: Callable) -> None:
    actors = {actor.id: actor.name for actor in platform.list_actors()}
    if not actors:
        ui.notify('先创建持有角色卡的 AI 席位', type='warning')
        return
    with ui.dialog() as dialog, ui.card().classes('w-[640px] max-w-full p-6'):
        ui.label('导入 COC7 角色卡').classes('record-title')
        actor = ui.select(
            actors, label='所属 AI 席位', value=next(iter(actors))
        ).classes('w-full')
        path = ui.input('本机 XLSX 文件路径').classes('w-full')

        async def import_file(filename: str) -> None:
            result = await _perform(
                lambda: platform.characters.import_xlsx(filename, actor.value),
                '角色卡已导入',
            )
            if result is not None:
                dialog.close()
                refresh()
                _character_dialog(platform, refresh, result)

        async def uploaded(event) -> None:
            target = await _perform(lambda: _save_upload(platform, event))
            if target:
                await import_file(str(target))

        ui.upload(
            label='或上传角色卡',
            on_upload=uploaded,
            max_file_size=20 * 1024 * 1024,
            auto_upload=True,
        ).props('accept=.xlsx').classes('w-full')
        ui.label(
            '兼容 COC7空白卡CY23.5；空白模板会提示缺少的身份或数值。'
        ).classes('muted')
        with ui.row().classes('justify-end w-full'):
            ui.button('取消', on_click=dialog.close).props('flat')
            ui.button('从路径导入', on_click=lambda: import_file(path.value))
    dialog.open()


def _characters_page(platform: Any) -> None:
    _shell('角色库', '/characters')
    with ui.column().classes('workspace gap-2'):
        _heading(
            '他们带着自己的往事',
            '一份角色卡、一段背景，和每次调查留下的变化。',
        )

        @ui.refreshable
        def records() -> None:
            characters = platform.characters.list()
            if not characters:
                _empty(
                    '尚未建立角色档案', '生成完整调查员，或导入已有的角色卡。'
                )
                return
            actor_names = {
                actor.id: actor.name for actor in platform.list_actors()
            }
            with ui.grid().classes('library-grid'):
                for character in characters:
                    with ui.card().classes('paper panel gap-3'):
                        _character_detail(character)
                        ui.label(
                            actor_names.get(character.actor_id, '未知 AI 席位')
                        ).classes('muted')
                        if character.import_warnings:
                            ui.label(
                                '；'.join(character.import_warnings)
                            ).classes('notice text-sm')
                        if character.locked_game_id:
                            ui.button(
                                '查看角色档案',
                                icon='badge',
                                on_click=lambda item=character: (
                                    _character_view_dialog(item)
                                ),
                            ).props('flat')
                            ui.link(
                                '正在游戏中，查看当局角色',
                                f'/games/{character.locked_game_id}',
                            )
                        else:
                            ui.button(
                                '查看与编辑档案',
                                on_click=lambda item=character: (
                                    _character_dialog(
                                        platform, records.refresh, item
                                    )
                                ),
                            ).props('flat')
                        ui.button(
                            '复制角色',
                            icon='content_copy',
                            on_click=lambda item=character: _clone_character(
                                platform, item.id, records.refresh
                            ),
                        ).props('flat')

        with ui.row().classes('mb-5 gap-3'):
            ui.button(
                'AI 生成调查员',
                icon='auto_awesome',
                on_click=lambda: _character_generate_dialog(
                    platform, records.refresh
                ),
            )
            ui.button(
                '导入角色卡',
                icon='upload',
                on_click=lambda: _character_import_dialog(
                    platform, records.refresh
                ),
            ).props('outline')
            ui.button(
                '手动建卡',
                icon='add',
                on_click=lambda: _character_dialog(platform, records.refresh),
            ).props('flat')
        records()


async def _clone_character(
    platform: Any, character_id: str, refresh: Callable
) -> None:
    await _perform(
        lambda: platform.characters.clone(character_id, name=None),
        '角色已复制，可用于另一场游戏',
        refresh,
    )


def _scenario_review_dialog(platform: Any, refresh: Callable, item) -> None:
    scenario = item
    blocks = {block.id: block for block in scenario.source_blocks}
    pending_map_assets = None

    def show_source(identity: str) -> None:
        source_choice.set_value(identity)
        tabs.set_value(source_tab)

    def source_links(identities: list[str]) -> None:
        if not identities:
            return
        with ui.row().classes('w-full gap-2 mt-3'):
            for identity in identities:
                block = blocks.get(identity)
                if block is not None:
                    ui.button(
                        f'{block.file} · {block.locator}',
                        icon='description',
                        on_click=lambda key=identity: show_source(key),
                    ).props('flat dense').classes('text-xs')

    with (
        ui.dialog() as dialog,
        ui.card().classes('review-dialog w-[1180px] max-w-full p-0'),
    ):
        with ui.row().classes('w-full items-center justify-between p-5'):
            with ui.column().classes('gap-1'):
                ui.label(scenario.title).classes('record-title')
                ui.label(
                    '核对剧情与秘密，标注地图，然后批准用于开团。'
                ).classes('muted')
            ui.button(icon='close', on_click=dialog.close).props(
                'flat round aria-label="关闭模组审核"'
            )
        with ui.tabs().classes('w-full border-b') as tabs:
            review_tab = ui.tab('审核')
            scenes_tab = ui.tab('剧情档案')
            maps_tab = ui.tab('地图与图片')
            source_tab = ui.tab('原文出处')
            json_tab = ui.tab('规范 JSON')
        with ui.tab_panels(tabs, value=review_tab).classes('review-body'):
            with ui.tab_panel(review_tab):
                ui.label(scenario.description or '尚未填写模组简介。')
                ui.label(
                    f'{scenario.min_players}–{scenario.max_players} 位玩家 · '
                    f'{scenario.era} · {len(scenario.scenes)} 个场景 · '
                    f'{len(scenario.assets)} 份图片资料'
                ).classes('muted my-3')
                resolutions = {}
                if not scenario.review_issues:
                    ui.label(
                        '没有待处理的导入问题，请核对秘密与出处。'
                    ).classes('notice my-3')
                for issue in scenario.review_issues:
                    with ui.card().classes('w-full p-3 my-2'):
                        ui.badge(
                            '必须处理'
                            if issue.severity == 'blocker'
                            else '提醒',
                            color='secondary',
                        )
                        ui.label(issue.message)
                        source_links(issue.source_ids)
                        resolutions[issue.id] = ui.checkbox(
                            '已核对并解决', value=issue.is_resolved
                        )
            with ui.tab_panel(scenes_tab):
                for role in scenario.roles:
                    with ui.expansion(f'角色 HO · {role.name}').classes(
                        'w-full'
                    ):
                        ui.label('公开信息').classes('eyebrow')
                        ui.markdown(role.public_text or '未填写')
                        ui.label('仅该角色知道').classes('eyebrow mt-3')
                        ui.markdown(role.secret_text or '未填写')
                        source_links(role.source_ids)
                category = (
                    ui.select(
                        {
                            'scenes': '场景',
                            'npcs': 'NPC',
                            'clues': '线索',
                            'handouts': '手册',
                            'endings': '结局',
                        },
                        value='scenes',
                        label='内容类型',
                    )
                    .classes('w-full mt-4')
                    .props('outlined')
                )
                scene_choice = (
                    ui.select(
                        {value.id: value.title for value in scenario.scenes},
                        value=scenario.scenes[0].id
                        if scenario.scenes
                        else None,
                        label='选择条目，可输入名称查找',
                        with_input=True,
                    )
                    .classes('w-full my-3')
                    .props('outlined')
                )

                @ui.refreshable
                def scene_detail() -> None:
                    selected = next(
                        (
                            value
                            for value in getattr(scenario, category.value)
                            if value.id == scene_choice.value
                        ),
                        None,
                    )
                    if selected is None:
                        ui.label('当前分类没有条目。').classes('muted')
                        return
                    if category.value == 'scenes':
                        ui.label('公开描述候选').classes('eyebrow')
                        ui.markdown(selected.public_text or '未填写')
                        ui.label('主持人资料').classes('eyebrow mt-4')
                        ui.markdown(selected.keeper_text or '未填写')
                        if selected.conditions:
                            ui.label('场景条件').classes('eyebrow mt-4')
                            ui.label(_json(selected.conditions)).classes(
                                'text-sm whitespace-pre-wrap'
                            )
                        scene_names = {
                            scene.id: scene.title for scene in scenario.scenes
                        }
                        ui.label(
                            '后继场景：'
                            + (
                                '、'.join(
                                    scene_names.get(key, key)
                                    for key in selected.next_scene_ids
                                )
                                or '无固定后继'
                            )
                        ).classes('muted mt-3')
                    else:
                        ui.badge(
                            {
                                'public': '公开',
                                'keeper': '仅主持',
                                'roles': '指定身份',
                            }[selected.visibility]
                        ).props('outline')
                        ui.markdown(selected.text or '未填写')
                    source_links(selected.source_ids)

                def change_category() -> None:
                    options = {
                        value.id: value.title
                        for value in getattr(scenario, category.value)
                    }
                    scene_choice.set_options(
                        options, value=next(iter(options), None)
                    )
                    scene_detail.refresh()

                category.on_value_change(change_category)
                scene_choice.on_value_change(scene_detail.refresh)
                scene_detail()
            with ui.tab_panel(maps_tab):
                ui.label(
                    '打开图片后可标为地图，在图上放置地点、绘制区域，'
                    '并设置每一项的可见范围。'
                ).classes('muted mb-4')

                @ui.refreshable
                def images() -> None:
                    if not scenario.assets:
                        ui.label('导入资料中没有图片。').classes('muted')
                    with ui.grid().classes('library-grid'):
                        for asset in scenario.assets:
                            with ui.card().classes('paper p-4 gap-3'):
                                ui.image(
                                    f'/api/v1/scenarios/{scenario.id}'
                                    f'/assets/{asset.id}?v={scenario.version}'
                                ).classes('review-asset-image').props(
                                    'fit=contain'
                                )
                                ui.label(asset.name).classes('font-medium')
                                ui.label(
                                    f'{"地图" if asset.is_map else "图片"} · '
                                    f'{len(asset.nodes)} 地点 · '
                                    f'{len(asset.regions)} 区域'
                                ).classes('muted text-xs')
                                ui.button(
                                    '编辑地图'
                                    if asset.is_map
                                    else '查看与标注',
                                    icon='edit_location_alt',
                                    on_click=lambda value=asset: (
                                        open_scenario_map_editor(
                                            platform,
                                            scenario.id,
                                            value.id,
                                            map_saved,
                                        )
                                    ),
                                ).props('outline')

                images()
            with ui.tab_panel(source_tab):
                ui.label(
                    f'作者：{scenario.author or "未标明"} · '
                    f'来源：{scenario.source or "未标明"}'
                ).classes('muted')
                ui.label(scenario.rights or '原资料的使用约定尚未填写。')
                source_choice = (
                    ui.select(
                        {
                            key: f'{value.file} · {value.locator}'
                            for key, value in blocks.items()
                        },
                        value=next(iter(blocks), None),
                        label='选择原文位置，可输入文件或页码查找',
                        with_input=True,
                    )
                    .classes('w-full my-3')
                    .props('outlined')
                )

                @ui.refreshable
                def source_detail() -> None:
                    block = blocks.get(source_choice.value)
                    if block is None:
                        ui.label('没有原文出处记录。').classes('muted')
                    else:
                        ui.label(block.text).classes('whitespace-pre-wrap')

                source_choice.on_value_change(source_detail.refresh)
                source_detail()
            with ui.tab_panel(json_tab):
                ui.label(
                    '完整结构的高级编辑。地图也可以在“地图与图片”中直接标注。'
                ).classes('muted mb-3')
                editor = ui.textarea(value=scenario.model_dump_json(indent=2))
                editor.classes('w-full json-editor').props('rows=18')

        def map_saved() -> None:
            nonlocal scenario, pending_map_assets
            latest = platform.scenarios.get(scenario.id)
            assets = [asset.model_dump(mode='json') for asset in latest.assets]
            try:
                values = json.loads(editor.value)
                if not isinstance(values, dict):
                    raise ValueError('模组 JSON 需要对象')
            except (TypeError, ValueError):
                pending_map_assets = assets
                ui.notify(
                    '地图已保存。规范 JSON 内容已保留，请修正后再保存；'
                    '保存时会合并最新地图标注。',
                    type='warning',
                )
            else:
                values['assets'] = assets
                values['version'] = latest.version
                values['status'] = latest.status
                editor.set_value(_json(values))
                pending_map_assets = None
            scenario = latest
            images.refresh()

        def save_package() -> Scenario:
            values = json.loads(editor.value)
            if values.get('id') != scenario.id:
                raise ValueError('不能修改模组 ID')
            if pending_map_assets is not None:
                values['assets'] = pending_map_assets
                values['version'] = scenario.version
            values['status'] = 'draft'
            revised = Scenario.model_validate(values)
            for issue in revised.review_issues:
                if issue.id in resolutions:
                    issue.is_resolved = resolutions[issue.id].value
            return platform.scenarios.save(revised)

        async def save() -> None:
            if await _perform(save_package, '规范包已保存，请继续审核'):
                dialog.close()
                refresh()

        async def approve() -> None:
            def action() -> bool:
                save_package()
                platform.scenarios.approve(scenario.id)
                return True

            if await _perform(action, '模组已批准，可用于开团'):
                dialog.close()
                refresh()

        with ui.row().classes('dialog-actions w-full justify-end'):
            ui.button('关闭', on_click=dialog.close).props('flat')
            ui.button('保存草稿', on_click=save).props('outline')
            ui.button('批准用于开团', on_click=approve, icon='task_alt')
        dialog.on('hide', refresh)
    dialog.open()


def _scenario_import_dialog(platform: Any, refresh: Callable) -> None:
    providers = {
        item.id: item.name
        for item in platform.list_providers()
        if item.is_enabled
    }
    with ui.dialog() as dialog, ui.card().classes('w-[740px] max-w-full p-6'):
        ui.label('整理一份模组').classes('record-title')
        ui.label(
            '选择文件或资料目录。原文会保留出处，AI 整理结果需要审核。'
        ).classes('muted')
        path = ui.input('本机文件或目录路径').classes('w-full')
        provider = ui.select(
            providers,
            label='整理模组使用的模型，可留空',
            value=next(iter(providers), None),
            clearable=True,
        ).classes('w-full')
        status = ui.label('支持 ZIP、PDF、DOCX 和规范 JSON 包。').classes(
            'muted'
        )

        async def import_file(filename: str) -> None:
            button.disable()
            status.set_text('正在提取资料、图片与出处，请稍候……')
            try:
                result = await _perform(
                    lambda: platform.scenarios.import_path(
                        filename, provider.value
                    ),
                    '模组已整理，等待审核',
                )
                if result is not None:
                    dialog.close()
                    refresh()
                    _scenario_review_dialog(platform, refresh, result)
            finally:
                button.enable()
                status.set_text('可修正路径或选择模型后重试。')

        async def uploaded(event) -> None:
            target = await _perform(lambda: _save_upload(platform, event))
            if target:
                await import_file(str(target))

        ui.upload(
            label='或上传模组文件',
            on_upload=uploaded,
            auto_upload=True,
            max_file_size=200 * 1024 * 1024,
        ).props('accept=.zip,.pdf,.docx,.json').classes('w-full')
        with ui.row().classes('w-full justify-end'):
            ui.button('取消', on_click=dialog.close).props('flat')
            button = ui.button(
                '从路径整理模组',
                icon='folder_open',
                on_click=lambda: import_file(path.value),
            )
    dialog.open()


def _scenarios_page(platform: Any) -> None:
    _shell('模组档案', '/scenarios')
    with ui.column().classes('workspace gap-2'):
        _heading(
            '每条线索都有出处',
            '把散落的文字与地图，整理成主持人可使用的档案。',
        )

        @ui.refreshable
        def records() -> None:
            scenarios = platform.scenarios.list()
            if not scenarios:
                _empty('还没有模组档案', '导入一份模组，审核后即可开始调查。')
                return
            with ui.grid().classes('library-grid'):
                for scenario in scenarios:
                    with ui.card().classes('paper panel gap-3'):
                        ui.label(scenario.title).classes('record-title')
                        ui.badge(STATUS_LABELS[scenario.status]).props(
                            'outline'
                        )
                        ui.label(
                            scenario.description or '简介尚未填写'
                        ).classes('text-sm')
                        ui.label(
                            f'{scenario.min_players}–'
                            f'{scenario.max_players} 人 · '
                            f'{scenario.era} · {len(scenario.assets)} 份资料'
                        ).classes('muted')
                        unresolved = [
                            issue
                            for issue in scenario.review_issues
                            if not issue.is_resolved
                        ]
                        blockers = sum(
                            issue.severity == 'blocker' for issue in unresolved
                        )
                        if blockers:
                            ui.label(f'{blockers} 项阻塞问题待处理').classes(
                                'notice text-sm'
                            )
                        warnings = len(unresolved) - blockers
                        if warnings:
                            ui.label(f'{warnings} 项导入提醒').classes('muted')
                        ui.button(
                            '查看与审核',
                            on_click=lambda item=scenario: (
                                _scenario_review_dialog(
                                    platform, records.refresh, item
                                )
                            ),
                        ).props('flat')

        ui.button(
            '导入模组',
            icon='upload',
            on_click=lambda: _scenario_import_dialog(
                platform, records.refresh
            ),
        ).classes('mb-5')
        records()


def _games_page(platform: Any) -> None:
    _shell('开团与历史', '/games')
    with ui.column().classes('workspace gap-2'):
        _heading(
            '把角色带到同一张桌边',
            '选定主持人、模组与调查员，开始一场可暂停的游戏。',
        )
        scenarios = {
            item.id: item
            for item in platform.scenarios.list()
            if item.status == 'approved'
        }
        actors = {item.id: item for item in platform.list_actors()}
        characters = {
            item.id: item
            for item in platform.characters.list()
            if not item.locked_game_id
        }
        if not scenarios:
            _empty(
                '先审核一份模组',
                '导入、核对并批准模组后再开团。',
                '/scenarios',
            )
        elif not actors or not characters:
            _empty(
                '桌边还缺少参与者',
                '创建 AI 席位，并为玩家准备角色卡。',
                '/characters',
            )
        else:
            _game_creation(platform, scenarios, actors, characters)
        ui.label('游戏档案').classes('record-title mt-6 mb-4')
        games = platform.list_games()
        if not games:
            _empty('尚未创建游戏', '上方选配完成后，故事会保存在这里。')
        with ui.grid().classes('library-grid'):
            for game in games:
                _game_record(game)


def _game_creation(platform, scenarios, actors, characters) -> None:
    with ui.card().classes('paper panel w-full gap-4'):
        with ui.grid().classes('form-grid w-full'):
            name = ui.input('游戏名称')
            scenario = ui.select(
                {key: item.title for key, item in scenarios.items()},
                label='已审核模组',
                value=next(iter(scenarios)),
            )
            keeper = ui.select(
                {key: item.name for key, item in actors.items()},
                label='主持人 AI 席位',
                value=next(iter(actors)),
            )
            selected = ui.select(
                {key: item.name for key, item in characters.items()},
                label='玩家角色卡',
                multiple=True,
                value=[],
            ).props('use-chips')
        assigned_roles = {}

        @ui.refreshable
        def role_assignments() -> None:
            chosen = scenarios[scenario.value]
            ui.label(
                f'模组建议 {chosen.min_players}–{chosen.max_players} 位玩家。'
            ).classes('muted')
            if actors[keeper.value].connection == 'mcp':
                ui.label('主持人需要外部 Agent 连入并持续领取邀请。').classes(
                    'notice'
                )
            for character_id in selected.value:
                character = characters[character_id]
                role_options = {role.id: role.name for role in chosen.roles}
                if assigned_roles.get(character_id) not in role_options:
                    assigned_roles.pop(character_id, None)
                with ui.row().classes('w-full items-center gap-4'):
                    ui.label(character.name).classes('font-medium')
                    ui.label(actors[character.actor_id].name).classes('muted')
                    if role_options:
                        role = ui.select(
                            role_options,
                            label='模组角色 / HO',
                            value=assigned_roles.get(character_id),
                            clearable=True,
                        ).classes('min-w-[220px]')
                        role.on_value_change(
                            lambda event, key=character_id: (
                                assigned_roles.update({key: event.value})
                            )
                        )

        for field in (scenario, keeper, selected):
            field.on_value_change(lambda: role_assignments.refresh())
        role_assignments()
        with ui.expansion('运行预算').classes('w-full'):
            with ui.grid().classes('form-grid w-full'):
                budget_nodes = ui.number(
                    '最多叙事节点',
                    value=1000,
                    min=1,
                    precision=0,
                )
                budget_tokens = ui.number(
                    '平台 API 最多使用 tokens',
                    value=10000000,
                    min=1000,
                    precision=0,
                )
            ui.label(
                '达到预算后暂停并保留记录。token 数只统计平台调用的 API；'
                '外部 Agent 的实际用量与账单由其客户端掌握。'
            ).classes('muted')

        async def create() -> None:
            if not selected.value:
                ui.notify('请至少选择一位调查员', type='warning')
                return
            if not name.value.strip():
                ui.notify('请填写游戏名称', type='warning')
                return
            seats = [
                Seat(
                    actor_id=characters[key].actor_id,
                    character_id=key,
                    role_id=assigned_roles.get(key),
                )
                for key in selected.value
            ]
            result = await _perform(
                lambda: platform.create_game(
                    name.value.strip(),
                    scenario.value,
                    keeper.value,
                    seats,
                    budget_nodes=int(budget_nodes.value or 0),
                    budget_tokens=int(budget_tokens.value or 0),
                ),
                '游戏已创建',
            )
            if result is not None:
                ui.navigate.to(f'/games/{result.id}')

        ui.button('创建游戏，进入阅读台', on_click=create, icon='auto_stories')


def _event_content(event: dict, names: dict) -> tuple[str, str, str]:
    kind = event.get('kind', '')
    data = event.get('data') or {}
    character_id = data.get('character_id')
    actor_id = data.get('actor_id')
    if kind == 'narration':
        return '主持人', str(data.get('text', '')), ''
    if kind in ('player', 'reaction'):
        title = names.get(character_id, names.get(actor_id, '调查员'))
        detail = str(data.get('intent', ''))
        if kind == 'reaction':
            title += ' · 防御回应'
            defense = {
                'dodge': '闪避',
                'fight_back': '反击',
                'cover': '寻找掩护',
            }.get(data.get('defense'), '')
            detail = ' · '.join(part for part in (defense, detail) if part)
        return title, str(data.get('speech', '')), detail
    if kind == 'check':
        title = names.get(character_id, '调查员') + ' · 骰点'
        expression = data.get('expression', '')
        total = data.get('total', '')
        text = f'{expression} → {total}'
        detail = data.get('details') or {}
        if detail.get('skill'):
            text = f'{detail["skill"]} · {text}'
        if detail.get('level') in CHECK_LABELS:
            text += ' · ' + CHECK_LABELS[detail['level']]
        reason = data.get('reason', '')
        return title, text, reason + ('\n' + _json(detail) if detail else '')
    if kind == 'ruling':
        return (
            '规则裁定',
            str(data.get('reason', '')),
            _json(data.get('details') or {}),
        )
    if kind == 'control_projection':
        return '叙事时空核对', str(data.get('reason', '')), _json(data)
    if kind == 'intervention':
        return '人工介入', str(data.get('text', data.get('reason', ''))), ''
    if kind == 'status':
        return (
            '游戏状态',
            str(data.get('text', data.get('message', data.get('status', '')))),
            '',
        )
    if kind == 'improvisation':
        return '主持即兴记录', str(data.get('text', '')), ''
    if kind == 'contest':
        winner = data.get('winner', '')
        outcome = {
            'attacker': names.get(data.get('initiator_id'), '发起方') + '胜出',
            'defender': names.get(data.get('opponent_id'), '对抗方') + '胜出',
            'neither': '双方均未成功',
            'reroll': '平局',
        }.get(winner, winner)
        return '对抗结果', outcome, str(data.get('reason', ''))
    return kind or '游戏记录', str(data.get('text', '')), _json(data)


def _render_event(event: dict, names: dict) -> None:
    title, text, detail = _event_content(event, names)
    if not text.strip() and not detail.strip():
        return
    sequence = event.get('sequence', '')
    time = _event_time(event.get('created_at'))
    classes = 'story-event w-full'
    if event.get('kind') == 'narration':
        classes += ' keeper'
    elif event.get('kind') in ('check', 'contest'):
        classes += ' roll'
    with ui.column().classes(classes):
        meta = f'{sequence} · {title}' + (f' · {time}' if time else '')
        if event.get('is_private'):
            meta += ' · 私密记录'
        ui.label(meta).classes('event-meta')
        if text:
            ui.markdown(text).classes('story-text w-full')
        if detail:
            detail_label = '查看详情'
            if event.get('kind') in ('player', 'reaction'):
                detail_label = (
                    '交给主持的行动'
                    if event.get('data', {}).get('intent_visibility', 'keeper')
                    == 'keeper'
                    else '行动意图'
                )
            with ui.expansion(detail_label).classes('w-full'):
                ui.label(detail).classes('event-detail whitespace-pre-wrap')


def _event_time(value: Any) -> str:
    if not value:
        return ''
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return ''
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(timezone(timedelta(hours=8))).strftime('%H:%M:%S')


class GameReader:
    def __init__(self, platform: Any, game_id: str) -> None:
        self.platform = platform
        self.game_id = game_id
        self.actor_id: str | None = None
        self.seen_ids: set[str] = set()
        self.side_signature = ''
        self.asset_signature = ''
        self.clue_signature = ''
        self.sidebar_panels: dict = {}
        self.is_refreshing = False
        self.is_busy = False
        self.event_limit = 80
        self.event_filter = 'story'
        self.event_search = ''
        self.displayed_ids: list[str] = []
        self.is_following = True
        self.event_ceiling: int | None = None
        self.view: dict = {}
        self.names = {actor.id: actor.name for actor in platform.list_actors()}
        self.build()

    def build(self) -> None:
        game = self.platform.get_game(self.game_id)
        _shell(game.name, '/games')
        with ui.column().classes('workspace reader-workspace'):
            with ui.row().classes(
                'reader-heading w-full justify-between items-center gap-3'
            ):
                with ui.column().classes('gap-1'):
                    ui.label('调查阅读台').classes('eyebrow')
                    ui.label(game.name).classes('page-title')
                    self.scene_label = ui.label().classes('muted')
                self.status_label = ui.label().classes('reader-status')
            self.error_label = ui.label().classes('notice text-sm w-full')
            self.error_label.set_visibility(False)
            self._toolbar(game)
            with ui.grid().classes('reader-grid'):
                with ui.column().classes(
                    'paper reader-sidebar character-sidebar'
                ):
                    with ui.row().classes('reader-section-header w-full'):
                        ui.label('调查员').classes('record-title')
                    with ui.scroll_area().classes('reader-scroll'):
                        self.characters_panel = ui.column().classes(
                            'reader-sidebar-content'
                        )
                with ui.column().classes('paper reader-paper'):
                    self._log_toolbar()
                    with ui.scroll_area(on_scroll=self._story_scroll).classes(
                        'reader-scroll'
                    ) as self.story_scroll:
                        with ui.column().classes('reader-events gap-0'):
                            self.older_button = (
                                ui.button(
                                    '载入更早记录',
                                    icon='history',
                                    on_click=self.load_older,
                                )
                                .props('flat dense')
                                .classes('self-center mb-5')
                            )
                            self.empty_label = ui.label(
                                '主持人尚未开场。使用“推进一步”开始。'
                            ).classes('muted')
                            self.events_panel = ui.column().classes(
                                'w-full gap-0'
                            )
                with ui.column().classes('paper reader-sidebar map-sidebar'):
                    with ui.tabs().classes('reader-tabs') as tabs:
                        maps_tab = ui.tab('地图与图片')
                        clues_tab = ui.tab('线索与手册')
                    with ui.tab_panels(tabs, value=maps_tab):
                        with ui.tab_panel(maps_tab):
                            with ui.scroll_area().classes('reader-scroll'):
                                self.assets_panel = ui.column().classes(
                                    'reader-sidebar-content'
                                )
                        with ui.tab_panel(clues_tab):
                            with ui.scroll_area().classes('reader-scroll'):
                                self.clues_panel = ui.column().classes(
                                    'reader-sidebar-content'
                                )
            with ui.row().classes('reader-footer'):
                self.usage_label = ui.label()
                ui.label('空格：开始 / 暂停 · →：推进一步').classes(
                    'keyboard-hint'
                )
                self.follow_checkbox = ui.checkbox(
                    '跟随最新', value=True, on_change=self._follow_changed
                ).props('dense size=xs')
        self.refresh()
        ui.timer(2, self.refresh)
        ui.keyboard(
            on_key=self._key,
            repeating=False,
            ignore=['input', 'select', 'button', 'textarea', 'div', 'a'],
        )

    def _toolbar(self, game: Game) -> None:
        options = {'__global__': '主持人资料与全局记录'}
        for seat in game.seats:
            options[seat.actor_id] = (
                self.names.get(seat.actor_id, '调查员') + '所见'
            )
        with ui.row().classes('reader-toolbar w-full'):
            self.auto_button = ui.button(
                '自动进行', icon='play_arrow', on_click=self.toggle_run
            )
            self.step_button = ui.button(
                '推进一步', icon='skip_next', on_click=self.step
            ).props('outline')
            ui.button(
                '介入游戏', icon='edit_note', on_click=self.intervention
            ).props('flat')
            self.map_button = ui.button(
                '地图位置',
                icon='edit_location_alt',
                on_click=self.map_management,
            ).props('flat')
            ui.button(
                '角色与地图', icon='map', on_click=self.sidebar_dialog
            ).props('flat').classes('mobile-sidebar-button')
            ui.select(
                options,
                value='__global__',
                label='阅读视角',
                on_change=self.change_view,
            ).classes('reader-view-select').props('outlined dense')
            with ui.button(icon='more_horiz').props(
                'flat round aria-label="对局操作"'
            ):
                with ui.menu():
                    ui.menu_item('调整运行预算', on_click=self.budget_dialog)
                    self.sync_button = ui.menu_item(
                        '核对叙事时空', on_click=self.synchronise_controls
                    )
                    ui.menu_item('外部 Agent 接入', on_click=self.agent_dialog)
                    ui.menu_item('导出当前视角记录', on_click=self.export)
                    ui.menu_item('结束本局', on_click=self.finish_dialog)

    def _log_toolbar(self) -> None:
        with ui.row().classes('reader-log-tools'):
            ui.select(
                {
                    'story': '剧情与骰点',
                    'narration': '主持叙事',
                    'all': '所有记录',
                },
                value=self.event_filter,
                on_change=self.filter_events,
            ).props('outlined dense aria-label="记录类型"')
            ui.input(
                placeholder='搜索当前视角记录',
                on_change=self.search_events,
            ).props('outlined dense clearable debounce=300').on(
                'keydown.enter', lambda: None
            )
            ui.button(
                icon='vertical_align_bottom', on_click=self.jump_latest
            ).props('flat round dense aria-label="跳到最新记录"')
            self.new_events_button = ui.button(
                '有新记录', on_click=self.jump_latest
            ).props('flat dense')
            self.new_events_button.set_visibility(False)

    def _story_scroll(self, event: Any) -> None:
        self.is_following = event.vertical_percentage >= 0.96
        self.follow_checkbox.set_value(self.is_following)

    def _follow_changed(self, event: Any) -> None:
        self.is_following = bool(event.value)
        if self.is_following:
            self.event_ceiling = None
            self._append_events()
            self.story_scroll.scroll_to(percent=1)
        elif self.event_ceiling is None:
            self.event_ceiling = max(
                (
                    _data(item).get('sequence', 0)
                    for item in self.view.get('events', [])
                ),
                default=0,
            )

    def jump_latest(self) -> None:
        self.is_following = True
        self.event_ceiling = None
        self.follow_checkbox.set_value(True)
        self._append_events()
        self.story_scroll.scroll_to(percent=1)

    def filter_events(self, event: Any) -> None:
        self.event_filter = event.value
        self._reset_events()

    def search_events(self, event: Any) -> None:
        self.event_search = (event.value or '').strip().casefold()
        self._reset_events()

    def _reset_events(self) -> None:
        self.events_panel.clear()
        self.seen_ids.clear()
        self.displayed_ids.clear()
        self.event_limit = 80
        self.event_ceiling = None
        self._append_events()

    def load_older(self) -> None:
        self.event_limit += 80
        self.is_following = False
        self.follow_checkbox.set_value(False)
        self._append_events()
        self.story_scroll.scroll_to(percent=0)

    def refresh(self) -> None:
        if self.is_refreshing:
            return
        self.is_refreshing = True
        try:
            self.view = self.platform.get_game_view(
                self.game_id, actor_id=self.actor_id
            )
            game = Game.model_validate(_data(self.view['game']))
            self.status_label.set_text(
                STATUS_LABELS.get(game.status, game.status)
            )
            scene_title = next(
                (
                    scene['title']
                    for scene in self.view.get('scenario', {}).get(
                        'scenes', []
                    )
                    if scene['id'] == game.scene_id
                ),
                '当前地点未公开' if game.scene_id else '尚未开场',
            )
            hour, minute = divmod(round(game.hour * 60), 60)
            self.scene_label.set_text(
                f'第 {game.day + 1} 天 · {hour:02d}:{minute:02d} · '
                f'{"战斗" if game.mode == "combat" else "自由调查"} · '
                f'{scene_title}'
            )
            self.usage_label.set_text(
                f'节点 {game.node_count} / {game.budget_nodes} · '
                f'API tokens {game.token_usage:,} / {game.budget_tokens:,}'
            )
            self.error_label.set_text(game.last_error)
            self.error_label.set_visibility(bool(game.last_error))
            self.auto_button.set_text(
                '暂停游戏' if game.status == 'running' else '自动进行'
            )
            self.auto_button.props(
                f'icon={"pause" if game.status == "running" else "play_arrow"}'
            )
            is_ended = game.status == 'ended'
            self.auto_button.set_enabled(
                not is_ended and (game.status == 'running' or not self.is_busy)
            )
            self.step_button.set_enabled(
                not is_ended and game.status != 'running' and not self.is_busy
            )
            self.sync_button.set_enabled(not is_ended and not self.is_busy)
            self.map_button.set_visibility(self.actor_id is None)
            characters = [
                _data(item) for item in self.view.get('characters', [])
            ]
            for character in characters:
                self.names[character['id']] = character['name']
                self.names[character['actor_id']] = character['name']
            self._append_events()
            signature = _json(
                {
                    'characters': characters,
                    'invitations': self.view.get('invitations', []),
                }
            )
            if signature != self.side_signature:
                self.side_signature = signature
                for panel in (
                    self.characters_panel,
                    self.sidebar_panels.get('characters'),
                ):
                    if panel is not None:
                        panel.clear()
                        with panel:
                            self._characters(characters)
            assets_signature = _json(self.view.get('assets', []))
            if assets_signature != self.asset_signature:
                self.asset_signature = assets_signature
                for panel in (
                    self.assets_panel,
                    self.sidebar_panels.get('assets'),
                ):
                    if panel is not None:
                        panel.clear()
                        with panel:
                            self._assets()
            clues_signature = _json(
                {
                    category: self.view.get('scenario', {}).get(category, [])
                    for category in ('clues', 'handouts')
                }
            )
            if clues_signature != self.clue_signature:
                self.clue_signature = clues_signature
                for panel in (
                    self.clues_panel,
                    self.sidebar_panels.get('clues'),
                ):
                    if panel is not None:
                        panel.clear()
                        with panel:
                            self._clues()
        except Exception as error:
            self.error_label.set_text(str(error))
            self.error_label.set_visibility(True)
        finally:
            self.is_refreshing = False

    def _append_events(self) -> None:
        if not self.is_following and self.event_ceiling is None:
            self.event_ceiling = max(
                (
                    _data(item).get('sequence', 0)
                    for item in self.view.get('events', [])
                ),
                default=0,
            )
        events = sorted(
            (_data(item) for item in self.view.get('events', [])),
            key=lambda item: item.get('sequence', 0),
        )
        if self.event_filter == 'narration':
            events = [item for item in events if item['kind'] == 'narration']
        elif self.event_filter == 'story':
            events = [
                item
                for item in events
                if item['kind']
                in (
                    'narration',
                    'player',
                    'reaction',
                    'check',
                    'contest',
                    'intervention',
                )
            ]
        if self.event_search:
            events = [
                item
                for item in events
                if self.event_search
                in ' '.join(_event_content(item, self.names)).casefold()
            ]
        if self.event_ceiling is not None:
            new_count = sum(
                item.get('sequence', 0) > self.event_ceiling for item in events
            )
            events = [
                item
                for item in events
                if item.get('sequence', 0) <= self.event_ceiling
            ]
        else:
            new_count = 0
        self.new_events_button.set_text(f'{new_count} 条新记录')
        self.new_events_button.set_visibility(bool(new_count))
        self.older_button.set_visibility(len(events) > self.event_limit)
        events = events[-self.event_limit :]
        self.empty_label.set_visibility(not events)
        if not events:
            self.empty_label.set_text(
                '没有符合筛选的记录。试试其他类型或清空搜索。'
                if self.event_search or self.event_filter != 'story'
                else '主持人尚未开场。使用“推进一步”开始。'
            )
        identifiers = [
            str(item.get('id', item.get('sequence'))) for item in events
        ]
        if identifiers == self.displayed_ids:
            return
        if identifiers[: len(self.displayed_ids)] != self.displayed_ids:
            self.events_panel.clear()
            self.seen_ids.clear()
        with self.events_panel:
            for event in events:
                event_id = str(event.get('id', event.get('sequence')))
                if event_id in self.seen_ids:
                    continue
                _render_event(event, self.names)
                self.seen_ids.add(event_id)
        self.displayed_ids = identifiers
        if self.is_following:
            self.story_scroll.scroll_to(percent=1)

    def _characters(self, characters: list[dict]) -> None:
        invitations = [
            _data(item)
            for item in self.view.get('invitations', [])
            if _data(item).get('status') in ('pending', 'claimed')
        ]
        if invitations:
            with ui.column().classes('waiting-list w-full gap-1'):
                ui.label(
                    '待续回应'
                    if self.view['game']['status'] == 'paused'
                    else '当前等待'
                ).classes('eyebrow')
                for invitation in invitations:
                    ui.label(
                        self.names.get(
                            invitation.get('actor_id'), '外部 Agent'
                        )
                    ).classes('text-sm')
        for values in characters:
            with ui.column().classes('investigator-card'):
                if 'attributes' not in values:
                    ui.label(values['name']).classes('record-title')
                    ui.label(values.get('occupation') or '职业未填').classes(
                        'muted'
                    )
                    ui.separator()
                    continue
                character = Character.model_validate(values)
                ui.label(character.name).classes('record-title')
                ui.label(character.occupation or '职业未填').classes('muted')
                with ui.grid().classes('investigator-stats'):
                    for title, current, maximum in (
                        ('HP', character.current_hp, character.max_hp),
                        ('MP', character.current_mp, character.max_mp),
                        ('SAN', character.current_san, character.max_san),
                    ):
                        with ui.column().classes('investigator-stat'):
                            ui.label(title).classes('muted')
                            ui.label(f'{current}/{maximum}').classes(
                                'stat-value'
                            )
                for condition in character.conditions:
                    ui.badge(
                        CONDITION_LABELS.get(condition, condition),
                        color='secondary',
                    ).props('outline')
                ui.button(
                    '查看角色档案',
                    icon='badge',
                    on_click=lambda item=character: _character_view_dialog(
                        item
                    ),
                ).props('flat dense')
            ui.separator()

    def _assets(self) -> None:
        assets = sorted(
            self.view.get('assets', []),
            key=lambda item: not _data(item).get('is_map'),
        )
        if not assets:
            ui.icon('map', size='28px').classes('muted')
            ui.label('当前没有可见的地图或图片。').classes('muted')
            if self.actor_id is None:
                ui.label('可在模组审核的“地图与图片”中标注地图。').classes(
                    'muted text-xs'
                )
        for value in assets:
            asset = _data(value)
            with ui.column().classes('reader-asset'):
                with ui.row().classes('w-full items-center gap-2'):
                    ui.icon('map' if asset.get('is_map') else 'image')
                    ui.label(asset.get('name', '图片资料')).classes(
                        'font-medium text-sm'
                    )
                url = asset.get('url', '')
                if url:
                    ui.image(url).classes('w-full rounded').props(
                        'fit=contain'
                    ).on('click', lambda _, item=asset: self.map_dialog(item))
                    ui.button(
                        '展开地图' if asset.get('is_map') else '查看图片',
                        icon='open_in_full',
                        on_click=lambda item=asset: self.map_dialog(item),
                    ).props('flat dense')
                if asset.get('is_map') and self.actor_id is None:
                    ui.button(
                        '调整位置与揭示',
                        icon='edit_location_alt',
                        on_click=lambda item=asset: open_game_map_editor(
                            self.platform,
                            self.game_id,
                            item['id'],
                            self.refresh,
                        ),
                    ).props('flat dense')
                self._map_legend(asset)

    def _clues(self) -> None:
        scenario = self.view.get('scenario', {})
        has_contents = False
        for category, label in (('clues', '线索'), ('handouts', '手册')):
            items = scenario.get(category, [])
            if not items:
                continue
            has_contents = True
            ui.label(label).classes('eyebrow')
            for item in items:
                with ui.expansion(item['title']).classes('reader-clue w-full'):
                    ui.markdown(item.get('text', '')).classes('text-sm w-full')
        if not has_contents:
            ui.label('当前视角还没有可见的线索或手册。').classes('muted')

    def _map_legend(self, asset: dict) -> None:
        for index, character_id in enumerate(
            asset.get('positions', {}), start=1
        ):
            with ui.row().classes('items-center gap-2'):
                ui.badge(str(index), color='secondary').props('rounded')
                ui.label(self.names.get(character_id, '调查员')).classes(
                    'text-sm'
                )

    def map_dialog(self, asset: dict) -> None:
        with (
            ui.dialog() as dialog,
            ui.card().classes('w-[1200px] max-w-full p-5'),
        ):
            with ui.row().classes('w-full justify-between items-center'):
                ui.label(asset.get('name', '地图与资料')).classes(
                    'record-title'
                )
                ui.button(icon='close', on_click=dialog.close).props(
                    'flat round aria-label="关闭图片"'
                )
            render_map(asset, names=self.names, height='62vh')
            self._map_legend(asset)
        dialog.open()

    def sidebar_dialog(self) -> None:
        panels = {}
        with (
            ui.dialog() as dialog,
            ui.card().classes('w-[760px] max-w-full p-5'),
        ):
            with ui.row().classes('w-full items-center justify-between'):
                ui.label('角色与资料').classes('record-title')
                ui.button(icon='close', on_click=dialog.close).props(
                    'flat round aria-label="关闭角色与资料"'
                )
            with ui.tabs().classes('w-full') as tabs:
                characters_tab = ui.tab('角色')
                maps_tab = ui.tab('地图与资料')
                clues_tab = ui.tab('线索与手册')
            with ui.tab_panels(tabs, value=characters_tab).classes('w-full'):
                with ui.tab_panel(characters_tab):
                    with ui.column().classes('w-full gap-3') as panel:
                        panels['characters'] = panel
                        self._characters(
                            [
                                _data(item)
                                for item in self.view.get('characters', [])
                            ]
                        )
                with ui.tab_panel(maps_tab):
                    with ui.column().classes('w-full gap-3') as panel:
                        panels['assets'] = panel
                        self._assets()
                with ui.tab_panel(clues_tab):
                    with ui.column().classes('w-full gap-3') as panel:
                        panels['clues'] = panel
                        self._clues()
            ui.button('关闭', on_click=dialog.close).props('flat')
        self.sidebar_panels = panels

        def detach() -> None:
            if self.sidebar_panels is panels:
                self.sidebar_panels = {}

        dialog.on('hide', detach)
        dialog.open()

    def finish_dialog(self) -> None:
        game = self.platform.get_game(self.game_id)
        if game.status == 'ended':
            ui.notify('本局已经结束，游戏记录仍可阅读', type='info')
            return
        with (
            ui.dialog() as dialog,
            ui.card().classes('w-[620px] max-w-full p-6'),
        ):
            ui.label('结束这场游戏').classes('record-title')
            ui.label(
                '结束会写入记录并释放角色卡，已有故事与存档保留。'
                '原局将无法继续，可复制角色或另开一局。'
            ).classes('notice')

            async def finish() -> None:
                result = await _perform(
                    lambda: self.platform.games.finish(
                        self.game_id, ending='用户结束本局'
                    ),
                    '本局已结束，角色卡已释放',
                )
                if result is not None:
                    dialog.close()
                    self.refresh()

            with ui.row().classes('w-full justify-end'):
                ui.button('继续保留本局', on_click=dialog.close).props('flat')
                ui.button('确认结束本局', on_click=finish, color='secondary')
        dialog.open()

    def budget_dialog(self) -> None:
        game = self.platform.get_game(self.game_id)
        with (
            ui.dialog() as dialog,
            ui.card().classes('w-[680px] max-w-full p-6'),
        ):
            ui.label('调整这局的运行预算').classes('record-title')
            nodes = ui.number(
                '最多叙事节点', value=game.budget_nodes, min=1, precision=0
            ).classes('w-full')
            tokens = ui.number(
                '平台 API token 上限',
                value=game.budget_tokens,
                min=1000,
                precision=0,
            ).classes('w-full')
            ui.label(
                f'已使用 {game.node_count} 个节点、'
                f'{game.token_usage:,} API tokens。'
                '外部 Agent 用量不计入；提高预算后可继续运行。'
            ).classes('muted')

            async def save() -> None:
                result = await _perform(
                    lambda: self.platform.games.update_budget(
                        self.game_id,
                        int(nodes.value or 0),
                        int(tokens.value or 0),
                    ),
                    '运行预算已保存',
                )
                if result is not None:
                    dialog.close()
                    self.refresh()

            with ui.row().classes('w-full justify-end'):
                ui.button('取消', on_click=dialog.close).props('flat')
                ui.button('保存预算', on_click=save, icon='save')
        dialog.open()

    def map_management(self) -> None:
        if self.actor_id is not None:
            return
        game = self.platform.get_game(self.game_id)
        scenario = self.platform.games.scenario(game)
        maps = {
            asset.id: asset.name for asset in scenario.assets if asset.is_map
        }
        if not maps:
            ui.notify(
                '当前模组没有地图。请在模组审核的“地图与图片”中标注后开团。',
                type='info',
            )
            return
        if len(maps) == 1:
            open_game_map_editor(
                self.platform, self.game_id, next(iter(maps)), self.refresh
            )
            return
        with (
            ui.dialog() as dialog,
            ui.card().classes('w-[500px] max-w-full p-5'),
        ):
            ui.label('选择需要调整的地图').classes('record-title')
            chosen = (
                ui.select(maps, value=next(iter(maps)), label='地图')
                .classes('w-full')
                .props('outlined')
            )

            def edit() -> None:
                dialog.close()
                open_game_map_editor(
                    self.platform, self.game_id, chosen.value, self.refresh
                )

            with ui.row().classes('w-full justify-end'):
                ui.button('取消', on_click=dialog.close).props('flat')
                ui.button('打开地图', icon='map', on_click=edit)
        dialog.open()

    def change_view(self, event: Any) -> None:
        self.actor_id = None if event.value == '__global__' else event.value
        self.events_panel.clear()
        self.seen_ids.clear()
        self.displayed_ids.clear()
        self.event_ceiling = None
        self.event_limit = 80
        self.side_signature = ''
        self.asset_signature = ''
        self.clue_signature = ''
        self.refresh()

    async def _operate(self, action: Callable, success: str = '') -> None:
        if self.is_busy:
            return
        self.is_busy = True
        self.auto_button.disable()
        self.step_button.disable()
        self.sync_button.disable()
        try:
            await _perform(action, success)
        finally:
            self.is_busy = False
            await asyncio.sleep(0)
            self.refresh()

    async def step(self) -> None:
        await self._operate(lambda: self.platform.step_game(self.game_id))

    async def synchronise_controls(self) -> None:
        await self._operate(
            lambda: self.platform.games.synchronise_controls(self.game_id),
            '已核对叙事时空，游戏保持暂停',
        )

    async def toggle_run(self) -> None:
        game = self.platform.get_game(self.game_id)
        if game.status == 'running':
            await _perform(lambda: self.platform.pause_game(self.game_id))
            self.refresh()
        else:
            await self._operate(lambda: self.platform.run_game(self.game_id))

    async def _key(self, event: Any) -> None:
        if not event.action.keydown or any(
            (
                event.modifiers.ctrl,
                event.modifiers.alt,
                event.modifiers.meta,
                event.modifiers.shift,
            )
        ):
            return
        if any(
            isinstance(element, Dialog) and element.value
            for element in event.client.elements.values()
        ):
            return
        if event.key.space:
            await self.toggle_run()
        elif event.key.arrow_right:
            game = self.platform.get_game(self.game_id)
            if game.status not in ('running', 'ended'):
                await self.step()

    def intervention(self) -> None:
        characters = {
            _data(item)['id']: _data(item)['name']
            for item in self.view.get('characters', [])
        }
        with (
            ui.dialog() as dialog,
            ui.card().classes('w-[740px] max-w-full p-6'),
        ):
            ui.label('介入游戏').classes('record-title')
            text = ui.textarea('给主持人的说明或介入理由').classes('w-full')
            target = ui.select(
                characters,
                label='需要调整的角色，可留空',
                clearable=True,
            ).classes('w-full')
            patch = (
                ui.textarea('角色状态修改 / JSON，可留空')
                .classes('w-full json-editor')
                .props('rows=4')
            )
            ui.label('介入会留下记录。修改数值前请填写具体理由。').classes(
                'muted'
            )

            async def submit() -> None:
                async def action() -> bool:
                    if not text.value.strip():
                        raise ValueError('请填写介入说明')
                    values = (
                        json.loads(patch.value)
                        if patch.value.strip()
                        else None
                    )
                    if values is not None and not target.value:
                        raise ValueError('修改角色状态时需要选择角色')
                    result = self.platform.intervene(
                        self.game_id,
                        text.value,
                        character_id=target.value,
                        patch=values,
                    )
                    if inspect.isawaitable(result):
                        await result
                    return True

                if await _perform(action, '介入已记录'):
                    dialog.close()
                    self.refresh()

            with ui.row().classes('w-full justify-end'):
                ui.button('取消', on_click=dialog.close).props('flat')
                ui.button('记录介入', on_click=submit, icon='save')
        dialog.open()

    def agent_dialog(self) -> None:
        game = self.platform.get_game(self.game_id)
        allowed = {game.keeper_actor_id} | {
            seat.actor_id for seat in game.seats
        }
        actors = {
            actor.id: actor.name
            for actor in self.platform.list_actors()
            if actor.id in allowed and actor.connection == 'mcp'
        }
        with (
            ui.dialog() as dialog,
            ui.card().classes('w-[720px] max-w-full p-6'),
        ):
            ui.label('外部 Agent 接入').classes('record-title')
            if not actors:
                ui.label(
                    '本局没有 MCP 席位。请在开团前选配外部 Agent。'
                ).classes('muted')
            else:
                actor = ui.select(
                    actors,
                    value=next(iter(actors)),
                    label='接入席位',
                ).classes('w-full')
                endpoint = (
                    f'http://127.0.0.1:{self.platform.settings.port}/mcp'
                )
                ui.input('本机 MCP 地址', value=endpoint).props(
                    'readonly'
                ).classes('w-full')
                token = (
                    ui.textarea('席位接入凭据')
                    .props('readonly rows=2')
                    .classes('w-full json-editor')
                )

                async def issue() -> None:
                    result = await _perform(
                        lambda: self.platform.issue_agent_token(
                            self.game_id, actor.value
                        )
                    )
                    if result:
                        token.set_value(result)

                ui.button('生成接入凭据', on_click=issue, icon='key')
                ui.label(
                    '外部 Agent 需要保持运行，反复领取邀请并提交回应。'
                    '仅连接聊天应用不会自动持续游玩。'
                ).classes('notice text-sm')
            ui.button('关闭', on_click=dialog.close).props('flat')
        dialog.open()

    def export(self) -> None:
        view = self.platform.get_game_view(
            self.game_id, actor_id=self.actor_id
        )
        ui.download.content(
            _json(view),
            filename=f'game_{self.game_id}.json',
            media_type='application/json',
        )


def _game_page(platform: Any, game_id: str) -> None:
    try:
        GameReader(platform, game_id)
    except Exception as error:
        _shell('游戏档案', '/games')
        with ui.column().classes('workspace'):
            _empty(str(error), '回到游戏档案', '/games')


def build_ui(platform: Any) -> None:
    @ui.page('/')
    def home() -> None:
        _home_page(platform)

    @ui.page('/providers')
    def providers() -> None:
        _providers_page(platform)

    @ui.page('/characters')
    def characters() -> None:
        _characters_page(platform)

    @ui.page('/scenarios')
    def scenarios() -> None:
        _scenarios_page(platform)

    @ui.page('/games')
    def games() -> None:
        _games_page(platform)

    @ui.page('/games/{game_id}')
    def game(game_id: str) -> None:
        _game_page(platform, game_id)
