import ast
import math
import random
import re
from typing import Any

from aitrpg.domain.models import Character
from aitrpg.domain.models import Roll
from aitrpg.domain.models import new_id

ATTRIBUTE_NAMES = ('STR', 'CON', 'SIZ', 'DEX', 'APP', 'INT', 'POW', 'EDU')
SUCCESS_LEVELS = {
    'fumble': 0,
    'failure': 1,
    'regular': 2,
    'hard': 3,
    'extreme': 4,
    'critical': 5,
}
DIFFICULTIES = {'regular': 2, 'hard': 3, 'extreme': 4}
BACKGROUND_FIELDS = (
    'personal_description',
    'ideology_beliefs',
    'significant_people',
    'meaningful_locations',
    'treasured_possessions',
    'traits',
    'injuries_scars',
    'phobias_manias',
    'tomes_spells_artifacts',
    'strange_encounters',
    'life_story',
    'key_connection',
)
SKILL_BASES = {
    '会计': 5,
    '人类学': 1,
    '估价': 5,
    '考古学': 1,
    '取悦': 15,
    '攀爬': 20,
    '计算机使用': 5,
    '信用评级': 0,
    '克苏鲁神话': 0,
    '乔装': 5,
    '汽车驾驶': 20,
    '电气维修': 10,
    '电子学': 1,
    '话术': 5,
    '格斗：斗殴': 25,
    '射击：手枪': 20,
    '射击：步枪/霰弹枪': 25,
    '射击：弓': 15,
    '射击：冲锋枪': 15,
    '射击：机枪': 10,
    '射击：重武器': 10,
    '射击：火焰喷射器': 10,
    '格斗：斧': 15,
    '格斗：剑': 20,
    '格斗：矛': 20,
    '格斗：绞索': 15,
    '格斗：链锯': 10,
    '格斗：连枷': 10,
    '格斗：鞭': 5,
    '急救': 30,
    '历史': 5,
    '恐吓': 15,
    '跳跃': 20,
    '法律': 5,
    '图书馆使用': 20,
    '聆听': 20,
    '锁匠': 1,
    '机械维修': 10,
    '医学': 1,
    '博物学': 10,
    '导航': 10,
    '神秘学': 5,
    '操作重型机械': 1,
    '说服': 10,
    '精神分析': 1,
    '心理学': 10,
    '骑术': 5,
    '妙手': 10,
    '侦查': 25,
    '潜行': 20,
    '游泳': 20,
    '投掷': 20,
    '追踪': 10,
    '动物驯养': 5,
    '潜水': 1,
    '爆破': 1,
    '读唇': 1,
    '催眠': 1,
    '炮术': 1,
}
SCIENCE_SPECIALIZATIONS = {
    '天文学',
    '生物学',
    '植物学',
    '化学',
    '密码学',
    '地质学',
    '药剂学',
    '物理学',
    '动物学',
}
SKILL_ALIASES = {
    '力量': 'STR',
    '体质': 'CON',
    '体型': 'SIZ',
    '敏捷': 'DEX',
    '外貌': 'APP',
    '智力': 'INT',
    '意志': 'POW',
    '意志力': 'POW',
    '教育': 'EDU',
    '教育程度': 'EDU',
    'strength': 'STR',
    'constitution': 'CON',
    'size': 'SIZ',
    'dexterity': 'DEX',
    'appearance': 'APP',
    'intelligence': 'INT',
    'power': 'POW',
    'education': 'EDU',
    '药学': '科学：药剂学',
    '药剂学': '科学：药剂学',
    '科学：药学': '科学：药剂学',
    'pharmacy': '科学：药剂学',
    'science (pharmacy)': '科学：药剂学',
    '图书馆': '图书馆使用',
    '斗殴': '格斗：斗殴',
    '手枪': '射击：手枪',
    '侦察': '侦查',
    '闪躲': '闪避',
    '信用': '信用评级',
    '信誉': '信用评级',
    '神话': '克苏鲁神话',
    'spot hidden': '侦查',
    'library use': '图书馆使用',
    'first aid': '急救',
    'medicine': '医学',
    'dodge': '闪避',
    'fighting (brawl)': '格斗：斗殴',
    'psychology': '心理学',
    'listen': '聆听',
    'stealth': '潜行',
    'credit rating': '信用评级',
    'cthulhu mythos': '克苏鲁神话',
    'own language': '母语',
}
SOCIAL_SKILLS = ['取悦', '话术', '恐吓', '说服']
OFFICIAL_SOURCE = 'https://www.chaosium.com/cthulhu-character-sheets/'
REFERENCE_SOURCE = 'CY23.5 职业参考；标准7版，官方规则优先'
OCCUPATIONS = {
    '古董商': {
        'factors': {'EDU': 4},
        'credit_range': [30, 50],
        'required': ['会计', '估价', '汽车驾驶', '历史', '图书馆使用', '导航'],
        'choices': [SOCIAL_SKILLS, SOCIAL_SKILLS],
        'free_choices': 0,
        'source': 'Chaosium 调查员手册官方商品页职业摘图',
        'authority': 'official',
    },
    '医生': {
        'factors': {'EDU': 4},
        'credit_range': [30, 80],
        'required': [
            '急救',
            '外语：拉丁语',
            '医学',
            '心理学',
            '科学：生物学',
            '科学：药剂学',
        ],
        'choices': [],
        'free_choices': 2,
        'source': REFERENCE_SOURCE,
        'authority': 'reference',
    },
    '教授': {
        'factors': {'EDU': 4},
        'credit_range': [20, 70],
        'required': ['图书馆使用', '外语', '母语', '心理学'],
        'choices': [],
        'free_choices': 4,
        'source': REFERENCE_SOURCE,
        'authority': 'reference',
    },
    '记者': {
        'factors': {'EDU': 4},
        'credit_range': [9, 30],
        'required': ['技艺：摄影', '历史', '图书馆使用', '母语', '心理学'],
        'choices': [SOCIAL_SKILLS],
        'free_choices': 2,
        'source': REFERENCE_SOURCE,
        'authority': 'reference',
    },
    '私家侦探': {
        'factors': {'EDU': 2},
        'choice_factor': ['STR', 'DEX'],
        'choice_multiplier': 2,
        'credit_range': [9, 30],
        'required': [
            '技艺：摄影',
            '乔装',
            '法律',
            '图书馆使用',
            '心理学',
            '侦查',
        ],
        'choices': [SOCIAL_SKILLS],
        'free_choices': 1,
        'source': REFERENCE_SOURCE,
        'authority': 'reference',
    },
    '警察': {
        'factors': {'EDU': 2},
        'choice_factor': ['STR', 'DEX'],
        'choice_multiplier': 2,
        'credit_range': [9, 30],
        'required': ['格斗：斗殴', '射击', '急救', '法律', '心理学', '侦查'],
        'choices': [SOCIAL_SKILLS, ['汽车驾驶', '骑术']],
        'free_choices': 0,
        'source': REFERENCE_SOURCE,
        'authority': 'reference',
    },
    '作家': {
        'factors': {'EDU': 4},
        'credit_range': [9, 30],
        'required': [
            '技艺：文学',
            '历史',
            '图书馆使用',
            '外语',
            '母语',
            '心理学',
        ],
        'choices': [['博物学', '神秘学']],
        'free_choices': 1,
        'source': REFERENCE_SOURCE,
        'authority': 'reference',
    },
    '士兵': {
        'factors': {'EDU': 2},
        'choice_factor': ['STR', 'DEX'],
        'choice_multiplier': 2,
        'credit_range': [9, 30],
        'required': [
            '攀爬',
            '闪避',
            '格斗',
            '射击',
            '潜行',
            '急救',
            '生存',
            '外语',
        ],
        'choices': [],
        'free_choices': 0,
        'source': REFERENCE_SOURCE,
        'authority': 'reference',
    },
}


