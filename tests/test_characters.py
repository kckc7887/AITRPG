from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

import aitrpg.application.characters as characters_module
from aitrpg.adapters.cards import import_character_xlsx
from aitrpg.adapters.storage import Transaction
from aitrpg.application.characters import CharacterAllocation
from aitrpg.application.characters import CharacterConcept
from aitrpg.application.characters import CharacterService
from aitrpg.application.platform import Platform
from aitrpg.config import Settings
from aitrpg.domain.models import Actor
from aitrpg.domain.models import Character
from aitrpg.domain.models import Provider
from aitrpg.domain.rules import BACKGROUND_FIELDS
from aitrpg.domain.rules import OCCUPATIONS
from aitrpg.domain.rules import validate_allocations


class MemoryStore:
    def __init__(self):
        self.data = {}

    def get(self, kind, entity_id):
        return deepcopy(self.data.get(kind, {}).get(entity_id))

    def list(self, kind):
        return list(deepcopy(self.data.get(kind, {})).values())

    def put(self, kind, entity_id, data):
        self.data.setdefault(kind, {})[entity_id] = deepcopy(data)

    @contextmanager
    def transaction(self):
        yield self


def make_character(actor_id='actor'):
    attributes = {
        'STR': 60,
        'CON': 60,
        'SIZ': 60,
        'DEX': 60,
        'APP': 60,
        'INT': 60,
        'POW': 60,
        'EDU': 60,
    }
    selected = [
        '会计',
        '估价',
        '汽车驾驶',
        '历史',
        '图书馆使用',
        '导航',
        '取悦',
        '说服',
    ]
    occupational = {
        '信用评级': 30,
        '会计': 30,
        '估价': 30,
        '汽车驾驶': 30,
        '历史': 30,
        '图书馆使用': 30,
        '导航': 30,
        '取悦': 20,
        '说服': 10,
    }
    interests = {'图书馆使用': 20, '侦查': 30, '聆听': 30, '潜行': 40}
    background = {key: '完整具体的人物背景' for key in BACKGROUND_FIELDS}
    background['key_connection'] = 'significant_people'
    return Character(
        actor_id=actor_id,
        name='林医生',
        occupation='古董商',
        occupation_definition=OCCUPATIONS['古董商'],
        allocations={
            'selected_skills': selected,
            'occupational': occupational,
            'interests': interests,
        },
        attributes=attributes,
        skills=validate_allocations(
            attributes,
            OCCUPATIONS['古董商'],
            selected,
            occupational,
            interests,
        ),
        background=background,
        max_hp=12,
        current_hp=7,
        max_mp=12,
        current_mp=4,
        current_san=39,
    )


def test_save_refuses_game_lock_and_stale_edits():
    store = MemoryStore()
    actor = Actor(id='actor', name='模型')
    store.put('actor', actor.id, actor.model_dump(mode='json'))
    service = CharacterService(store, None)
    first = service.save(make_character())
    first.name = '改名'
    second = service.save(first)
    assert second.version == 2
    with pytest.raises(ValueError, match='已被更新'):
        service.save(first)
    locked = second.model_dump(mode='json')
    locked['locked_game_id'] = 'ongoing-game'
    store.put('character', second.id, locked)
    with pytest.raises(ValueError, match='占用'):
        service.save(second)
    assert service.get(second.id).locked_game_id == 'ongoing-game'


def test_import_preserves_runtime_values_and_recalculates_formula_fields(
    tmp_path,
):
    path = tmp_path / '调查员.xlsx'
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '人物卡'
    for address in ('U3', 'U5', 'U7', 'AA3', 'AA5', 'AA7', 'AG3', 'AG5'):
        sheet[address] = 60
    sheet.merge_cells('E3:Q3')
    sheet['E3'] = '陈调查员'
    sheet['E6'] = 30
    sheet['E10'] = 4
    sheet['N10'] = 29
    sheet['W10'] = 2
    sheet['G10'] = '=999'
    sheet['P10'] = '=999'
    sheet['Y10'] = '=999'
    sheet['AB35'] = '侦查'
    sheet['AF35'] = 25
    sheet['AH35'] = 6
    sheet['AJ35'] = 30
    sheet['AL35'] = 10
    sheet['AN35'] = '=999'
    sheet['X35'] = '☑'
    workbook.save(path)
    imported = import_character_xlsx(path, 'actor')
    assert imported.name == '陈调查员'
    assert imported.current_hp == 4
    assert imported.current_mp == 2
    assert imported.current_san == 29
    assert imported.max_hp == 12
    assert imported.max_mp == 12
    assert imported.skills['侦查'] == 71
    assert imported.skill_marks == ['侦查']
    platform = Platform(Settings(data_dir=tmp_path / 'platform'))
    platform.save_actor(Actor(id='actor', name='被动模型', connection='mcp'))
    stored = platform.characters.import_xlsx(str(path), 'actor')
    assert platform.store.get('character', stored.id)['current_hp'] == 4
    assert platform.characters.list('actor')[0].current_san == 29


