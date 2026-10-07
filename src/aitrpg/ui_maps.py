from __future__ import annotations

import inspect
import math
from copy import deepcopy
from html import escape

from nicegui import ui

from aitrpg.domain.models import new_id

COLORS = ('#177e89', '#355ec2', '#9659a6', '#bd722e', '#498061')
VISIBILITY = {'keeper': '仅主持', 'public': '所有角色', 'roles': '指定身份'}


def label_of(value, fallback):
    return (
        value.get('label') or value.get('name') or value.get('id') or fallback
    )


def point_of(value: dict, nodes: list[dict]) -> tuple[float, float] | None:
    if value.get('node_id'):
        value = next(
            (node for node in nodes if node.get('id') == value['node_id']), {}
        )
    coordinates = (value.get('x'), value.get('y'))
    if all(
        type(number) in (int, float)
        and math.isfinite(number)
        and 0 <= number <= 1
        for number in coordinates
    ):
        return coordinates
    return None


def rectangle(first, second) -> list[float]:
    return [
        min(first[0], second[0]),
        min(first[1], second[1]),
        max(first[0], second[0]),
        max(first[1], second[1]),
    ]


class MapDraft:
    def __init__(self, asset, positions=None, revealed_regions=None):
        self.asset = deepcopy(asset)
        self.nodes = self.asset.setdefault('nodes', [])
        self.regions = self.asset.setdefault('regions', [])
        if self.asset.get('is_fog_enabled') is None:
            self.asset['is_fog_enabled'] = bool(self.regions)
        self.positions = deepcopy(positions or {})
        self.revealed_regions = deepcopy(revealed_regions or {})
        self.selection: tuple[str, str] | None = None

    def selected(self) -> dict | None:
        if not self.selection:
            return None
        kind, identity = self.selection
        if kind == 'character':
            return self.positions.get(identity)
        items = self.nodes if kind == 'node' else self.regions
        return next((item for item in items if item['id'] == identity), None)

    def add_node(self, x, y):
        node = {
            'id': new_id(),
            'label': f'新地点 {len(self.nodes) + 1}',
            'x': x,
            'y': y,
            'visibility': 'keeper',
            'role_ids': [],
        }
        self.nodes.append(node)
        self.asset['is_map'] = True
        self.selection = ('node', node['id'])

    def add_region(self, bounds):
        if bounds[2] - bounds[0] < 0.003 or bounds[3] - bounds[1] < 0.003:
            return
        region = {
            'id': new_id(),
            'label': f'区域 {len(self.regions) + 1}',
            'shape': 'rect',
            'bounds': bounds,
            'visibility': 'keeper',
            'role_ids': [],
        }
        self.regions.append(region)
        self.asset.update({'is_map': True, 'is_fog_enabled': True})
        self.selection = ('region', region['id'])

    def place_character(self, identity, x, y):
        self.positions[identity] = {'x': x, 'y': y}
        self.selection = ('character', identity)

    def remove_selected(self):
        if not self.selection:
            return
        kind, identity = self.selection
        if kind == 'character':
            self.positions.pop(identity, None)
        elif kind == 'node':
            for value in self.positions.values():
                if value.get('node_id') == identity:
                    point = point_of(value, self.nodes)
                    if point:
                        value.clear()
                        value.update({'x': point[0], 'y': point[1]})
            self.nodes[:] = [
                node for node in self.nodes if node['id'] != identity
            ]
        else:
            self.regions[:] = [
                region for region in self.regions if region['id'] != identity
            ]
            for actor_id, identities in self.revealed_regions.items():
                self.revealed_regions[actor_id] = [
                    value for value in identities if value != identity
                ]
        self.selection = None


