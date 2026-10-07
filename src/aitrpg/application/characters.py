from typing import Any

from pydantic import Field

from aitrpg.adapters.cards import import_character_xlsx
from aitrpg.domain.models import Actor
from aitrpg.domain.models import Character
from aitrpg.domain.models import Model
from aitrpg.domain.models import Provider
from aitrpg.domain.rules import BACKGROUND_FIELDS
from aitrpg.domain.rules import OCCUPATIONS
from aitrpg.domain.rules import SKILL_BASES
from aitrpg.domain.rules import age_modifiers
from aitrpg.domain.rules import apply_age
from aitrpg.domain.rules import derived_character
from aitrpg.domain.rules import generate_attributes
from aitrpg.domain.rules import occupation_budget
from aitrpg.domain.rules import roll_dice
from aitrpg.domain.rules import validate_allocations


class CharacterConcept(Model):
    name: str
    age: int = Field(default=30, ge=15, le=89)
    sex: str
    era: str = '1920s'
    residence: str
    birthplace: str
    occupation: str
    custom_occupation: dict[str, Any] | None = None
    age_reductions: dict[str, int] = Field(default_factory=dict)
    background: dict[str, str]
    inventory: list[dict[str, Any]] = Field(default_factory=list)
    weapons: list[dict[str, Any]] = Field(default_factory=list)
    assets: dict[str, Any] = Field(default_factory=dict)


class CharacterAllocation(Model):
    selected_skills: list[str]
    occupational: dict[str, int]
    interests: dict[str, int]


