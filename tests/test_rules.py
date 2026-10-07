import pytest

from aitrpg.domain.models import Character
from aitrpg.domain.models import Roll
from aitrpg.domain.rules import apply_age
from aitrpg.domain.rules import apply_command
from aitrpg.domain.rules import movement_rate
from aitrpg.domain.rules import opposed_result
from aitrpg.domain.rules import roll_dice
from aitrpg.domain.rules import roll_percentile
from aitrpg.domain.rules import skill_check
from aitrpg.domain.rules import success_level
from aitrpg.domain.rules import weapon_damage


class FixedDice:
    def __init__(self, values):
        self.values = iter(values)

    def randint(self, minimum, maximum):
        result = next(self.values)
        if not minimum <= result <= maximum:
            raise ValueError('测试提供了超出骰子范围的值')
        return result


@pytest.fixture
def character():
    return Character(
        actor_id='actor',
        name='调查员',
        attributes={
            'STR': 60,
            'CON': 60,
            'SIZ': 60,
            'DEX': 60,
            'APP': 60,
            'INT': 60,
            'POW': 60,
            'EDU': 60,
        },
        skills={
            '侦查': 45,
            '闪避': 30,
            '急救': 80,
            '医学': 80,
            '信用评级': 20,
            '克苏鲁神话': 0,
        },
        max_hp=12,
        current_hp=12,
        max_mp=12,
        current_mp=12,
        current_san=60,
    )


def test_dice_supports_creation_math_and_rejects_execution():
    roll = roll_dice('(2D6+6)*5', rng=FixedDice([3, 4]))
    assert roll.total == 65
    assert roll.dice == [3, 4]
    for expression in ('__import__("os")', '2**8', '1000D6', '1D1'):
        with pytest.raises(ValueError):
            roll_dice(expression)


def test_bonus_zero_digit_compares_hundred_with_ten():
    bonus = roll_percentile(1, rng=FixedDice([0, 0, 1]))
    penalty = roll_percentile(-1, rng=FixedDice([0, 0, 1]))
    assert bonus.total == 10
    assert penalty.total == 100


@pytest.mark.parametrize(
    ('target', 'result', 'expected'),
    [
        (49, 96, 'fumble'),
        (50, 96, 'failure'),
        (100, 100, 'fumble'),
        (60, 1, 'critical'),
        (60, 12, 'extreme'),
        (60, 30, 'hard'),
        (60, 60, 'regular'),
        (60, 61, 'failure'),
    ],
)
def test_percentile_thresholds(target, result, expected):
    assert success_level(target, result) == expected


def test_defense_ties_are_not_general_opposed_ties():
    first = Roll(
        expression='1D100',
        total=40,
        details={'level': 'regular', 'target': 40},
    )
    second = Roll(
        expression='1D100',
        total=55,
        details={'level': 'regular', 'target': 60},
    )
    assert opposed_result(first, second) == 'defender'
    assert opposed_result(first, second, defense='fight_back') == 'attacker'
    assert opposed_result(first, second, defense='dodge') == 'defender'


def test_combat_and_sanity_cannot_be_pushed(character):
    with pytest.raises(ValueError, match='不能孤注'):
        skill_check(character, 'SAN', is_pushed=True)
    with pytest.raises(ValueError, match='不能孤注'):
        skill_check(character, '侦查', is_pushed=True, mode='combat')


def test_age_edu_checks_use_updated_edu_and_move_equal_is_eight(character):
    attributes, rolls = apply_age(
        character.attributes,
        40,
        {'STR': 5},
        rng=FixedDice([70, 8, 65]),
    )
    assert attributes['EDU'] == 68
    assert attributes['APP'] == 55
    assert attributes['STR'] == 55
    assert len(rolls) == 3
    assert movement_rate(character.attributes, 30) == 8
    assert movement_rate(character.attributes, 40) == 7


def test_accumulated_minor_damage_does_not_create_major_wound(character):
    character, _, _ = apply_command(character, 'damage', {'amount': 4})
    character, _, _ = apply_command(character, 'damage', {'amount': 4})
    character, _, _ = apply_command(character, 'damage', {'amount': 4})
    assert character.current_hp == 0
    assert character.conditions == ['unconscious']