def map_svg(draft, width, height, names=None, show_regions=False, ghost=None):
    names = names or {}
    scale = min(width, height)
    radius = max(8, scale * 0.015)
    font_size = max(14, scale * 0.019)
    content = []
    if show_regions:
        for region in draft.regions:
            color = (
                '#cf7b28'
                if draft.selection == ('region', region['id'])
                else '#177e89'
            )
            if region.get('shape', 'rect') == 'rect':
                bounds = region.get('bounds')
                if not bounds or len(bounds) != 4:
                    continue
                x, y = bounds[0] * width, bounds[1] * height
                content.append(
                    f'<rect x="{x}" y="{y}" '
                    f'width="{(bounds[2] - bounds[0]) * width}" '
                    f'height="{(bounds[3] - bounds[1]) * height}" '
                    f'fill="{color}" fill-opacity="0.12" stroke="{color}" '
                    'stroke-width="2" vector-effect="non-scaling-stroke"/>'
                )
                content.append(
                    f'<text x="{x + 8}" y="{y + font_size + 5}" '
                    f'font-size="{font_size}" fill="{color}" '
                    'stroke="white" stroke-width="3" paint-order="stroke">'
                    f'{escape(str(label_of(region, "区域")))}</text>'
                )
                if draft.selection == ('region', region['id']):
                    for px, py in (
                        (bounds[0], bounds[1]),
                        (bounds[2], bounds[1]),
                        (bounds[2], bounds[3]),
                        (bounds[0], bounds[3]),
                    ):
                        content.append(
                            f'<rect x="{px * width - radius / 2}" '
                            f'y="{py * height - radius / 2}" '
                            f'width="{radius}" height="{radius}" '
                            'fill="#cf7b28" stroke="white" stroke-width="2"/>'
                        )
            elif region.get('shape') == 'polygon':
                points = ' '.join(
                    f'{x * width},{y * height}'
                    for x, y in region.get('points', [])
                )
                content.append(
                    f'<polygon points="{points}" fill="{color}" '
                    f'fill-opacity="0.12" stroke="{color}" stroke-width="2"/>'
                )
    points = [
        ('node', node['id'], node, label_of(node, '地点'))
        for node in draft.nodes
    ] + [
        ('character', identity, position, names.get(identity, '调查员'))
        for identity, position in draft.positions.items()
    ]
    for index, (kind, identity, value, label) in enumerate(points):
        point = point_of(value, draft.nodes)
        if point is None:
            continue
        x, y = point[0] * width, point[1] * height
        color = (
            COLORS[index % len(COLORS)] if kind == 'character' else '#177e89'
        )
        if draft.selection == (kind, identity):
            color = '#cf7b28'
        content.append(
            f'<circle cx="{x}" cy="{y}" r="{radius}" fill="{color}" '
            'stroke="white" stroke-width="2" '
            'vector-effect="non-scaling-stroke"/>'
        )
        if kind == 'character':
            content.append(
                f'<text x="{x}" y="{y + font_size * 0.32}" '
                f'font-size="{font_size}" text-anchor="middle" fill="white">'
                f'{escape(str(label)[:1])}</text>'
            )
        content.append(
            f'<text x="{x + radius + 5}" y="{y + font_size * 0.3}" '
            f'font-size="{font_size}" fill="#203645" stroke="white" '
            'stroke-width="3" paint-order="stroke">'
            f'{escape(str(label))}</text>'
        )
    if ghost:
        bounds = ghost
        content.append(
            f'<rect x="{bounds[0] * width}" y="{bounds[1] * height}" '
            f'width="{(bounds[2] - bounds[0]) * width}" '
            f'height="{(bounds[3] - bounds[1]) * height}" '
            'fill="#cf7b28" fill-opacity="0.15" stroke="#cf7b28" '
            'stroke-width="2" stroke-dasharray="7 5"/>'
        )
    return ''.join(content)