def _generator(rng: Any = None) -> Any:
    return rng if rng is not None else random.SystemRandom()


def _integer(value: Any, name: str, minimum: int = 0) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
    ):
        raise ValueError(f'{name}必须为不小于{minimum}的整数')
    return value


def roll_dice(expression: str, *, rng: Any = None, reason: str = '') -> Roll:
    text = str(expression).strip().replace(' ', '')
    if (
        not text
        or len(text) > 160
        or not re.fullmatch(r'[0-9dD+*()\-]+', text)
    ):
        raise ValueError('骰式仅支持骰子、整数、加减乘和括号')
    generator = _generator(rng)
    dice = []
    terms = []

    def replace(match: re.Match) -> str:
        count = int(match.group(1) or '1')
        sides = int(match.group(2))
        if not 1 <= count <= 100 or not 2 <= sides <= 10000:
            raise ValueError('骰子数量须为1–100，面数须为2–10000')
        if len(dice) + count > 100:
            raise ValueError('一次最多投100枚骰子')
        values = [generator.randint(1, sides) for _ in range(count)]
        dice.extend(values)
        terms.append({'count': count, 'sides': sides, 'values': values})
        return str(sum(values))

    numeric = re.sub(r'(\d*)[dD](\d+)', replace, text)
    try:
        tree = ast.parse(numeric, mode='eval')
    except (SyntaxError, ValueError) as exc:
        raise ValueError('骰式语法无效') from exc
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ValueError('骰式过于复杂')

    def calculate(node: ast.AST) -> int:
        if isinstance(node, ast.Constant) and type(node.value) is int:
            result = node.value
        elif isinstance(node, ast.UnaryOp) and isinstance(
            node.op, (ast.UAdd, ast.USub)
        ):
            result = calculate(node.operand)
            if isinstance(node.op, ast.USub):
                result = -result
        elif isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult)
        ):
            left = calculate(node.left)
            right = calculate(node.right)
            if isinstance(node.op, ast.Add):
                result = left + right
            elif isinstance(node.op, ast.Sub):
                result = left - right
            else:
                result = left * right
        else:
            raise ValueError('骰式包含不支持的运算')
        if abs(result) > 1000000:
            raise ValueError('骰式结果超出允许范围')
        return result

    return Roll(
        expression=text,
        dice=dice,
        total=calculate(tree.body),
        reason=reason,
        details={'terms': terms},
    )