def test_blank_template_is_not_a_playable_character(tmp_path):
    workbook = Workbook()
    workbook.active.title = '人物卡'
    path = tmp_path / '空白.xlsx'
    workbook.save(path)
    with pytest.raises(ValueError, match='缺少数值'):
        import_character_xlsx(path, 'actor')


def test_profession_and_interest_pools_are_additive_and_separate():
    attributes = make_character().attributes
    definition = OCCUPATIONS['古董商']
    selected = [
        '会计',
        '估价',
        '汽车驾驶',
        '历史',
        '图书馆使用',
        '导航',
        '取悦',
        '说服',
    ]
    occupational = {
        '信用评级': 30,
        '会计': 30,
        '估价': 30,
        '汽车驾驶': 30,
        '历史': 30,
        '图书馆使用': 30,
        '导航': 30,
        '取悦': 20,
        '说服': 10,
    }
    skills = validate_allocations(
        attributes,
        definition,
        selected,
        occupational,
        {'图书馆使用': 20, '侦查': 30, '聆听': 30, '潜行': 40},
    )
    assert skills['图书馆使用'] == 70
    assert skills['侦查'] == 55
    assert skills['信用评级'] == 30
    with pytest.raises(ValueError, match='职业点数'):
        validate_allocations(
            attributes, definition, selected, {'会计': 1}, {'侦查': 120}
        )


async def test_generation_corrects_invalid_allocation_without_changing_dice(
    monkeypatch,
    tmp_path,
):
    actor = Actor(id='actor', name='模型', provider_id='provider')
    provider = Provider(
        id='provider', name='接口', base_url='https://example.test'
    )
    monkeypatch.setattr(
        'aitrpg.application.characters.generate_attributes',
        lambda: (dict(make_character().attributes), []),
    )
    background = dict(make_character().background)

    class ModelClient:
        calls = 0
        budgets = []

        async def generate(self, provider, system, context, output_type):
            self.calls += 1
            if output_type is CharacterConcept:
                value = CharacterConcept(
                    name='少年古董商',
                    age=19,
                    sex='女',
                    residence='上海',
                    birthplace='苏州',
                    occupation='古董商',
                    age_reductions={'SIZ': 5},
                    background=background,
                )
            else:
                self.budgets.append(context['occupational_budget'])
                values = {
                    '信用评级': 30,
                    '会计': 30,
                    '估价': 30,
                    '汽车驾驶': 30,
                    '历史': 20,
                    '图书馆使用': 20,
                    '导航': 20,
                    '取悦': 20,
                    '说服': 20,
                }
                if self.calls == 2:
                    values['会计'] = 1
                value = CharacterAllocation(
                    selected_skills=[
                        '会计',
                        '估价',
                        '汽车驾驶',
                        '历史',
                        '图书馆使用',
                        '导航',
                        '取悦',
                        '说服',
                    ],
                    occupational=values,
                    interests={
                        '侦查': 30,
                        '聆听': 30,
                        '潜行': 30,
                        '图书馆使用': 30,
                    },
                )
            return SimpleNamespace(value=value, usage=10)

    client = ModelClient()
    platform = Platform(Settings(data_dir=tmp_path / 'platform'), client)
    platform.save_provider(provider)
    platform.save_actor(actor)
    service = platform.characters
    character = await service.generate('actor', '上海少年古董商')
    assert client.calls == 2
    assert client.budgets == [220]
    assert character.attributes['SIZ'] == 55
    assert character.attributes['EDU'] == 55
    assert character.max_hp == 11
    assert character.skills['图书馆使用'] == 73
    assert sum(character.allocations['occupational'].values()) == 220
    assert sum(character.allocations['interests'].values()) == 120
    assert character.allocations['allocation_adjustments']
    assert service.get(character.id).name == '少年古董商'


def prepared_platform(tmp_path, monkeypatch):
    class Dice:
        def randint(self, minimum, maximum):
            return 4 if maximum == 6 else 10

    monkeypatch.setattr('aitrpg.domain.rules.random.SystemRandom', Dice)
    platform = Platform(Settings(data_dir=tmp_path / 'draft-platform'))
    for actor_id in ('owner', 'other'):
        platform.save_actor(
            Actor(id=actor_id, name=actor_id, connection='mcp')
        )
    return platform