class MapCanvas:
    def __init__(
        self,
        asset,
        *,
        draft=None,
        names=None,
        height='56vh',
        editable=False,
        on_change=None,
        mode='select',
    ):
        self.draft = draft or MapDraft(asset)
        self.names = names or {}
        self.editable = editable
        self.on_change = on_change
        self.mode = mode
        self.width = self.height = 1000
        self.start = None
        self.drag = None
        self.ghost = None
        self.gesture_changed = False
        self.character_id = None
        self.zoom = 1.0
        with ui.column().classes('w-full gap-2') as self.element:
            with ui.row().classes('w-full items-center gap-2'):
                ui.button(
                    icon='remove', on_click=lambda: self.set_zoom(-0.25)
                ).props('flat dense aria-label="缩小地图"')
                self.zoom_label = ui.label('100%').classes('text-sm')
                ui.button(
                    icon='add', on_click=lambda: self.set_zoom(0.25)
                ).props('flat dense aria-label="放大地图"')
                ui.button(
                    '适应宽度', on_click=lambda: self.set_zoom(0, reset=True)
                ).props('flat dense')
            with (
                ui.element('div')
                .classes('map-canvas-viewport w-full overflow-auto rounded')
                .style(f'height:{height};background:#e8eef3;min-height:220px')
            ):
                self.image = ui.interactive_image(
                    asset.get('url', ''),
                    on_mouse=self.mouse if editable else None,
                    events=[
                        'pointerdown',
                        'pointermove',
                        'pointerup',
                        'pointerleave',
                        'pointercancel',
                    ]
                    if editable
                    else [],
                    sanitize=True,
                ).style('width:100%;min-width:100%;display:block')
                if editable:
                    self.image.style('touch-action:none;cursor:crosshair')
                self.image.on('loaded', self.loaded)
        self.redraw()

    def loaded(self, event):
        self.width = max(1, event.args.get('width', 1000))
        self.height = max(1, event.args.get('height', 1000))
        self.redraw()

    def set_zoom(self, delta, reset=False):
        self.zoom = 1.0 if reset else max(0.5, min(3, self.zoom + delta))
        self.image.style(
            f'width:{self.zoom * 100}%;min-width:{self.zoom * 100}%'
        )
        self.zoom_label.set_text(f'{self.zoom:.0%}')

    def redraw(self):
        self.image.set_content(
            map_svg(
                self.draft,
                self.width,
                self.height,
                self.names,
                show_regions=self.editable,
                ghost=self.ghost,
            )
        )

    def changed(self):
        self.redraw()
        if self.on_change:
            self.on_change()

    def near(self, first, second):
        return math.hypot(
            (first[0] - second[0]) * self.width,
            (first[1] - second[1]) * self.height,
        ) <= max(15, min(self.width, self.height) * 0.022)

    def hit(self, point):
        for identity, value in reversed(list(self.draft.positions.items())):
            location = point_of(value, self.draft.nodes)
            if location and self.near(point, location):
                return ('character', identity)
        for node in reversed(self.draft.nodes):
            location = point_of(node, self.draft.nodes)
            if location and self.near(point, location):
                return ('node', node['id'])
        for region in reversed(self.draft.regions):
            bounds = region.get('bounds', [])
            if len(bounds) == 4 and (
                bounds[0] <= point[0] <= bounds[2]
                and bounds[1] <= point[1] <= bounds[3]
            ):
                return ('region', region['id'])
        return None

    def mouse(self, event):
        if not self.editable or event.button not in (0, -1):
            return
        point = (
            max(0, min(1, event.image_x / self.width)),
            max(0, min(1, event.image_y / self.height)),
        )
        if event.type == 'pointerdown':
            self.start = point
            self.drag = self.ghost = None
            self.gesture_changed = False
            if self.mode == 'node':
                self.draft.add_node(*point)
                self.start = None
                self.changed()
                return
            if self.mode == 'region':
                self.ghost = rectangle(point, point)
                self.redraw()
                return
            if self.mode == 'character':
                if self.character_id:
                    self.draft.place_character(self.character_id, *point)
                    self.drag = ('character', self.character_id)
                    self.changed()
                return
            selected = self.draft.selected()
            if (
                self.draft.selection
                and self.draft.selection[0] == 'node'
                and selected is not None
                and point_of(selected, self.draft.nodes) is None
            ):
                selected.update({'x': point[0], 'y': point[1]})
                self.drag = self.draft.selection
                self.changed()
                return
            if self.draft.selection and self.draft.selection[0] == 'region':
                bounds = (selected or {}).get('bounds', [])
                if len(bounds) == 4:
                    corners = (
                        (bounds[0], bounds[1]),
                        (bounds[2], bounds[1]),
                        (bounds[2], bounds[3]),
                        (bounds[0], bounds[3]),
                    )
                    for index, corner in enumerate(corners):
                        if self.near(point, corner):
                            self.drag = ('resize', corners[(index + 2) % 4])
                            return
            self.draft.selection = self.hit(point)
            self.drag = self.draft.selection
            selected = self.draft.selected()
            if self.drag and self.drag[0] == 'region':
                self.drag = ('region', deepcopy(selected['bounds']))
            self.changed()
        elif event.type == 'pointermove' and event.buttons:
            if self.mode == 'region' and self.start:
                self.ghost = rectangle(self.start, point)
            elif self.drag:
                selected = self.draft.selected()
                previous = deepcopy(selected)
                if self.drag[0] == 'character':
                    self.draft.place_character(self.drag[1], *point)
                elif self.drag[0] == 'node' and selected:
                    selected.update({'x': point[0], 'y': point[1]})
                elif self.drag[0] == 'resize' and selected:
                    selected['bounds'] = rectangle(self.drag[1], point)
                elif self.drag[0] == 'region' and selected:
                    bounds = self.drag[1]
                    dx = max(
                        -bounds[0],
                        min(1 - bounds[2], point[0] - self.start[0]),
                    )
                    dy = max(
                        -bounds[1],
                        min(1 - bounds[3], point[1] - self.start[1]),
                    )
                    selected['bounds'] = [
                        bounds[0] + dx,
                        bounds[1] + dy,
                        bounds[2] + dx,
                        bounds[3] + dy,
                    ]
                self.gesture_changed |= self.draft.selected() != previous
            self.redraw()
        elif event.type in {'pointerup', 'pointerleave', 'pointercancel'}:
            if self.start is None and self.drag is None and self.ghost is None:
                return
            changed = self.gesture_changed
            if (
                event.type != 'pointercancel'
                and self.mode == 'region'
                and self.start
                and self.ghost
            ):
                previous_count = len(self.draft.regions)
                self.draft.add_region(self.ghost)
                changed |= len(self.draft.regions) != previous_count
            self.start = self.drag = self.ghost = None
            self.gesture_changed = False
            if changed:
                self.changed()
            else:
                self.redraw()