class CharacterService:
    def __init__(self, store: Any, provider_client: Any):
        self.store = store
        self.provider_client = provider_client

    def list(self, actor_id: str | None = None) -> list[Character]:
        characters = [
            Character.model_validate(data)
            for data in self.store.list('character')
        ]
        if actor_id is not None:
            characters = [
                character
                for character in characters
                if character.actor_id == actor_id
            ]
        return characters

    def get(self, character_id: str) -> Character:
        data = self.store.get('character', character_id)
        if data is None:
            raise ValueError('找不到角色卡')
        return Character.model_validate(data)

    def save(self, character: Character) -> Character:
        return self._save(character)

    def _save(
        self, character: Character, *, is_imported: bool = False
    ) -> Character:
        if self.store.get('actor', character.actor_id) is None:
            raise ValueError('角色所属模型不存在')
        updated = derived_character(character)
        with self.store.transaction() as transaction:
            previous = transaction.get('character', updated.id)
            if previous:
                if previous.get('locked_game_id'):
                    raise ValueError('游戏占用中的角色卡不能直接修改')
                if previous['version'] != character.version:
                    raise ValueError('角色卡已被更新，请重新读取')
                if previous['actor_id'] != character.actor_id:
                    raise ValueError('不能更改角色所属模型')
                updated.version = previous['version'] + 1
                updated.created_at = Character.model_validate(
                    previous
                ).created_at
            elif updated.locked_game_id:
                raise ValueError('创建角色时不能自行设置游戏占用')
            elif not is_imported:
                self._validate_background(updated.background)
                if not updated.occupation_definition:
                    raise ValueError('新角色必须明确职业定义与点数公式')
                validated = validate_allocations(
                    updated.attributes,
                    updated.occupation_definition,
                    updated.allocations.get('selected_skills', []),
                    updated.allocations.get('occupational', {}),
                    updated.allocations.get('interests', {}),
                )
                if updated.skills != validated:
                    raise ValueError('新角色技能值与标准分配记录不一致')
            transaction.put(
                'character', updated.id, updated.model_dump(mode='json')
            )
        return updated

    def import_xlsx(self, path: str, actor_id: str) -> Character:
        return self._save(
            import_character_xlsx(path, actor_id), is_imported=True
        )

    async def generate(
        self, actor_id: str, concept: str, public_scenario: str = ''
    ) -> Character:
        actor_data = self.store.get('actor', actor_id)
        if actor_data is None:
            raise ValueError('找不到创建角色的模型')
        actor = Actor.model_validate(actor_data)
        if actor.connection != 'api' or not actor.provider_id:
            raise ValueError('被动MCP模型请通过角色保存工具提交完整角色卡')
        provider_data = self.store.get('provider', actor.provider_id)
        if provider_data is None:
            raise ValueError('角色模型没有有效接口配置')
        provider = Provider.model_validate(provider_data)
        attributes, creation_rolls = generate_attributes()
        context = {
            'concept': concept,
            'actor_style': actor.style,
            'public_scenario': public_scenario,
            'rolled_attributes': attributes,
            'occupations': OCCUPATIONS,
            'background_fields': list(BACKGROUND_FIELDS),
            'age_rules': {
                age: age_modifiers(age)
                for age in (15, 20, 30, 40, 50, 60, 70, 80)
            },
        }
        response = await self.provider_client.generate(
            provider,
            '创建标准CoC7人物概念。背景核心六项和life_story要具体完整，'
            'key_connection填最重要的一项背景字段名。未经历的伤痕、'
            '疯狂、法术和神秘接触填无。不能知道主持秘密。职业选择目录'
            '中的名称，或custom_occupation显式给factors、credit_range、'
            'required、choices、free_choices、source；总职业技能八项。'
            '年龄身体扣点age_reductions必须符合age_rules，未指定用30岁。'
            '不要擅自获得法术、额外属性、无依据的贵重资产。',
            context,
            CharacterConcept,
        )
        token_usage = response.usage
        proposal = CharacterConcept.model_validate(response.value)
        self._validate_background(proposal.background)
        definition = proposal.custom_occupation
        if definition is None:
            definition = OCCUPATIONS.get(proposal.occupation)
        if definition is None:
            raise ValueError('职业不在目录内，必须明确自定义职业配置')
        definition = dict(definition)
        if proposal.custom_occupation:
            definition['authority'] = 'keeper_defined'
            if not definition.get('source'):
                raise ValueError('自定义职业必须写明来源或主持裁定')
        attributes, age_rolls = apply_age(
            attributes, proposal.age, proposal.age_reductions
        )
        creation_rolls.extend(age_rolls)
        luck_rolls = [
            roll_dice('3D6*5', reason='创建幸运')
            for _ in range(age_modifiers(proposal.age)['luck_rolls'])
        ]
        creation_rolls.extend(luck_rolls)
        allocation_context = {
            'character': proposal.model_dump(mode='json'),
            'attributes': attributes,
            'occupation': definition,
            'occupational_budget': occupation_budget(definition, attributes),
            'interest_budget': attributes['INT'] * 2,
            'skill_bases': SKILL_BASES,
            'dynamic_bases': {
                '闪避': attributes['DEX'] // 2,
                '母语': attributes['EDU'],
            },
        }
        allocation = None
        error = ''
        for _ in range(3):
            allocation_context['previous_error'] = error
            response = await self.provider_client.generate(
                provider,
                '分配标准7版角色技能点。selected_skills选八项不同职业'
                '技能，严格满足required与choices，专业技能写技艺：摄影、'
                '科学：生物学等具体名称。occupational与interests分别'
                '准确用完点数，数值是增加的点数而非技能总值。职业点可'
                '用于职业技能和信用，兴趣点可用于任何普通技能。信用'
                '必须满足职业范围，克苏鲁神话不得加点，最终技能不超99。',
                allocation_context,
                CharacterAllocation,
            )
            token_usage += response.usage
            allocation = CharacterAllocation.model_validate(response.value)
            try:
                skills = validate_allocations(
                    attributes,
                    definition,
                    allocation.selected_skills,
                    allocation.occupational,
                    allocation.interests,
                )
            except ValueError as exc:
                error = str(exc)
            else:
                break
        else:
            raise ValueError(f'模型连续三次无法合法分配技能：{error}')
        max_hp = (attributes['CON'] + attributes['SIZ']) // 10
        max_mp = attributes['POW'] // 5
        character = Character(
            actor_id=actor_id,
            name=proposal.name,
            occupation=proposal.occupation,
            occupation_definition=definition,
            age=proposal.age,
            sex=proposal.sex,
            era=proposal.era,
            residence=proposal.residence,
            birthplace=proposal.birthplace,
            attributes=attributes,
            skills=skills,
            allocations=allocation.model_dump(mode='json'),
            luck=max(roll.total for roll in luck_rolls),
            max_hp=max_hp,
            current_hp=max_hp,
            max_mp=max_mp,
            current_mp=max_mp,
            current_san=attributes['POW'],
            background=proposal.background,
            inventory=proposal.inventory,
            weapons=proposal.weapons,
            assets=proposal.assets,
            creation_rolls=creation_rolls,
            source='标准CoC7骰属性创建；职业来源见occupation_definition',
        )
        character.allocations['creation_token_usage'] = token_usage
        return self.save(character)

    @staticmethod
    def _validate_background(background: dict[str, str]) -> None:
        if any(
            not background.get(name, '').strip() for name in BACKGROUND_FIELDS
        ):
            raise ValueError('角色必须填写全部背景字段，未经历项目填无')
        if background['key_connection'] not in BACKGROUND_FIELDS[:6]:
            raise ValueError('关键连接须指向六项核心背景之一')
        if any(
            background[name].strip() == '无'
            for name in (*BACKGROUND_FIELDS[:6], 'life_story')
        ):
            raise ValueError('人物核心背景和经历正文不能填无')