def roll_percentile(
    bonus_dice: int = 0, *, rng: Any = None, reason: str = ''
) -> Roll:
    if type(bonus_dice) is not int or not -2 <= bonus_dice <= 2:
        raise ValueError('净奖励骰数量须在-2至2之间')
    generator = _generator(rng)
    unit = generator.randint(0, 9)
    tens = [generator.randint(0, 9) for _ in range(abs(bonus_dice) + 1)]
    candidates = [ten * 10 + unit or 100 for ten in tens]
    total = max(candidates) if bonus_dice < 0 else min(candidates)
    return Roll(
        expression='1D100',
        dice=[unit, *tens],
        total=total,
        reason=reason,
        details={
            'unit': unit,
            'tens': tens,
            'candidates': candidates,
            'bonus_dice': bonus_dice,
        },
    )


def success_level(target: int, result: int) -> str:
    _integer(target, '检定值')
    _integer(result, '骰点', 1)
    if result > 100:
        raise ValueError('百分骰结果不能超过100')
    if result == 1:
        return 'critical'
    if result == 100 or (target < 50 and result >= 96):
        return 'fumble'
    if result <= target // 5:
        return 'extreme'
    if result <= target // 2:
        return 'hard'
    return 'regular' if result <= target else 'failure'


def normalize_skill(skill: str) -> str:
    key = str(skill).strip().replace('：', ':').replace(':', '：')
    return SKILL_ALIASES.get(key.lower(), key)


def skill_value(character: Character, skill: str) -> int:
    key = normalize_skill(skill)
    if key.upper() in character.attributes:
        return character.attributes[key.upper()]
    if key.lower() in ('luck', '幸运', '运气'):
        return character.luck
    if key.lower() in ('san', '理智', 'sanity'):
        return character.current_san
    if key in character.skills:
        return character.skills[key]
    for saved_name, value in character.skills.items():
        if normalize_skill(saved_name) == key:
            return value
    if key in SKILL_BASES or key in ('闪避', '母语'):
        return base_skill(key, character.attributes)
    if key.startswith('科学：') and key[3:] in SCIENCE_SPECIALIZATIONS:
        return 1
    raise ValueError(f'角色没有技能：{key}')


def skill_check(
    character: Character,
    skill: str,
    difficulty: str = 'regular',
    bonus_dice: int = 0,
    *,
    is_pushed: bool = False,
    mode: str = 'exploration',
    rng: Any = None,
    reason: str = '',
) -> Roll:
    if difficulty not in DIFFICULTIES:
        raise ValueError('检定难度无效')
    key = normalize_skill(skill)
    if is_pushed and (
        mode == 'combat'
        or key.lower() in ('san', '理智', 'sanity', 'luck', '幸运', '运气')
        or key.startswith(('格斗：', '射击：'))
        or key == '闪避'
    ):
        raise ValueError('战斗、理智和幸运检定不能孤注一掷')
    target = skill_value(character, key)
    roll = roll_percentile(bonus_dice, rng=rng, reason=reason)
    level = success_level(target, roll.total)
    divisor = {'regular': 1, 'hard': 2, 'extreme': 5}[difficulty]
    roll.details.update(
        {
            'skill': key,
            'target': target,
            'difficulty': difficulty,
            'threshold': target // divisor,
            'level': level,
            'is_success': SUCCESS_LEVELS[level] >= DIFFICULTIES[difficulty],
            'is_pushed': is_pushed,
        }
    )
    return roll


def opposed_result(
    attacker: Roll, defender: Roll, *, defense: str = 'opposed'
) -> str:
    if defense not in ('opposed', 'fight_back', 'dodge'):
        raise ValueError('对抗方式无效')
    first = SUCCESS_LEVELS[attacker.details['level']]
    second = SUCCESS_LEVELS[defender.details['level']]
    if first < 2 and second < 2:
        return 'neither'
    if first != second:
        return 'attacker' if first > second else 'defender'
    if defense == 'fight_back':
        return 'attacker'
    if defense == 'dodge':
        return 'defender'
    first_value = attacker.details['target']
    second_value = defender.details['target']
    if first_value == second_value:
        return 'reroll'
    return 'attacker' if first_value > second_value else 'defender'