def card_from_draft(draft):
    character = make_character(actor_id=draft['actor_id'])
    for name in (
        'attributes',
        'age',
        'luck',
        'max_hp',
        'max_mp',
        'max_san',
        'current_hp',
        'current_mp',
        'current_san',
        'movement',
        'damage_bonus',
        'build',
    ):
        setattr(character, name, deepcopy(draft[name]))
    character.allocations['occupational'] = {
        '信用评级': 30,
        '会计': 35,
        '估价': 35,
        '汽车驾驶': 30,
        '历史': 30,
        '图书馆使用': 30,
        '导航': 30,
        '取悦': 30,
        '说服': 30,
    }
    character.allocations['interests'] = {
        '图书馆使用': 20,
        '侦查': 40,
        '聆听': 40,
        '潜行': 40,
    }
    character.skills = validate_allocations(
        character.attributes,
        character.occupation_definition,
        character.allocations['selected_skills'],
        character.allocations['occupational'],
        character.allocations['interests'],
    )
    return character


def test_prepare_reuses_draw_and_requires_explicit_age_reductions(
    tmp_path,
    monkeypatch,
):
    platform = prepared_platform(tmp_path, monkeypatch)
    first = platform.characters.prepare('owner')
    second = platform.characters.prepare('owner')
    assert first['draft_id'] == second['draft_id']
    assert first['max_hp'] == 13
    assert first['attributes']['EDU'] == 70
    assert first['luck'] == 60
    with pytest.raises(ValueError, match='age_reductions'):
        platform.characters.prepare('owner', age=15)
    assert len(platform.store.list('creation_draft')) == 1


def test_finalise_rejects_owner_or_attribute_forgery_and_consumes_once(
    tmp_path,
    monkeypatch,
):
    platform = prepared_platform(tmp_path, monkeypatch)
    draft = platform.characters.prepare('owner')
    character = card_from_draft(draft)
    with pytest.raises(ValueError, match='不属于'):
        platform.characters.finalise_draft(
            'other', draft['draft_id'], character
        )
    forged = character.model_copy(deep=True)
    forged.attributes['STR'] += 1
    with pytest.raises(ValueError, match='attributes'):
        platform.characters.finalise_draft('owner', draft['draft_id'], forged)
    saved = platform.characters.finalise_draft(
        'owner', draft['draft_id'], character
    )
    assert saved.attributes['STR'] == 60
    assert saved.current_hp == 13
    assert platform.characters.get(saved.id).name == character.name
    replacement = character.model_copy(update={'id': 'another-card'})
    with pytest.raises(ValueError, match='重复消费'):
        platform.characters.finalise_draft(
            'owner', draft['draft_id'], replacement
        )
    assert platform.store.get('character', 'another-card') is None


def test_draft_consumption_and_character_save_roll_back_together(
    tmp_path,
    monkeypatch,
):
    platform = prepared_platform(tmp_path, monkeypatch)
    draft = platform.characters.prepare('owner')
    character = card_from_draft(draft)
    original_put = Transaction.put

    def failing_put(transaction, kind, identifier, body, **kwargs):
        if kind == 'creation_draft' and body['is_consumed']:
            raise RuntimeError('模拟写入草稿消费失败')
        return original_put(transaction, kind, identifier, body, **kwargs)

    monkeypatch.setattr(Transaction, 'put', failing_put)
    with pytest.raises(RuntimeError, match='消费失败'):
        platform.characters.finalise_draft(
            'owner', draft['draft_id'], character
        )
    assert platform.store.get('character', character.id) is None
    assert not platform.store.get('creation_draft', draft['draft_id'])[
        'is_consumed'
    ]


def generation_platform(tmp_path, client):
    platform = Platform(Settings(data_dir=tmp_path / 'generation'), client)
    provider = platform.save_provider(
        Provider(
            id='generator-provider',
            name='生成模型',
            base_url='https://example.test',
        )
    )
    platform.save_actor(
        Actor(id='generator', name='生成身份', provider_id=provider.id)
    )
    return platform


class GeneratorClient:
    def __init__(self, bad_selection=False, bad_age=False):
        self.bad_selection = bad_selection
        self.bad_age = bad_age
        self.contexts = []
        self.concept_calls = 0

    async def generate(self, provider, system, context, output_type):
        self.contexts.append(deepcopy(context))
        if output_type is CharacterConcept:
            self.concept_calls += 1
            reductions = {name: 0 for name in make_character().attributes}
            if self.bad_age and self.concept_calls == 1:
                reductions['STR'] = 5
            value = CharacterConcept(
                name='古董商',
                age=30,
                sex='女',
                residence='上海',
                birthplace='苏州',
                occupation='古董商',
                age_reductions=reductions,
                background=make_character().background,
            )
        else:
            selected = make_character().allocations['selected_skills']
            if self.bad_selection:
                selected = selected[:-1]
            value = CharacterAllocation(
                selected_skills=selected,
                occupational={
                    name: 20
                    for name in make_character().allocations['selected_skills']
                },
                interests={
                    '侦查': 20,
                    '聆听': 20,
                    '潜行': 20,
                    '图书馆使用': 20,
                },
            )
        return SimpleNamespace(value=value, usage=10)