def render_map(asset: dict, *, names=None, height='56vh') -> MapCanvas:
    return MapCanvas(asset, names=names, height=height)


class MapEditor:
    def __init__(
        self,
        platform,
        identity,
        asset,
        *,
        scenario=None,
        game=None,
        on_saved=None,
    ):
        self.platform = platform
        self.identity = identity
        self.asset_id = asset['id']
        self.scenario = scenario
        self.game = game
        self.on_saved = on_saved
        self.characters = []
        if game:
            self.characters = [
                {
                    'actor_id': seat.actor_id,
                    'character_id': seat.character_id,
                    'name': platform.store.get('character', seat.character_id)[
                        'name'
                    ],
                }
                for seat in game.seats
            ]
        self.roles = {role.id: role.name for role in scenario.roles}
        self.names = {
            value['character_id']: value['name'] for value in self.characters
        }
        self.draft = MapDraft(
            asset,
            game.flags.get('map_positions', {}).get(self.asset_id, {})
            if game
            else {},
            game.flags.get('map_regions', {}).get(self.asset_id, {})
            if game
            else {},
        )
        with ui.dialog().props('persistent') as self.dialog:
            with (
                ui.card()
                .classes('w-full max-w-[1440px] p-4 md:p-6 gap-3')
                .style('height:94vh;max-height:94vh')
            ):
                with ui.row().classes('w-full items-center justify-between'):
                    ui.label(
                        '地图与图片编辑' if not game else '角色位置与地图揭示'
                    ).classes('text-xl font-semibold')
                    ui.button(icon='close', on_click=self.dialog.close).props(
                        'flat round aria-label="取消地图编辑"'
                    )
                with ui.row().classes('w-full items-center gap-3'):
                    modes = {
                        'select': '选择与拖动',
                        'node': '添加地点',
                        'region': '画揭示区域',
                    }
                    if game:
                        modes = {'character': '放置或移动角色'}
                    ui.toggle(
                        modes,
                        value=next(iter(modes)),
                        on_change=lambda e: self.mode(e.value),
                    ).props('no-caps')
                    ui.label(
                        '点图设置位置；拖动标记，或拖动区域四角。'
                    ).classes('text-sm opacity-70')
                with ui.row().classes(
                    'map-editor-body w-full flex-1 min-h-0 gap-4 items-start '
                    'overflow-y-auto flex-col md:flex-row flex-nowrap'
                ):
                    with ui.column().classes('w-full md:flex-1 min-w-0'):
                        self.canvas = MapCanvas(
                            asset,
                            draft=self.draft,
                            names=self.names,
                            height='calc(94vh - 200px)',
                            editable=True,
                            on_change=self.refresh,
                            mode=next(iter(modes)),
                        )
                    self.sidebar = ui.column().classes(
                        'map-editor-sidebar w-full md:w-[300px] shrink-0 '
                        'overflow-auto gap-3 '
                        'md:max-h-[65vh]'
                    )
                with ui.row().classes('w-full justify-end items-center gap-2'):
                    ui.label('修改仅保存在当前编辑框，保存后生效。').classes(
                        'text-sm opacity-70 mr-auto'
                    )
                    ui.button('取消', on_click=self.dialog.close).props(
                        'flat no-caps'
                    )
                    ui.button(
                        '保存地图', icon='save', on_click=self.save
                    ).props('no-caps')
        self.refresh()
        self.dialog.open()

    def mode(self, value):
        self.canvas.mode = value
        self.canvas.start = self.canvas.drag = self.canvas.ghost = None
        self.canvas.redraw()

    def update(self, value, key, new_value):
        value[key] = new_value
        self.canvas.redraw()

    def select(self, kind, identity):
        self.draft.selection = (kind, identity) if identity else None
        if kind == 'character':
            self.canvas.character_id = identity
        self.refresh()
        self.canvas.redraw()

    def remove(self):
        self.draft.remove_selected()
        self.refresh()
        self.canvas.redraw()

    def visibility_fields(self, value):
        ui.select(
            VISIBILITY,
            label='可见范围',
            value=value.get('visibility', 'keeper'),
            on_change=lambda e: self.update(value, 'visibility', e.value),
        ).classes('w-full')
        if self.roles:
            ui.select(
                self.roles,
                label='允许的身份',
                multiple=True,
                value=value.get('role_ids', []),
                on_change=lambda e: self.update(value, 'role_ids', e.value),
            ).classes('w-full').props('use-chips')

    def refresh(self):
        self.sidebar.clear()
        with self.sidebar:
            if self.game:
                self.character_fields()
                return
            with ui.expansion(
                '图片与素材设置', icon='image', value=not self.draft.selection
            ).classes('w-full'):
                value = self.draft.asset
                ui.input(
                    '图片名称',
                    value=value.get('name', ''),
                    on_change=lambda e: self.update(value, 'name', e.value),
                ).classes('w-full')
                ui.textarea(
                    '图片说明',
                    value=value.get('caption', ''),
                    on_change=lambda e: self.update(value, 'caption', e.value),
                ).classes('w-full').props('autogrow')
                ui.checkbox(
                    '作为地图使用',
                    value=value.get('is_map', False),
                    on_change=lambda e: self.update(value, 'is_map', e.value),
                )
                ui.checkbox(
                    '启用区域遮罩',
                    value=value.get(
                        'is_fog_enabled', bool(self.draft.regions)
                    ),
                    on_change=lambda e: self.update(
                        value, 'is_fog_enabled', e.value
                    ),
                )
                self.visibility_fields(value)
            node_options = {
                node['id']: label_of(node, '未命名地点')
                for node in self.draft.nodes
            }
            region_options = {
                region['id']: label_of(region, '未命名区域')
                for region in self.draft.regions
            }
            if node_options:
                ui.select(
                    node_options,
                    label='地点',
                    value=self.draft.selection[1]
                    if self.draft.selection
                    and self.draft.selection[0] == 'node'
                    else None,
                    on_change=lambda e: self.select('node', e.value),
                ).classes('w-full')
            if region_options:
                ui.select(
                    region_options,
                    label='区域',
                    value=self.draft.selection[1]
                    if self.draft.selection
                    and self.draft.selection[0] == 'region'
                    else None,
                    on_change=lambda e: self.select('region', e.value),
                ).classes('w-full')
            selected = self.draft.selected()
            if selected:
                self.object_fields(selected)
            else:
                ui.label(
                    '选择“添加地点”后点图，或选择“画揭示区域”后拖出矩形。'
                ).classes('text-sm opacity-70')

    def object_fields(self, value):
        ui.label('所选对象').classes('font-semibold')
        ui.input(
            '名称',
            value=label_of(value, ''),
            on_change=lambda e: self.update(value, 'label', e.value),
        ).classes('w-full')
        self.visibility_fields(value)
        if self.draft.selection[0] == 'node':
            for key, label in (('x', '水平位置 (%)'), ('y', '垂直位置 (%)')):
                ui.number(
                    label,
                    value=round(100 * value.get(key, 0), 2),
                    min=0,
                    max=100,
                    precision=2,
                    step=0.1,
                    on_change=lambda e, k=key: self.update(
                        value, k, (e.value or 0) / 100
                    ),
                ).classes('w-full')
        elif value.get('shape', 'rect') == 'rect':
            for index, label in enumerate(
                ('左边 (%)', '上边 (%)', '右边 (%)', '下边 (%)')
            ):
                ui.number(
                    label,
                    value=round(100 * value['bounds'][index], 2),
                    min=0,
                    max=100,
                    precision=2,
                    step=0.1,
                    on_change=lambda e, i=index: self.set_bound(
                        value, i, (e.value or 0) / 100
                    ),
                ).classes('w-full')
            ui.label('拖动区域可移动；拖动四角可调整大小。').classes(
                'text-sm opacity-70'
            )
        else:
            ui.label('已有多边形会保留；本编辑器支持新建矩形区域。').classes(
                'text-sm opacity-70'
            )
        ui.button(
            '删除所选对象', icon='delete_outline', on_click=self.remove
        ).props('flat color=negative no-caps')

    def set_bound(self, value, index, coordinate):
        value['bounds'][index] = coordinate
        self.canvas.redraw()

    def character_fields(self):
        options = {
            value['character_id']: value['name'] for value in self.characters
        }
        selected_id = self.canvas.character_id or next(iter(options), None)
        self.canvas.character_id = selected_id
        ui.select(
            options,
            label='要放置的角色',
            value=selected_id,
            on_change=lambda e: self.select('character', e.value),
        ).classes('w-full')
        ui.label('选好角色后点地图设置位置，也可拖动标记。').classes(
            'text-sm opacity-70'
        )
        if selected_id in self.draft.positions:
            ui.button(
                '清除该角色标记',
                on_click=lambda: self.clear_character(selected_id),
            ).props('flat no-caps')
        ui.separator()
        region_options = {
            region['id']: label_of(region, '区域')
            for region in self.draft.regions
        }
        ui.label('每名角色可见的区域').classes('font-semibold')
        if not region_options:
            ui.label('此地图尚未标注揭示区域，可先在模组审核中编辑。').classes(
                'text-sm opacity-70'
            )
        for value in self.characters:
            actor_id = value['actor_id']
            ui.select(
                region_options,
                label=value['name'],
                multiple=True,
                value=self.draft.revealed_regions.get(actor_id, []),
                on_change=lambda e, i=actor_id: self.update(
                    self.draft.revealed_regions, i, e.value
                ),
            ).classes('w-full').props('use-chips')

    def clear_character(self, identity):
        self.draft.positions.pop(identity, None)
        self.refresh()
        self.canvas.redraw()

    async def save(self):
        try:
            if self.game:
                self.platform.games.update_map(
                    self.identity,
                    self.asset_id,
                    self.draft.positions,
                    self.draft.revealed_regions,
                )
            else:
                value = self.draft.asset
                self.platform.scenarios.update_map_annotations(
                    self.identity,
                    self.asset_id,
                    self.draft.nodes,
                    self.draft.regions,
                    name=value.get('name', ''),
                    caption=value.get('caption', ''),
                    is_map=value.get('is_map', False),
                    visibility=value.get('visibility', 'keeper'),
                    role_ids=value.get('role_ids', []),
                    is_fog_enabled=value.get(
                        'is_fog_enabled', bool(self.draft.regions)
                    ),
                )
            self.dialog.close()
            ui.notify('地图已保存', type='positive')
            if self.on_saved:
                result = self.on_saved()
                if inspect.isawaitable(result):
                    await result
        except (ValueError, PermissionError, FileNotFoundError) as error:
            ui.notify(str(error), type='negative')