def test_major_wound_and_death_use_single_damage(character):
    wounded, _, _ = apply_command(
        character, 'damage', {'amount': 6}, rng=FixedDice([0, 8])
    )
    assert wounded.current_hp == 6
    assert 'major_wound' in wounded.conditions
    assert 'unconscious' in wounded.conditions
    assert character.current_hp == 12
    dead, _, _ = apply_command(character, 'damage', {'amount': 12})
    assert 'dead' in dead.conditions


def test_daily_sanity_threshold_keeps_start_of_day_baseline(character):
    first, _, _ = apply_command(
        character,
        'sanity',
        {'success_loss': '4', 'failure_loss': '4'},
        day=1,
        rng=FixedDice([0, 2]),
    )
    second, _, _ = apply_command(
        first,
        'sanity',
        {'success_loss': '4', 'failure_loss': '4'},
        day=1,
        rng=FixedDice([0, 2]),
    )
    third, _, _ = apply_command(
        second,
        'sanity',
        {'success_loss': '4', 'failure_loss': '4'},
        day=1,
        rng=FixedDice([0, 2]),
    )
    assert second.current_san == 52
    assert 'indefinite_insanity' not in second.conditions
    assert third.current_san == 48
    assert 'indefinite_insanity' in third.conditions
    tomorrow, _, _ = apply_command(
        second,
        'sanity',
        {'success_loss': '4', 'failure_loss': '4'},
        day=2,
        rng=FixedDice([0, 2]),
    )
    assert 'indefinite_insanity' not in tomorrow.conditions


def test_temporary_insanity_uses_int_success_and_hours(character):
    changed, _, info = apply_command(
        character,
        'sanity',
        {'success_loss': '0', 'failure_loss': '5'},
        rng=FixedDice([0, 8, 0, 2, 3]),
    )
    assert changed.current_san == 55
    assert info['temporary_insanity_hours'] == 3
    recovered, _, _ = apply_command(changed, 'recover', {'hours': 3})
    assert 'temporary_insanity' not in recovered.conditions


def test_natural_healing_is_daily_not_per_wound(character):
    injured, _, _ = apply_command(character, 'damage', {'amount': 3})
    injured, _, _ = apply_command(injured, 'damage', {'amount': 3})
    recovered, _, _ = apply_command(injured, 'recover', {'days': 1})
    assert recovered.current_hp == 7


def test_same_wound_cannot_be_farmed_with_first_aid(character):
    injured, _, _ = apply_command(character, 'damage', {'amount': 3})
    healed, _, _ = apply_command(
        injured, 'heal', {'source': 'first_aid'}, rng=FixedDice([0, 2])
    )
    assert healed.current_hp == 10
    with pytest.raises(ValueError, match='不能重复'):
        apply_command(healed, 'heal', {'source': 'first_aid'})


def test_growth_changes_only_marked_skill_and_consumes_mark(character):
    marked, _, _ = apply_command(character, 'mark_skill', {'skill': '侦查'})
    grown, _, _ = apply_command(marked, 'grow', {}, rng=FixedDice([73, 7]))
    assert grown.skills['侦查'] == 52
    assert grown.skill_marks == []
    assert character.skills['侦查'] == 45
    with pytest.raises(ValueError, match='成长标记'):
        apply_command(character, 'grow', {'skills': ['侦查']})


def test_extreme_damage_distinguishes_impaling_and_fight_back():
    blunt = weapon_damage('1D6', '1D4', is_extreme=True)
    knife = weapon_damage(
        '1D4',
        '1D4',
        is_extreme=True,
        is_impaling=True,
        rng=FixedDice([2, 3]),
    )
    reaction = weapon_damage(
        '1D6',
        '1D4',
        is_extreme=True,
        is_fight_back=True,
        rng=FixedDice([2, 3]),
    )
    assert blunt.total == 10
    assert knife.total == 9
    assert reaction.total == 5
    imported = weapon_damage('1D3+DB', '1D4', rng=FixedDice([2, 3]))
    assert imported.total == 5
    weak = weapon_damage('1D3', '-2', rng=FixedDice([1]))
    assert weak.total == 0
