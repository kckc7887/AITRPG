from types import SimpleNamespace

import pytest

from aitrpg.ui_maps import MapCanvas
from aitrpg.ui_maps import MapDraft
from aitrpg.ui_maps import map_svg
from aitrpg.ui_maps import point_of


def canvas_for(asset, mode='select', positions=None):
    canvas = MapCanvas.__new__(MapCanvas)
    canvas.draft = MapDraft(asset, positions)
    canvas.width = 1000
    canvas.height = 500
    canvas.editable = True
    canvas.mode = mode
    canvas.start = canvas.drag = canvas.ghost = None
    canvas.gesture_changed = False
    canvas.character_id = None
    canvas.on_change = None
    canvas.redraw = lambda: None
    return canvas


def pointer(canvas, kind, x, y, buttons=1):
    canvas.mouse(
        SimpleNamespace(
            type=kind,
            image_x=x,
            image_y=y,
            button=0,
            buttons=buttons,
        )
    )


def test_dragged_node_uses_image_coordinates_and_does_not_write_source():
    original = {'nodes': [{'id': 'door', 'label': '门', 'x': 0.2, 'y': 0.3}]}
    canvas = canvas_for(original)
    pointer(canvas, 'pointerdown', 200, 150)
    pointer(canvas, 'pointermove', 700, 200)
    pointer(canvas, 'pointerup', 700, 200, buttons=0)
    assert point_of(canvas.draft.nodes[0], []) == (0.7, 0.4)
    assert point_of(original['nodes'][0], []) == (0.2, 0.3)


def test_node_click_followed_by_name_input_does_not_rebuild_sidebar_twice():
    canvas = canvas_for({}, mode='node')
    refreshes = []
    canvas.on_change = lambda: refreshes.append('rebuilt')
    pointer(canvas, 'pointerdown', 200, 150)
    selected = canvas.draft.selected()
    selected['label'] = '接待室'
    pointer(canvas, 'pointerup', 200, 150, buttons=0)
    pointer(canvas, 'pointerleave', 200, 150, buttons=0)
    assert refreshes == ['rebuilt']
    assert canvas.draft.selected()['label'] == '接待室'
    pointer(canvas, 'pointerdown', 600, 300)
    canvas.draft.selected()['label'] = '办公室'
    pointer(canvas, 'pointerup', 600, 300, buttons=0)
    pointer(canvas, 'pointerleave', 600, 300, buttons=0)
    assert refreshes == ['rebuilt', 'rebuilt']
    assert [node['label'] for node in canvas.draft.nodes] == [
        '接待室',
        '办公室',
    ]


def test_selection_click_without_motion_preserves_sidebar_until_real_drag():
    canvas = canvas_for(
        {
            'nodes': [
                {'id': 'door', 'label': '门', 'x': 0.2, 'y': 0.3},
            ]
        }
    )
    displayed_positions = []
    canvas.on_change = lambda: displayed_positions.append(
        point_of(canvas.draft.selected(), canvas.draft.nodes)
    )
    pointer(canvas, 'pointerdown', 200, 150)
    pointer(canvas, 'pointermove', 200, 150)
    pointer(canvas, 'pointerup', 200, 150, buttons=0)
    pointer(canvas, 'pointerleave', 200, 150, buttons=0)
    assert displayed_positions == [(0.2, 0.3)]
    pointer(canvas, 'pointerdown', 200, 150)
    pointer(canvas, 'pointermove', 700, 250)
    pointer(canvas, 'pointerup', 700, 250, buttons=0)
    pointer(canvas, 'pointerleave', 700, 250, buttons=0)
    assert displayed_positions == [(0.2, 0.3), (0.2, 0.3), (0.7, 0.5)]


def test_unknown_node_can_be_selected_then_located_without_guessing():
    canvas = canvas_for({'nodes': [{'id': 'room', 'label': '未定位房间'}]})
    canvas.draft.selection = ('node', 'room')
    pointer(canvas, 'pointerdown', 800, 350)
    pointer(canvas, 'pointerup', 800, 350, buttons=0)
    assert point_of(canvas.draft.nodes[0], []) == (0.8, 0.7)


def test_region_draw_resize_and_cancel_keep_valid_bounds():
    canvas = canvas_for({}, mode='region')
    pointer(canvas, 'pointerdown', 700, 400)
    pointer(canvas, 'pointermove', 200, 100)
    pointer(canvas, 'pointerup', 200, 100, buttons=0)
    region = canvas.draft.regions[0]
    assert region['bounds'] == [0.2, 0.2, 0.7, 0.8]
    assert canvas.draft.asset['is_fog_enabled'] is True
    canvas.mode = 'select'
    pointer(canvas, 'pointerdown', 700, 400)
    pointer(canvas, 'pointermove', 900, 450)
    pointer(canvas, 'pointerup', 900, 450, buttons=0)
    assert region['bounds'] == [0.2, 0.2, 0.9, 0.9]
    canvas.mode = 'region'
    pointer(canvas, 'pointerdown', 10, 20)
    pointer(canvas, 'pointermove', 400, 250)
    pointer(canvas, 'pointercancel', 400, 250, buttons=0)
    assert len(canvas.draft.regions) == 1


def test_role_position_drag_breaks_node_reference_and_clamps_to_map():
    original = {'card': {'node_id': 'room'}}
    canvas = canvas_for(
        {'nodes': [{'id': 'room', 'x': 0.3, 'y': 0.4}]},
        mode='character',
        positions=original,
    )
    canvas.character_id = 'card'
    pointer(canvas, 'pointerdown', 300, 200)
    pointer(canvas, 'pointermove', 1300, -100)
    pointer(canvas, 'pointerup', 1300, -100, buttons=0)
    assert canvas.draft.positions['card'] == {'x': 1, 'y': 0}
    assert original['card'] == {'node_id': 'room'}


def test_remove_last_region_keeps_fog_and_removes_draft_grants():
    draft = MapDraft(
        {
            'regions': [
                {
                    'id': 'private',
                    'shape': 'rect',
                    'bounds': [0.1, 0.1, 0.8, 0.8],
                }
            ]
        },
        revealed_regions={'actor': ['private']},
    )
    draft.selection = ('region', 'private')
    draft.remove_selected()
    assert draft.regions == []
    assert draft.revealed_regions['actor'] == []
    assert draft.asset['is_fog_enabled'] is True


@pytest.mark.parametrize('coordinate', [float('nan'), float('inf'), True])
def test_unpositioned_or_invalid_points_are_not_drawn(coordinate):
    draft = MapDraft(
        {
            'nodes': [
                {
                    'id': 'invalid',
                    'label': '应隐藏',
                    'x': coordinate,
                    'y': 0.2,
                },
                {'id': 'unknown', 'label': '尚未定位'},
            ]
        }
    )
    assert '应隐藏' not in map_svg(draft, 1000, 500)
    assert '尚未定位' not in map_svg(draft, 1000, 500)


def test_untrusted_map_labels_cannot_inject_svg_markup():
    draft = MapDraft(
        {
            'nodes': [
                {
                    'id': 'x',
                    'label': '<script>alert(1)</script>',
                    'x': 0.2,
                    'y': 0.3,
                }
            ]
        }
    )
    drawing = map_svg(draft, 1000, 500)
    assert '<script>' not in drawing
    assert '&lt;script&gt;' in drawing
