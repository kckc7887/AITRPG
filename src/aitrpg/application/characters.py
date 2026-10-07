import asyncio
import hashlib
import json
from copy import deepcopy
from typing import Any

from pydantic import Field

from aitrpg.adapters.cards import import_character_xlsx
from aitrpg.domain.models import Actor
from aitrpg.domain.models import Character
from aitrpg.domain.models import Model
from aitrpg.domain.models import Provider
from aitrpg.domain.models import Roll
from aitrpg.domain.models import new_id
from aitrpg.domain.models import utc_now
from aitrpg.domain.rules import ATTRIBUTE_NAMES
from aitrpg.domain.rules import BACKGROUND_FIELDS
from aitrpg.domain.rules import OCCUPATIONS
from aitrpg.domain.rules import SKILL_BASES
from aitrpg.domain.rules import age_modifiers
from aitrpg.domain.rules import apply_age
from aitrpg.domain.rules import base_skill
from aitrpg.domain.rules import damage_bonus_build
from aitrpg.domain.rules import derived_character
from aitrpg.domain.rules import generate_attributes
from aitrpg.domain.rules import movement_rate
from aitrpg.domain.rules import normalize_skill
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
        self._generation_locks = {}

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

    def clone(self, character_id: str, name: str | None = None) -> Character:
        copied = self.get(character_id).model_copy(deep=True)
        copied.id = new_id()
        copied.version = 1
        copied.created_at = utc_now()
        copied.locked_game_id = None
        copied.name = (
            name.strip() if name and name.strip() else copied.name + '副本'
        )
        copied.source = f'服务端复制：{character_id}'
        return self._save(copied, is_imported=True)

    def save(self, character: Character) -> Character:
        return self._save(character)

    def _save(
        self,
        character: Character,
        *,
        is_imported: bool = False,
        transaction: Any = None,
    ) -> Character:
        if transaction is None:
            with self.store.transaction() as transaction:
                return self._save(
                    character,
                    is_imported=is_imported,
                    transaction=transaction,
                )
        if transaction.get('actor', character.actor_id) is None:
            raise ValueError('角色所属模型不存在')
        updated = derived_character(character)
        previous = transaction.get('character', updated.id)
        if previous:
            if previous.get('locked_game_id'):
                raise ValueError('游戏占用中的角色卡不能直接修改')
            if previous['version'] != character.version:
                raise ValueError('角色卡已被更新，请重新读取')
            if previous['actor_id'] != character.actor_id:
                raise ValueError('不能更改角色所属模型')
            updated.version = previous['version'] + 1
            updated.created_at = Character.model_validate(previous).created_at
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

    def prepare(
        self,
        actor_id: str,
        age: int = 30,
        age_reductions: dict[str, int] | None = None,
    ) -> dict:
        if type(age) is not int:
            raise ValueError('年龄须为整数')
        modifiers = age_modifiers(age)
        if modifiers['physical'] and age_reductions is None:
            raise ValueError('此年龄需要明确age_reductions，不自动猜测扣点')
        reductions = dict(age_reductions or {})
        if (
            set(reductions) - set(modifiers['allowed'])
            or any(
                type(value) is not int or value < 0
                for value in reductions.values()
            )
            or sum(reductions.values()) != modifiers['physical']
        ):
            raise ValueError('年龄身体扣点不符合标准规则')
        with self.store.transaction() as transaction:
            if transaction.get('actor', actor_id) is None:
                raise ValueError('找不到创建角色的模型身份')
            for previous in transaction.list('creation_draft'):
                if (
                    previous['actor_id'] == actor_id
                    and not previous['is_consumed']
                    and previous['age'] == age
                    and previous['age_reductions'] == reductions
                ):
                    return self._draft_context(previous)
            attributes, rolls = generate_attributes()
            attributes, age_rolls = apply_age(attributes, age, reductions)
            rolls.extend(age_rolls)
            lucky = [
                roll_dice('3D6*5', reason='服务器草稿幸运')
                for _ in range(modifiers['luck_rolls'])
            ]
            rolls.extend(lucky)
            bonus, build = damage_bonus_build(attributes)
            max_hp = (attributes['CON'] + attributes['SIZ']) // 10
            max_mp = attributes['POW'] // 5
            draft = {
                'draft_id': new_id(),
                'actor_id': actor_id,
                'age': age,
                'age_reductions': reductions,
                'attributes': attributes,
                'luck': max(roll.total for roll in lucky),
                'max_hp': max_hp,
                'current_hp': max_hp,
                'max_mp': max_mp,
                'current_mp': max_mp,
                'max_san': 99,
                'current_san': attributes['POW'],
                'movement': movement_rate(attributes, age),
                'damage_bonus': bonus,
                'build': build,
                'creation_rolls': [
                    roll.model_dump(mode='json') for roll in rolls
                ],
                'is_consumed': False,
                'created_at': utc_now().isoformat(),
            }
            transaction.put('creation_draft', draft['draft_id'], draft)
            return self._draft_context(draft)

    @staticmethod
    def _draft_context(draft: dict) -> dict:
        result = deepcopy(draft)
        result.update(
            {
                'character_schema': Character.model_json_schema(),
                'occupations': deepcopy(OCCUPATIONS),
                'skill_bases': dict(SKILL_BASES),
                'dynamic_bases': {
                    '母语': draft['attributes']['EDU'],
                    '闪避': draft['attributes']['DEX'] // 2,
                },
                'background_fields': list(BACKGROUND_FIELDS),
            }
        )
        return result

    def finalise_draft(
        self,
        actor_id: str,
        draft_id: str,
        character: Character,
    ) -> Character:
        with self.store.transaction() as transaction:
            return self._finalise(transaction, actor_id, draft_id, character)

    def _finalise(self, transaction, actor_id, draft_id, character):
        draft = transaction.get('creation_draft', draft_id)
        if not draft or draft['actor_id'] != actor_id:
            raise ValueError('角色草稿不存在或不属于当前模型')
        if draft['is_consumed']:
            raise ValueError('角色草稿已完成，不能重复消费')
        if character.actor_id != actor_id:
            raise ValueError('角色必须属于当前模型')
        if transaction.get('character', character.id):
            raise ValueError('服务器草稿只能创建新角色，不能覆盖已有卡')
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
            if getattr(character, name) != draft[name]:
                raise ValueError(f'客户端不能修改服务器草稿的{name}')
        submitted = character.model_copy(deep=True)
        submitted.creation_rolls = [
            Roll.model_validate(roll) for roll in draft['creation_rolls']
        ]
        submitted.source = '标准CoC7服务器草稿创建'
        saved = self._save(submitted, transaction=transaction)
        draft['is_consumed'] = True
        draft['character_id'] = saved.id
        transaction.put('creation_draft', draft_id, draft)
        return saved

    def import_xlsx(self, path: str, actor_id: str) -> Character:
        return self._save(
            import_character_xlsx(path, actor_id), is_imported=True
        )

    async def generate(
        self, actor_id: str, concept: str, public_scenario: str = ''
    ) -> Character:
        signature = hashlib.sha256(
            json.dumps(
                [actor_id, concept, public_scenario],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        lock = self._generation_locks.setdefault(signature, asyncio.Lock())
        async with lock:
            return await self._generate_attempt(
                actor_id, concept, public_scenario, signature
            )

    async def _generate_attempt(
        self, actor_id, concept, public_scenario, signature
    ):
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
        with self.store.transaction() as transaction:
            attempt = next(
                (
                    item
                    for item in transaction.list('character_attempt')
                    if item['signature'] == signature
                    and item['status'] != 'completed'
                ),
                None,
            )
            if attempt is None:
                attributes, raw_rolls = generate_attributes()
                attempt = {
                    'id': new_id(),
                    'signature': signature,
                    'actor_id': actor_id,
                    'status': 'pending',
                    'raw_attributes': attributes,
                    'raw_rolls': [
                        roll.model_dump(mode='json') for roll in raw_rolls
                    ],
                    'token_usage': 0,
                }
                transaction.put('character_attempt', attempt['id'], attempt)
        try:
            return await self._continue_generation(
                actor, provider, concept, public_scenario, attempt
            )
        except Exception as error:
            attempt.update(status='failed', last_error=str(error))
            self.store.put('character_attempt', attempt['id'], attempt)
            raise

    async def _continue_generation(
        self,
        actor,
        provider,
        concept,
        public_scenario,
        attempt,
    ):
        attributes = attempt['raw_attributes']
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
        if 'draft_id' not in attempt:
            self._concept_context(attempt, context)
            proposal, definition = await self._generate_concept(
                provider, context, attempt
            )
            attributes, age_rolls = apply_age(
                attributes, proposal.age, proposal.age_reductions
            )
            lucky = [
                roll_dice('3D6*5', reason='创建幸运')
                for _ in range(age_modifiers(proposal.age)['luck_rolls'])
            ]
            rolls = [
                Roll.model_validate(item) for item in attempt['raw_rolls']
            ]
            rolls.extend([*age_rolls, *lucky])
            bonus, build = damage_bonus_build(attributes)
            draft = {
                'draft_id': new_id(),
                'actor_id': actor.id,
                'age': proposal.age,
                'age_reductions': proposal.age_reductions,
                'attributes': attributes,
                'luck': max(roll.total for roll in lucky),
                'max_hp': (attributes['CON'] + attributes['SIZ']) // 10,
                'max_mp': attributes['POW'] // 5,
                'max_san': 99,
                'current_san': attributes['POW'],
                'movement': movement_rate(attributes, proposal.age),
                'damage_bonus': bonus,
                'build': build,
                'creation_rolls': [
                    roll.model_dump(mode='json') for roll in rolls
                ],
                'is_consumed': False,
                'created_at': utc_now().isoformat(),
            }
            draft['current_hp'] = draft['max_hp']
            draft['current_mp'] = draft['max_mp']
            attempt.update(
                draft_id=draft['draft_id'],
                proposal=proposal.model_dump(mode='json'),
                definition=definition,
                draft_snapshot=deepcopy(draft),
            )
            with self.store.transaction() as transaction:
                transaction.put('creation_draft', draft['draft_id'], draft)
                transaction.put('character_attempt', attempt['id'], attempt)
        else:
            draft = self.store.get('creation_draft', attempt['draft_id'])
            if draft is None:
                draft = deepcopy(attempt['draft_snapshot'])
                self.store.put('creation_draft', draft['draft_id'], draft)
            proposal = CharacterConcept.model_validate(attempt['proposal'])
            definition = attempt['definition']
        attributes = draft['attributes']
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
        error = attempt.get('last_allocation_error', '')
        old_allocation = attempt.get('model_allocation')
        for _ in range(3):
            allocation_context['previous_error'] = error
            allocation_context['previous_allocation'] = old_allocation
            if old_allocation:
                allocation_context['budget_diagnostics'] = self._diagnostics(
                    old_allocation, allocation_context
                )
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
            attempt['token_usage'] += response.usage
            allocation = CharacterAllocation.model_validate(response.value)
            old_allocation = allocation.model_dump(mode='json')
            attempt['model_allocation'] = old_allocation
            self.store.put('character_attempt', attempt['id'], attempt)
            try:
                allocation, skills, adjustments = self._fit_allocation(
                    attributes, definition, allocation
                )
            except ValueError as exc:
                error = str(exc)
                attempt['last_allocation_error'] = error
                self.store.put('character_attempt', attempt['id'], attempt)
            else:
                break
        else:
            raise ValueError(f'模型连续三次无法合法分配技能：{error}')
        character = Character(
            actor_id=actor.id,
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
            luck=draft['luck'],
            max_hp=draft['max_hp'],
            current_hp=draft['current_hp'],
            max_mp=draft['max_mp'],
            current_mp=draft['current_mp'],
            max_san=draft['max_san'],
            current_san=draft['current_san'],
            movement=draft['movement'],
            damage_bonus=draft['damage_bonus'],
            build=draft['build'],
            background=proposal.background,
            inventory=proposal.inventory,
            weapons=proposal.weapons,
            assets=proposal.assets,
            creation_rolls=[
                Roll.model_validate(item) for item in draft['creation_rolls']
            ],
            source='标准CoC7骰属性创建；职业来源见occupation_definition',
        )
        character.allocations.update(
            creation_token_usage=attempt['token_usage'],
            generation_attempt_id=attempt['id'],
            allocation_adjustments=adjustments,
            model_allocation=old_allocation,
        )
        with self.store.transaction() as transaction:
            saved = self._finalise(
                transaction, actor.id, draft['draft_id'], character
            )
            attempt.update(status='completed', character_id=saved.id)
            transaction.put('character_attempt', attempt['id'], attempt)
            return saved

    @staticmethod
    def _concept_context(attempt, context):
        context['previous_concept'] = attempt.get('previous_concept')
        context['previous_error'] = attempt.get('last_concept_error', '')

    async def _generate_concept(self, provider, context, attempt):
        error = ''
        for _ in range(2):
            response = await self.provider_client.generate(
                provider,
                '创建标准CoC7人物概念。核心六项背景和life_story要具体完整，'
                'key_connection为最重要背景字段名；未经历的伤痕、疯狂、'
                '法术和神秘接触填无。不要知道主持秘密。职业选目录名称，'
                '自定义须明确点数公式/信用范围/八项技能和来源。年龄扣点'
                '按age_rules，未指定30岁。不得挑选更高属性或伪造财富。'
                'previous_error是实际错误，请修正上次完整人物概念。',
                context,
                CharacterConcept,
            )
            attempt['token_usage'] += response.usage
            proposal = CharacterConcept.model_validate(response.value)
            attempt['previous_concept'] = proposal.model_dump(mode='json')
            self.store.put('character_attempt', attempt['id'], attempt)
            try:
                self._validate_background(proposal.background)
                proposal.age_reductions = {
                    name: value
                    for name, value in proposal.age_reductions.items()
                    if value != 0 or name not in ATTRIBUTE_NAMES
                }
                modifiers = age_modifiers(proposal.age)
                if set(proposal.age_reductions) - set(modifiers['allowed']):
                    raise ValueError('年龄扣点包含不可扣减属性')
                if any(
                    value < 0 for value in proposal.age_reductions.values()
                ) or (
                    sum(proposal.age_reductions.values())
                    != modifiers['physical']
                ):
                    raise ValueError('年龄身体扣点总量不符合标准规则')
                simulated = dict(attempt['raw_attributes'])
                for name, value in proposal.age_reductions.items():
                    simulated[name] -= value
                simulated['APP'] -= modifiers['app']
                simulated['EDU'] += modifiers['edu']
                if any(value < 1 for value in simulated.values()):
                    raise ValueError('年龄调整后属性必须大于0')
                definition = proposal.custom_occupation or OCCUPATIONS.get(
                    proposal.occupation
                )
                if not definition:
                    raise ValueError('职业不在目录，须明确自定义职业配置')
                definition = deepcopy(definition)
                if proposal.custom_occupation:
                    definition['authority'] = 'keeper_defined'
                    if not definition.get('source'):
                        raise ValueError('自定义职业必须写明来源或主持裁定')
                credit = definition.get('credit_range')
                if (
                    not isinstance(credit, list)
                    or len(credit) != 2
                    or any(type(value) is not int for value in credit)
                    or not 0 <= credit[0] <= credit[1] <= 99
                ):
                    raise ValueError('职业必须明确信用范围，且在0至99之间')
                occupation_budget(definition, simulated)
            except ValueError as exc:
                error = str(exc)
                attempt['last_concept_error'] = error
                self.store.put('character_attempt', attempt['id'], attempt)
                self._concept_context(attempt, context)
            else:
                return proposal, definition
        raise ValueError(f'模型两次人物概念不合法：{error}')

    @staticmethod
    def _diagnostics(allocation, context):
        occupational = sum(allocation['occupational'].values())
        interests = sum(allocation['interests'].values())
        return {
            'occupational_used': occupational,
            'interest_used': interests,
            'occupational_budget': context['occupational_budget'],
            'interest_budget': context['interest_budget'],
            'occupational_remaining': context['occupational_budget']
            - occupational,
            'interest_remaining': context['interest_budget'] - interests,
        }

    @staticmethod
    def _weighted_pool(budget, weights, capacities):
        result = {name: 0 for name in weights}
        remaining = budget
        while remaining:
            active = [
                name
                for name in weights
                if result[name] < capacities.get(name, 0)
            ]
            if not active:
                raise ValueError('所选技能容量不足，请增加合适的兴趣技能')
            total = sum(weights[name] for name in active)
            weighted = {
                name: (weights[name] if total else 1) for name in active
            }
            total = sum(weighted.values())
            shares = {
                name: remaining * weighted[name] / total for name in active
            }
            grants = {
                name: min(capacities[name] - result[name], int(shares[name]))
                for name in active
            }
            if not sum(grants.values()):
                chosen = max(
                    active, key=lambda name: (shares[name], weights[name])
                )
                grants[chosen] = 1
            for name, grant in grants.items():
                result[name] += grant
                remaining -= grant
        return result

    @classmethod
    def _fit_allocation(cls, attributes, definition, allocation):
        try:
            skills = validate_allocations(
                attributes,
                definition,
                allocation.selected_skills,
                allocation.occupational,
                allocation.interests,
            )
        except ValueError as original_error:
            original_reason = str(original_error)
        else:
            return allocation, skills, []
        selected = [
            normalize_skill(name) for name in allocation.selected_skills
        ]
        if len(set(selected)) != 8 or len(selected) != 8:
            raise ValueError('职业须选择八项不同技能')
        pools = []
        for raw in (allocation.occupational, allocation.interests):
            pool = {}
            for raw_name, value in raw.items():
                name = normalize_skill(raw_name)
                if value < 0:
                    raise ValueError('技能分配点数不能为负数')
                if name == '克苏鲁神话':
                    if value:
                        raise ValueError('标准新卡不能投入克苏鲁神话点数')
                    continue
                base_skill(name, attributes)
                pool[name] = pool.get(name, 0) + value
            pools.append(pool)
        occupational, interests = pools
        if any(
            name not in {*selected, '信用评级'} and value > 0
            for name, value in occupational.items()
        ):
            raise ValueError('职业点只能投入所选职业技能和信用评级')
        lower, upper = definition['credit_range']
        upper = min(99, upper)
        interest_credit = min(
            interests.get('信用评级', 0), max(0, upper - lower)
        )
        occupation_credit = min(
            max(lower, occupational.get('信用评级', lower)),
            upper - interest_credit,
        )
        bases = {
            name: base_skill(name, attributes)
            for name in set(selected) | set(interests)
        }
        interest_weights = {
            name: value
            for name, value in interests.items()
            if name != '信用评级'
        }
        interest_budget = attributes['INT'] * 2 - interest_credit
        if interest_budget < 0:
            raise ValueError('信用投入超过兴趣预算')
        # 先预留兴趣容量，避免职业加点占满同一技能。
        interested = cls._weighted_pool(
            interest_budget,
            interest_weights,
            {name: max(0, 99 - bases[name]) for name in interest_weights},
        )
        occupational_budget = occupation_budget(definition, attributes)
        remaining = occupational_budget - occupation_credit
        if remaining < 0:
            raise ValueError('信用评级下限超过职业预算')
        assigned = cls._weighted_pool(
            remaining,
            {name: occupational.get(name, 0) for name in selected},
            {
                name: max(0, 99 - bases[name] - interested.get(name, 0))
                for name in selected
            },
        )
        assigned['信用评级'] = occupation_credit
        if interest_credit:
            interested['信用评级'] = interest_credit
        fitted = CharacterAllocation(
            selected_skills=selected,
            occupational=assigned,
            interests=interested,
        )
        skills = validate_allocations(
            attributes, definition, selected, assigned, interested
        )
        adjustments = [
            {
                'reason': original_reason,
                'original': allocation.model_dump(mode='json'),
                'adjusted': fitted.model_dump(mode='json'),
            }
        ]
        return fitted, skills, adjustments

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