async def test_zero_age_deductions_and_arithmetic_errors_are_repaired(
    tmp_path,
    monkeypatch,
):
    client = GeneratorClient()
    platform = generation_platform(tmp_path, client)
    monkeypatch.setattr(
        'aitrpg.application.characters.generate_attributes',
        lambda: (make_character().attributes, []),
    )
    character = await platform.characters.generate('generator', '古董商概念')
    assert client.concept_calls == 1
    assert character.age == 30
    assert character.occupation == '古董商'
    assert character.skills['信用评级'] == 30
    assert sum(character.allocations['occupational'].values()) == (
        character.attributes['EDU'] * 4
    )
    assert sum(character.allocations['interests'].values()) == 120
    assert character.skills['侦查'] == 55
    assert character.allocations['allocation_adjustments']


async def test_concept_repair_receives_error_and_reuses_raw_attributes(
    tmp_path,
    monkeypatch,
):
    client = GeneratorClient(bad_age=True)
    platform = generation_platform(tmp_path, client)
    raw_calls = []

    def raw_attributes():
        raw_calls.append('draw')
        return make_character().attributes, []

    monkeypatch.setattr(
        'aitrpg.application.characters.generate_attributes', raw_attributes
    )
    result = await platform.characters.generate('generator', '古董商概念')
    assert client.concept_calls == 2
    assert '扣点总量' in client.contexts[1]['previous_error']
    assert client.contexts[1]['previous_concept']['age_reductions']['STR'] == 5
    assert raw_calls == ['draw']
    assert result.attributes['STR'] == 60


async def test_failed_generation_reuses_draft_on_new_service_retry(
    tmp_path,
    monkeypatch,
):
    client = GeneratorClient(bad_selection=True)
    platform = generation_platform(tmp_path, client)
    raw_calls = []
    age_calls = []
    original_age = characters_module.apply_age

    def raw_attributes():
        raw_calls.append('draw')
        return make_character().attributes, []

    def tracked_age(*args, **kwargs):
        age_calls.append('age')
        return original_age(*args, **kwargs)

    monkeypatch.setattr(
        'aitrpg.application.characters.generate_attributes', raw_attributes
    )
    monkeypatch.setattr('aitrpg.application.characters.apply_age', tracked_age)
    with pytest.raises(ValueError, match='连续三次'):
        await platform.characters.generate('generator', '同一失败概念')
    diagnostics = next(
        context['budget_diagnostics']
        for context in client.contexts
        if 'budget_diagnostics' in context
    )
    assert diagnostics['occupational_used'] == 160
    assert diagnostics['interest_used'] == 80
    assert diagnostics['interest_remaining'] == 40
    attempt = platform.store.list('character_attempt')[0]
    draft = platform.store.get('creation_draft', attempt['draft_id'])
    client.bad_selection = False
    restored = Platform(Settings(data_dir=tmp_path / 'generation'), client)
    result = await restored.characters.generate('generator', '同一失败概念')
    assert raw_calls == ['draw']
    assert age_calls == ['age']
    assert client.concept_calls == 1
    assert result.attributes == draft['attributes']
    assert result.luck == draft['luck']
    assert [roll.id for roll in result.creation_rolls] == [
        roll['id'] for roll in draft['creation_rolls']
    ]


def test_clone_keeps_growth_without_modifying_locked_original(tmp_path):
    platform = Platform(Settings(data_dir=tmp_path / 'cloning'))
    platform.save_actor(Actor(id='actor', name='模型', connection='mcp'))
    original = make_character()
    original.skills['侦查'] = 91
    original.current_hp = 4
    original.experiences = ['完成了上一模组']
    original.locked_game_id = 'active-game'
    platform.store.put(
        'character', original.id, original.model_dump(mode='json')
    )
    copied = platform.characters.clone(original.id, '另一个世界的调查员')
    assert copied.id != original.id
    assert copied.locked_game_id is None
    assert copied.current_hp == 4
    assert copied.skills['侦查'] == 91
    assert copied.experiences == ['完成了上一模组']
    assert platform.characters.get(original.id).locked_game_id == 'active-game'