def maneuver_penalty(attacker: Character, defender: Character) -> int:
    difference = defender.build - attacker.build
    if difference >= 3:
        raise ValueError('体格相差至少3，无法进行搏击战技')
    return -max(0, difference)


def weapon_damage(
    expression: str,
    damage_bonus: str = '0',
    *,
    is_extreme: bool = False,
    is_impaling: bool = False,
    is_fight_back: bool = False,
    rng: Any = None,
) -> Roll:
    class MaximumDice:
        def randint(self, minimum: int, maximum: int) -> int:
            return maximum

    expression = re.sub(r'(?i)(?:\+\s*)?DB', '', expression).strip() or '0'
    if not is_extreme or is_fight_back:
        result = roll_dice(f'({expression})+({damage_bonus})', rng=rng)
        result.total = max(0, result.total)
        return result
    maximum_weapon = roll_dice(expression, rng=MaximumDice())
    if is_impaling:
        maximum_bonus = roll_dice(damage_bonus, rng=MaximumDice())
        fixed = maximum_weapon.total + maximum_bonus.total
        extra = roll_dice(f'({expression})+({fixed})', rng=rng)
        extra.total = max(0, extra.total)
        extra.details.update(
            {
                'is_impaling': True,
                'maximum_weapon': maximum_weapon.total,
                'maximum_damage_bonus': maximum_bonus.total,
            }
        )
        return extra
    maximum = roll_dice(f'({expression})+({damage_bonus})', rng=MaximumDice())
    maximum.details['is_maximized'] = True
    maximum.total = max(0, maximum.total)
    return maximum