def open_scenario_map_editor(
    platform,
    scenario_id,
    asset_id,
    on_saved=None,
) -> MapEditor:
    scenario = platform.scenarios.get(scenario_id)
    asset = next(
        (value for value in scenario.assets if value.id == asset_id), None
    )
    if asset is None:
        raise ValueError('模组图片不存在')
    metadata = asset.model_dump(mode='json')
    metadata['url'] = f'/api/v1/scenarios/{scenario_id}/assets/{asset_id}'
    return MapEditor(
        platform,
        scenario_id,
        metadata,
        scenario=scenario,
        on_saved=on_saved,
    )


def open_game_map_editor(
    platform,
    game_id,
    asset_id,
    on_saved=None,
) -> MapEditor:
    game = platform.get_game(game_id)
    scenario = platform.games.scenario(game)
    asset = next(
        (value for value in scenario.assets if value.id == asset_id), None
    )
    if asset is None or not asset.is_map:
        raise ValueError('所选素材不是地图')
    metadata = asset.model_dump(mode='json')
    metadata['url'] = (
        f'/api/v1/games/{game_id}/assets/{asset_id}?markers=false'
    )
    return MapEditor(
        platform,
        game_id,
        metadata,
        scenario=scenario,
        game=game,
        on_saved=on_saved,
    )
