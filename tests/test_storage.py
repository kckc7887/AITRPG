import pytest

from aitrpg.adapters.storage import Store


def test_failed_state_change_rolls_back_event_and_character(tmp_path):
    store = Store(tmp_path / 'game.sqlite3')
    store.put('character', 'card', {'hp': 8})
    with pytest.raises(ValueError, match='裁定失败'):
        with store.transaction() as transaction:
            transaction.put('character', 'card', {'hp': 3})
            transaction.append_event('game', 'damage', {'amount': 5})
            raise ValueError('裁定失败')
    assert store.get('character', 'card')['hp'] == 8
    assert store.events('game') == []
