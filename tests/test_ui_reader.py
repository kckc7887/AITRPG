from types import SimpleNamespace

import aitrpg.ui
from aitrpg.ui import GameReader


class Element:
    def __init__(self):
        self.text = ''
        self.visible = True
        self.value = None
        self.scrolls = []

    def set_text(self, text):
        self.text = text

    def set_visibility(self, value):
        self.visible = value

    def set_value(self, value):
        self.value = value

    def scroll_to(self, *, percent):
        self.scrolls.append(percent)


class EventPanel:
    def __init__(self):
        self.records = []
        self.clears = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def clear(self):
        self.clears += 1
        self.records.clear()


def events_through(last):
    return [
        {
            'id': f'event-{sequence}',
            'sequence': sequence,
            'kind': 'narration',
            'data': {'text': f'现场记录 {sequence}'},
        }
        for sequence in range(1, last + 1)
    ]


def reader_for(monkeypatch, last):
    reader = object.__new__(GameReader)
    reader.names = {}
    reader.event_limit = 80
    reader.event_filter = 'story'
    reader.event_search = ''
    reader.seen_ids = set()
    reader.displayed_ids = []
    reader.is_following = True
    reader.event_ceiling = None
    reader.view = {'events': events_through(last)}
    reader.events_panel = EventPanel()
    for name in (
        'empty_label',
        'older_button',
        'follow_checkbox',
        'new_events_button',
        'story_scroll',
    ):
        setattr(reader, name, Element())
    monkeypatch.setattr(
        aitrpg.ui,
        '_render_event',
        lambda event, names: reader.events_panel.records.append(
            event['data']['text']
        ),
    )
    reader._append_events()
    return reader


def test_paused_reader_keeps_eighty_records_until_jump_latest(monkeypatch):
    reader = reader_for(monkeypatch, 80)
    original = list(reader.events_panel.records)
    clears = reader.events_panel.clears
    reader._follow_changed(SimpleNamespace(value=False))
    reader.story_scroll.scrolls.clear()
    reader.view['events'] = events_through(100)
    reader._append_events()
    assert reader.events_panel.records == original
    assert reader.events_panel.clears == clears
    assert reader.story_scroll.scrolls == []
    assert reader.new_events_button.visible
    assert '20' in reader.new_events_button.text
    reader.jump_latest()
    assert reader.events_panel.records == [
        f'现场记录 {sequence}' for sequence in range(21, 101)
    ]
    assert reader.story_scroll.scrolls[-1] == 1
    assert not reader.new_events_button.visible


def test_loading_older_keeps_same_top_when_new_events_arrive(monkeypatch):
    reader = reader_for(monkeypatch, 240)
    reader.load_older()
    expected = [f'现场记录 {sequence}' for sequence in range(81, 241)]
    assert reader.events_panel.records == expected
    reader.story_scroll.scrolls.clear()
    clears = reader.events_panel.clears
    reader.view['events'] = events_through(250)
    reader._append_events()
    assert reader.events_panel.records == expected
    assert reader.events_panel.clears == clears
    assert reader.story_scroll.scrolls == []
    assert '10' in reader.new_events_button.text
    reader.load_older()
    assert reader.events_panel.records == [
        f'现场记录 {sequence}' for sequence in range(1, 241)
    ]
    assert 1 not in reader.story_scroll.scrolls
    assert reader.new_events_button.visible
    assert '10' in reader.new_events_button.text