def movement_rate(attributes: dict[str, int], age: int) -> int:
    strength = attributes['STR']
    dexterity = attributes['DEX']
    size = attributes['SIZ']
    if strength < size and dexterity < size:
        base = 7
    elif strength > size and dexterity > size:
        base = 9
    else:
        base = 8
    reduction = max(0, age // 10 - 3)
    return max(1, base - reduction)


def damage_bonus_build(attributes: dict[str, int]) -> tuple[str, int]:
    total = attributes['STR'] + attributes['SIZ']
    for upper, bonus, build in (
        (64, '-2', -2),
        (84, '-1', -1),
        (124, '0', 0),
        (164, '1D4', 1),
        (204, '1D6', 2),
    ):
        if total <= upper:
            return bonus, build
    build = 3 + (total - 205) // 80
    return f'{build - 1}D6', build


def derived_character(character: Character) -> Character:
    updated = character.model_copy(deep=True)
    attributes = updated.attributes
    updated.max_hp = max(1, (attributes['CON'] + attributes['SIZ']) // 10)
    updated.max_mp = attributes['POW'] // 5
    updated.max_san = max(0, 99 - updated.skills.get('克苏鲁神话', 0))
    updated.movement = movement_rate(attributes, updated.age)
    updated.damage_bonus, updated.build = damage_bonus_build(attributes)
    updated.current_hp = max(0, min(updated.current_hp, updated.max_hp))
    updated.current_mp = max(0, min(updated.current_mp, updated.max_mp))
    updated.current_san = max(0, min(updated.current_san, updated.max_san))
    if updated.current_hp == 0:
        _condition(updated, 'unconscious', True)
        if 'major_wound' in updated.conditions and not any(
            state in updated.conditions for state in ('stabilized', 'dead')
        ):
            _condition(updated, 'dying', True)
    if updated.current_san == 0:
        _condition(updated, 'permanent_insanity', True)
    return updated


def generate_attributes(*, rng: Any = None) -> tuple[dict, list[Roll]]:
    attributes = {}
    rolls = []
    for name in ATTRIBUTE_NAMES:
        expression = '(2D6+6)*5' if name in ('SIZ', 'INT', 'EDU') else '3D6*5'
        roll = roll_dice(expression, rng=rng, reason=f'创建{name}')
        attributes[name] = roll.total
        rolls.append(roll)
    return attributes, rolls


def age_modifiers(age: int) -> dict[str, Any]:
    if not 15 <= age <= 89:
        raise ValueError('标准自动建卡支持15至89岁；其他年龄须主持裁定')
    if age < 20:
        return {
            'physical': 5,
            'allowed': ['STR', 'SIZ'],
            'app': 0,
            'edu': -5,
            'edu_checks': 0,
            'luck_rolls': 2,
        }
    decade = age // 10
    amounts = {4: 5, 5: 10, 6: 20, 7: 40, 8: 80}
    return {
        'physical': amounts.get(decade, 0),
        'allowed': ['STR', 'CON', 'DEX'],
        'app': max(0, decade - 3) * 5,
        'edu': 0,
        'edu_checks': min(4, max(1, decade - 2)),
        'luck_rolls': 1,
    }


def apply_age(
    attributes: dict[str, int],
    age: int,
    reductions: dict[str, int],
    *,
    rng: Any = None,
) -> tuple[dict[str, int], list[Roll]]:
    modifiers = age_modifiers(age)
    result = dict(attributes)
    if set(reductions) - set(modifiers['allowed']):
        raise ValueError('年龄扣点包含不可扣减属性')
    values = [_integer(value, '年龄扣点') for value in reductions.values()]
    if sum(values) != modifiers['physical']:
        raise ValueError('年龄身体扣点总量不符合标准规则')
    for name, value in reductions.items():
        result[name] -= value
    result['APP'] -= modifiers['app']
    result['EDU'] += modifiers['edu']
    if any(value < 1 for value in result.values()):
        raise ValueError('年龄调整后属性必须大于0')
    rolls = []
    for _ in range(modifiers['edu_checks']):
        check = roll_dice('1D100', rng=rng, reason='年龄EDU改善检定')
        rolls.append(check)
        if check.total > result['EDU']:
            gain = roll_dice('1D10', rng=rng, reason='年龄EDU改善')
            result['EDU'] = min(99, result['EDU'] + gain.total)
            rolls.append(gain)
    return result, rolls


def base_skill(skill: str, attributes: dict[str, int]) -> int:
    key = normalize_skill(skill)
    if key == '闪避':
        return attributes['DEX'] // 2
    if key == '母语' or key.startswith('母语：'):
        return attributes['EDU']
    if key in SKILL_BASES:
        return SKILL_BASES[key]
    if key.startswith('技艺：'):
        return 5
    if key.startswith('生存：'):
        return 10
    if key.startswith(('外语：', '科学：', '驾驶：')):
        return 1
    raise ValueError(f'技能必须使用已知名称或明确专业：{key}')


def occupation_budget(definition: dict, attributes: dict[str, int]) -> int:
    factors = definition.get('factors')
    if not isinstance(factors, dict) or not factors:
        raise ValueError('职业必须明确点数公式，不能自动猜测')
    total = 0
    for name, factor in factors.items():
        if (
            name not in ATTRIBUTE_NAMES
            or type(factor) is not int
            or factor not in (1, 2, 3, 4)
        ):
            raise ValueError('职业点数公式无效')
        total += attributes[name] * factor
    choices = definition.get('choice_factor', [])
    if choices:
        if any(name not in ATTRIBUTE_NAMES for name in choices):
            raise ValueError('职业可选属性无效')
        multiplier = definition.get('choice_multiplier', 2)
        _integer(multiplier, '职业属性倍率', 1)
        total += max(attributes[name] for name in choices) * multiplier
    if total > 1000:
        raise ValueError('职业点数超过自动创建允许范围')
    return total


def validate_allocations(
    attributes: dict[str, int],
    definition: dict,
    selected_skills: list[str],
    occupational: dict[str, int],
    interests: dict[str, int],
) -> dict[str, int]:
    selected = [normalize_skill(skill) for skill in selected_skills]
    if len(selected) != 8 or len(set(selected)) != 8:
        raise ValueError('职业须选择八项不同技能')
    remaining = list(selected)

    def match(name: str) -> bool:
        for skill in remaining:
            if skill == name or skill.startswith(name + '：'):
                remaining.remove(skill)
                return True
        return False

    for required in definition.get('required', []):
        if not match(normalize_skill(required)):
            raise ValueError(f'缺少职业必选技能：{required}')
    for choices in definition.get('choices', []):
        if not any(match(normalize_skill(choice)) for choice in choices):
            raise ValueError('职业可选技能选择不完整')
    if len(remaining) != definition.get('free_choices', 0):
        raise ValueError('职业自由技能数量不符合定义')
    occupational = {normalize_skill(k): v for k, v in occupational.items()}
    interests = {normalize_skill(k): v for k, v in interests.items()}
    if set(occupational) - (set(selected) | {'信用评级'}):
        raise ValueError('职业点只能投入所选职业技能和信用评级')
    for pool in (occupational, interests):
        for name, points in pool.items():
            _integer(points, '技能分配点数')
            if name == '克苏鲁神话':
                raise ValueError('标准新卡不能投入克苏鲁神话点数')
            base_skill(name, attributes)
    if sum(occupational.values()) != occupation_budget(definition, attributes):
        raise ValueError('职业点数须准确用完')
    if sum(interests.values()) != attributes['INT'] * 2:
        raise ValueError('兴趣点数须准确用完')
    skills = dict(SKILL_BASES)
    skills['闪避'] = attributes['DEX'] // 2
    skills['母语'] = attributes['EDU']
    for name in set(selected) | set(occupational) | set(interests):
        skills[name] = (
            base_skill(name, attributes)
            + occupational.get(name, 0)
            + interests.get(name, 0)
        )
        if skills[name] > 99:
            raise ValueError('标准自动新卡技能超过99，须重新分配')
    credit_range = definition.get('credit_range')
    if not credit_range or len(credit_range) != 2:
        raise ValueError('职业必须明确信用评级范围')
    if not credit_range[0] <= skills['信用评级'] <= credit_range[1]:
        raise ValueError('信用评级不在所选职业范围')
    return skills


def _runtime(character: Character) -> dict:
    return character.allocations.setdefault('runtime', {})


def _condition(character: Character, name: str, is_present: bool) -> None:
    if is_present and name not in character.conditions:
        character.conditions.append(name)
    if not is_present and name in character.conditions:
        character.conditions.remove(name)


def _amount(parameters: dict, *, rng: Any, rolls: list[Roll]) -> int:
    amount = parameters.get('amount', parameters.get('expression', 0))
    if isinstance(amount, str):
        roll = roll_dice(amount, rng=rng)
        rolls.append(roll)
        amount = roll.total
    return _integer(amount, '变更数量')


def _damage(
    character: Character, amount: int, rng: Any, rolls: list[Roll]
) -> dict:
    character.current_hp = max(0, character.current_hp - amount)
    if amount:
        wounds = _runtime(character).setdefault('wounds', [])
        wounds.append({'id': new_id(), 'damage': amount, 'attempts': []})
    if amount >= character.max_hp:
        _condition(character, 'dead', True)
        _condition(character, 'dying', False)
        _condition(character, 'stabilized', False)
    elif amount * 2 >= character.max_hp:
        _condition(character, 'major_wound', True)
        _runtime(character)['healing_days'] = 0
        check = skill_check(character, 'CON', rng=rng)
        rolls.append(check)
        if not check.details['is_success']:
            _condition(character, 'unconscious', True)
    if character.current_hp == 0:
        _condition(character, 'unconscious', True)
        if 'major_wound' in character.conditions and 'dead' not in (
            character.conditions
        ):
            _condition(character, 'dying', True)
    return {'damage': amount}


def _sanity(
    character: Character,
    parameters: dict,
    day: int,
    rng: Any,
    rolls: list[Roll],
    sanity_roll: Roll | None = None,
) -> dict:
    runtime = _runtime(character)
    if runtime.get('san_day') != day:
        runtime.update(
            {
                'san_day': day,
                'san_day_start': character.current_san,
                'san_day_loss': 0,
            }
        )
    if sanity_roll is None:
        check = skill_check(character, 'SAN', rng=rng)
        rolls.append(check)
    else:
        if (
            str(sanity_roll.details.get('skill', '')).lower()
            not in ('san', '理智', 'sanity')
            or not 1 <= sanity_roll.total <= 100
        ):
            raise ValueError('复用理智检定须由服务器提供有效SAN骰')
        check = sanity_roll
    key = 'success_loss' if check.details['is_success'] else 'failure_loss'

    class MaximumDice:
        def randint(self, minimum, maximum):
            return maximum

    maximized = check.details['level'] == 'fumble' and key == 'failure_loss'
    loss_roll = roll_dice(
        str(parameters.get(key, '0')),
        rng=MaximumDice() if maximized else rng,
    )
    if loss_roll.total < 0:
        raise ValueError('理智损失不能为负数')
    if maximized:
        loss_roll.details['is_maximized'] = True
    rolls.append(loss_roll)
    loss = min(character.current_san, loss_roll.total)
    character.current_san -= loss
    runtime['san_day_loss'] += loss
    result = {
        'san_loss': loss,
        'is_involuntary': loss > 0,
        'sanity_check_id': check.id,
        'sanity_check_reused': sanity_roll is not None,
    }
    if loss >= 5:
        intelligence = skill_check(character, 'INT', rng=rng)
        rolls.append(intelligence)
        if intelligence.details['is_success']:
            duration = roll_dice('1D10', rng=rng)
            rolls.append(duration)
            _condition(character, 'temporary_insanity', True)
            runtime['temporary_insanity_hours'] = duration.total
            result['temporary_insanity_hours'] = duration.total
    if runtime['san_day_loss'] >= max(
        1, math.ceil(runtime['san_day_start'] / 5)
    ):
        _condition(character, 'indefinite_insanity', True)
    if character.current_san == 0:
        _condition(character, 'permanent_insanity', True)
    return result


def _heal(
    character: Character,
    parameters: dict,
    rng: Any,
    rolls: list[Roll],
    healer: Character | None = None,
) -> dict:
    source = parameters.get('source', 'first_aid')
    if source not in ('first_aid', 'medicine'):
        raise ValueError('治疗来源须为first_aid或medicine')
    clinician = healer if healer is not None else character
    if any(
        state in clinician.conditions
        for state in ('dead', 'unconscious', 'dying', 'permanent_insanity')
    ):
        raise ValueError('治疗者当前无法行动')
    attribution = {
        'healer_id': clinician.id,
        'patient_id': character.id,
        'source': source,
    }
    wounds = _runtime(character).setdefault('wounds', [])
    if not wounds:
        wounds.append({'id': 'imported', 'damage': 0, 'attempts': []})
    wound_id = parameters.get('wound_id', wounds[-1]['id'])
    wound = next((item for item in wounds if item['id'] == wound_id), None)
    if wound is None:
        raise ValueError('找不到对应伤口')
    if source in wound['attempts']:
        raise ValueError('同一伤口不能重复进行相同治疗')
    wound['attempts'].append(source)
    check = skill_check(
        clinician, '急救' if source == 'first_aid' else '医学', rng=rng
    )
    check.details.update(attribution)
    rolls.append(check)
    if not check.details['is_success']:
        return {'healed': 0, **attribution}
    if 'dying' in character.conditions or 'stabilized' in character.conditions:
        _condition(character, 'dying', False)
        _condition(character, 'stabilized', True)
        if source == 'medicine':
            _condition(character, 'medical_treatment', True)
        return {'healed': 0, 'is_stabilized': True, **attribution}
    amount = 1
    if source == 'medicine':
        healing = roll_dice('1D3', rng=rng)
        healing.details.update(attribution)
        rolls.append(healing)
        amount = healing.total
    before = character.current_hp
    character.current_hp = min(character.max_hp, before + amount)
    if character.current_hp > 0:
        _condition(character, 'unconscious', False)
    return {'healed': character.current_hp - before, **attribution}


def _recover(
    character: Character, parameters: dict, rng: Any, rolls: list[Roll]
) -> dict:
    days = _integer(parameters.get('days', 0), '恢复天数')
    hours = _integer(parameters.get('hours', days * 24), '恢复小时')
    if days > 365 or hours > 8760:
        raise ValueError('单次恢复时间不能超过一年')
    before = character.current_hp
    character.current_mp = min(character.max_mp, character.current_mp + hours)
    runtime = _runtime(character)
    if 'temporary_insanity_hours' in runtime:
        remaining = runtime['temporary_insanity_hours'] - hours
        if remaining <= 0:
            _condition(character, 'temporary_insanity', False)
            runtime.pop('temporary_insanity_hours', None)
        else:
            runtime['temporary_insanity_hours'] = remaining
    if 'dying' in character.conditions or (
        'stabilized' in character.conditions
        and 'medical_treatment' not in character.conditions
    ):
        return {'healed': 0, 'days': days, 'hours': hours}
    if 'major_wound' not in character.conditions:
        character.current_hp = min(character.max_hp, before + days)
    else:
        accumulated = runtime.get('healing_days', 0) + days
        weeks, runtime['healing_days'] = divmod(accumulated, 7)
        for week in range(weeks):
            check = skill_check(character, 'CON', rng=rng)
            rolls.append(check)
            if not check.details['is_success']:
                continue
            is_extreme = SUCCESS_LEVELS[check.details['level']] >= 4
            healing = roll_dice('2D3' if is_extreme else '1D3', rng=rng)
            rolls.append(healing)
            character.current_hp = min(
                character.max_hp, character.current_hp + healing.total
            )
            if is_extreme or character.current_hp * 2 >= character.max_hp:
                _condition(character, 'major_wound', False)
                extra_days = (weeks - week - 1) * 7 + runtime['healing_days']
                character.current_hp = min(
                    character.max_hp, character.current_hp + extra_days
                )
                runtime['healing_days'] = 0
                break
    if character.current_hp > 0:
        _condition(character, 'unconscious', False)
        _condition(character, 'stabilized', False)
    return {
        'healed': character.current_hp - before,
        'days': days,
        'hours': hours,
    }


def apply_command(
    character: Character,
    kind: str,
    parameters: dict,
    *,
    day: int = 0,
    rng: Any = None,
    healer: Character | None = None,
    sanity_roll: Roll | None = None,
) -> tuple[Character, list[Roll], dict]:
    updated = character.model_copy(deep=True)
    rolls = []
    info = {'kind': kind}
    if 'dead' in updated.conditions and kind not in (
        'add_item',
        'remove_item',
    ):
        raise ValueError('死亡角色不能继续进行规则操作')
    if kind == 'damage':
        amount = _amount(parameters, rng=rng, rolls=rolls)
        armor = _integer(parameters.get('armor', 0), '护甲')
        info.update(_damage(updated, max(0, amount - armor), rng, rolls))
    elif kind in ('sanity', 'san'):
        info.update(_sanity(updated, parameters, day, rng, rolls, sanity_roll))
    elif kind == 'gain_sanity':
        if (
            'permanent_insanity' in updated.conditions
            or updated.current_san == 0
        ):
            raise ValueError('永久疯狂不能通过普通理智奖励恢复')
        runtime = _runtime(updated)
        if runtime.get('san_day') != day:
            runtime.update(
                {
                    'san_day': day,
                    'san_day_start': updated.current_san,
                    'san_day_loss': 0,
                }
            )
        amount = _amount(parameters, rng=rng, rolls=rolls)
        maximum = max(0, 99 - updated.skills.get('克苏鲁神话', 0))
        gained = min(amount, max(0, maximum - updated.current_san))
        updated.current_san += gained
        info.update({'san_gain': gained, 'requested_gain': amount})
    elif kind == 'heal':
        info.update(_heal(updated, parameters, rng, rolls, healer))
    elif kind == 'recover':
        info.update(_recover(updated, parameters, rng, rolls))
    elif kind == 'dying_check':
        if 'dying' not in updated.conditions:
            raise ValueError('只有濒死角色需要濒死检定')
        check = skill_check(updated, 'CON', rng=rng)
        rolls.append(check)
        if not check.details['is_success']:
            _condition(updated, 'dead', True)
    elif kind == 'spend_mp':
        amount = _amount(parameters, rng=rng, rolls=rolls)
        deficit = max(0, amount - updated.current_mp)
        updated.current_mp = max(0, updated.current_mp - amount)
        if deficit:
            info.update(_damage(updated, deficit, rng, rolls))
    elif kind == 'mark_skill':
        skill = normalize_skill(parameters.get('skill', ''))
        value = skill_value(updated, skill)
        if (
            skill.upper() not in ATTRIBUTE_NAMES
            and skill.lower()
            not in ('san', '理智', 'sanity', 'luck', '幸运', '运气')
            and skill not in ('克苏鲁神话', '信用评级')
            and skill not in updated.skill_marks
        ):
            updated.skills.setdefault(skill, value)
            updated.skill_marks.append(skill)
    elif kind in ('grow', 'growth'):
        requested = parameters.get('skills', list(updated.skill_marks))
        if isinstance(requested, str):
            requested = [requested]
        for raw_skill in requested:
            skill = normalize_skill(raw_skill)
            if skill not in updated.skill_marks:
                raise ValueError('只有已记录成长标记的技能能进行成长')
            check = roll_dice('1D100', rng=rng, reason=f'{skill}成长检定')
            rolls.append(check)
            if check.total > updated.skills[skill] or check.total >= 96:
                gain = roll_dice('1D10', rng=rng, reason=f'{skill}成长')
                updated.skills[skill] += gain.total
                rolls.append(gain)
            updated.skill_marks.remove(skill)
    elif kind == 'modify_skill':
        skill = normalize_skill(parameters.get('skill', ''))
        base_skill(skill, updated.attributes)
        amount = parameters.get('delta')
        if type(amount) is not int or abs(amount) > 1000:
            raise ValueError('技能变更须为有效整数增量')
        initial = base_skill(skill, updated.attributes)
        updated.skills[skill] = max(
            0, updated.skills.get(skill, initial) + amount
        )
    elif kind == 'modify_attribute':
        name = str(parameters.get('attribute', '')).upper()
        delta = parameters.get('delta')
        if name not in ATTRIBUTE_NAMES or type(delta) is not int:
            raise ValueError('属性变更必须提供有效属性和整数增量')
        if not 1 <= updated.attributes[name] + delta <= 1000:
            raise ValueError('属性变更超出允许范围')
        updated.attributes[name] += delta
    elif kind == 'condition':
        name = str(parameters.get('name', '')).strip()
        if not name or len(name) > 120:
            raise ValueError('状态名称无效')
        is_present = parameters.get('is_present', True)
        if type(is_present) is not bool:
            raise ValueError('状态是否生效须为布尔值')
        _condition(updated, name, is_present)
    elif kind == 'add_item':
        item = dict(parameters.get('item', {}))
        if not item.get('name'):
            raise ValueError('物品必须包含名称')
        item.setdefault('id', new_id())
        updated.inventory.append(item)
    elif kind == 'remove_item':
        item_id = parameters.get('item_id')
        matching = [
            item for item in updated.inventory if item.get('id') == item_id
        ]
        if not matching:
            raise ValueError('找不到要移除的物品')
        updated.inventory = [
            item for item in updated.inventory if item.get('id') != item_id
        ]
    else:
        raise ValueError(f'规则命令尚未自动化：{kind}')
    updated = derived_character(updated)
    info.update(
        {
            'current_hp': updated.current_hp,
            'current_san': updated.current_san,
            'current_mp': updated.current_mp,
            'conditions': list(updated.conditions),
        }
    )
    return updated, rolls, info
