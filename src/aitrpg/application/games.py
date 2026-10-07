from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import mimetypes
import os
import re
import secrets
from datetime import timedelta
from pathlib import Path

from PIL import Image
from PIL import ImageDraw

from aitrpg.adapters.process import is_process_alive
from aitrpg.adapters.providers import ProviderError
from aitrpg.adapters.storage import ConflictError
from aitrpg.domain.models import Actor
from aitrpg.domain.models import Character
from aitrpg.domain.models import Game
from aitrpg.domain.models import Invitation
from aitrpg.domain.models import KeeperControl
from aitrpg.domain.models import KeeperResponse
from aitrpg.domain.models import PlayerResponse
from aitrpg.domain.models import Provider
from aitrpg.domain.models import Roll
from aitrpg.domain.models import RulingRepair
from aitrpg.domain.models import Scenario
from aitrpg.domain.models import Seat
from aitrpg.domain.models import new_id
from aitrpg.domain.models import utc_now
from aitrpg.domain.privacy import retrieve_sources
from aitrpg.domain.privacy import scenario_for_viewer
from aitrpg.domain.rules import apply_command
from aitrpg.domain.rules import derived_character
from aitrpg.domain.rules import normalize_skill
from aitrpg.domain.rules import opposed_result
from aitrpg.domain.rules import skill_check
from aitrpg.domain.rules import skill_value
from aitrpg.domain.scheduler import record_selection
from aitrpg.domain.scheduler import select_actors
from aitrpg.domain.scheduler import select_group

PLAYER_INSTRUCTION = (
    '你是CoC7调查员，只根据本人角色、可见记录和现场行动。'
    'self_character_id与self_character_name明确指定本人；'
    '不要把别人的姓名、职业或台词当成自己。'
    '认真扮演背景、动机与关系。不要假装知道未提供的剧本秘密。'
    '当前共同目标以实际开场与已获信息为准，背景旧案不会自动变成本团主线。'
    '台词写speech，具体行动写intent；有必要可观察或让出机会。'
    '参照最新记录，已完成的提问或动作不要原样再提交。'
    '不得自行投骰、宣称检定结果或修改状态。'
    '意图默认只给主持，公开行动可设intent_visibility=public。'
    '防御邀请时选择defense=dodge/fight_back/cover。'
)
KEEPER_INSTRUCTION = (
    '你是CoC7主持人。保留模组核心真相，允许合理即兴并记录improvisation。'
    '即兴仅补足对白、环境和衔接，不能添加新的主要秘密、雇主任务或'
    '核心对手去替代原作主线。原作没有关键线索的准备地点应适时离开。'
    '公平安排不同数量和组合的调查员，避免机械车轮。'
    '玩家台词和行动按actor_id映射到对应角色，不能代演未受邀者的重大行动。'
    'is_all_players仅用于必须全员参与的检定或决定，'
    '普通介绍、多人在场和分头探索不要设为true。'
    '检查只填写checks，平台会给真实结果；未知结果前不要叙述成功或失败。'
    '普通状态用commands请求，所有骰点和计算由平台执行。'
    '公开narration不能包含主持专用资料、非现场角色的秘密或私密判定。'
    '各人的HO和秘密导入放private_messages，以actor_id为键分别发送；'
    '不能把多个HO放在同一narration中向现场公开。'
    '命令参数：damage(amount,armor=0)、sanity(success_loss,failure_loss)、'
    'heal(source=first_aid或medicine,wound_id可选)、'
    '治疗别人时附healer_id，使用治疗者真实技能；heal只操作调查员卡。'
    'NPC治疗申请医生check医学，再用flags记录主持裁定的NPC后果。'
    'recover(hours=0,days=0)、grow(skills列表)、'
    'gain_sanity(amount整数或安全骰式，结局或明确规则奖励)、'
    'spend_mp(amount)、condition(name,is_present布尔)、'
    'add_item(item对象)、remove_item(item_id)、mark_skill(skill)、'
    'background(updates对象)、custom(changes对象，须有裁定依据)。'
    '場景切换使用scene_id；split_groups的groups为组名到actor_id列表。'
    '各队处于不同场景时，split_groups另填group_scenes的组名到scene_id。'
    'start_combat的npcs条目必须有id,name,dex,hp,fighting,dodge,damage,armor；'
    '并提供source_id或明确improvisation，readied_guns列出已准备枪械角色卡id。'
    'combat_attack参数attacker_id,target_id,weapon_index,mode=melee或firearm；'
    '玩家伤害技能来自真实角色卡，不传weapon_damage或替玩家选defense。'
    '战斗行动顺序由平台规定，NPC不能凭空改变调查员行动次数。'
    '只结算combat_turn当前行动；被拒的攻击没有排队或产生效果。'
    '本轮实际攻击必须提交新的combat_attack，不能仅靠flags或台词声称已打。'
    'NPC攻防走combat_attack，不放进仅支持调查员的opponent_character_id。'
    '结束用end_combat。结束模组时is_finished=true且说明ending。'
    '场景只选scene_catalog已列id，邀请使用actor_id，检定使用character_id。'
    '时间用advance_hours/advance_days推进，平台自动处理自然恢复；'
    'narration中发生跨夜、出发、到达或耗时调查，必须同步填写时间与scene_id；'
    '不能仅用台词声称已过夜或到新地点却把结构化状态留在原处。'
    '不重复发同一时段recover。'
    '必须根据真实调查进度推进，不需要逐字复演书面时长。'
    '成功检定后兑现模组规定的信息，不重复考验已完成条件。'
    '不能临时添加永远无法通过的行政或权限障碍阻塞原有线索。'
    '场景目标满足后收束对话，提供明确下一步并推进实际时间/场景。'
    '同一问题已有答复时提示既有答案，避免让角色反复核验相同细节。'
    '观察、警戒、等候应在合理耗时后产生新情况或明确无发现，'
    '不得逐节点重复同一静止画面且保持时钟不变。'
    'scene_id表示实际所在场景，准备去、指图或讨论路线不算到达。'
    '查看scheduled_events里的原有事件条件。玩家持续等待时，'
    '处理到下一实际变化或原定事件，不能只重复无变化的观察。'
    '不要因为略过白天或守夜而漏掉模组核心定时事件。'
    '事件确因玩家选择未发生时，记录条件和原因，保留核心真相。'
    'NPC及环境按原条件自然行动，玩家观察不等于世界静止。'
    '原文规定的NPC意外和环境后果不能因玩家被动而取消，'
    '满足条件时落实世界事件，再让玩家选择应对，不代选角色行动。'
)
CONTROL_INSTRUCTION = (
    '你仍是本局主持，核对已完成叙事与游戏控制状态。'
    '不新增情节或角色行动，不重新检定。未来约定不算已发生。'
    '输出绝对target_day/target_hour，由引擎算增量。'
    'day从0起算，第1天=0，第二天=1。current是唯一实际存档时刻。'
    'previous只是未执行且被拒绝的候选，不能当已发生状态。'
    'unchanged须与current比较；之前叙事漏记的已发生时间仍须同步。'
    '只能选目录中的scene_id，复合旅行场景可包含其原文地点。'
    '已有叙事仅把同一段旅行的交通方式作合理即兴调整时，'
    '仍可对应原旅行场景，在reason注明差异，不能对应终局撤离。'
    '核对剧情阶段、行进方向与场景前提，不能仅凭同名地点匹配。'
    '场景的核心遭遇实际未发生时，不能自行称作该场景入口来匹配；'
    '讨论图上的目的地仍是当前旅行阶段，不代表已到目的地。'
    '每项变更给连续逐字原句quote，不得拼接或用省略号代替原文。'
    '不能把原作时长当实际耗时。'
    '精确时间用supported；只有大约耗时且允许估计时，'
    '由守秘人裁定合理分钟，标estimated并解释。'
    '无变化用unchanged，资料不足且不能裁定用needs_review。'
)


class GameService:
    def __init__(self, platform):
        self.platform = platform
        self.store = platform.store
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._steps: dict[str, asyncio.Task] = {}
        self._signals: dict[str, asyncio.Event] = {}
        self._offline: dict[str, set[str]] = {}
        for record in self.store.list('game'):
            if record['status'] in {
                'running',
                'waiting',
            } and not is_process_alive(
                record.get('scheduler', {}).get('runner_pid', 0)
            ):
                record['status'] = 'paused'
                record['last_error'] = '服务重新启动，已保留进度，请继续游戏'
                self.store.put('game', record['id'], record)

    def list(self) -> list[Game]:
        return [Game.model_validate(item) for item in self.store.list('game')]

    def get(self, identifier: str) -> Game:
        record = self.store.get('game', identifier)
        if not record:
            raise ValueError('游戏不存在')
        return Game.model_validate(record)

    def scenario(self, game: Game) -> Scenario:
        record = self.store.get(
            'scenario_version', f'{game.scenario_id}@{game.scenario_version}'
        )
        if not record:
            raise ValueError('游戏的固定模组版本不存在')
        return Scenario.model_validate(record)

    def create(
        self,
        name,
        scenario_id,
        keeper_actor_id,
        seats,
        budget_nodes=1000,
        budget_tokens=10000000,
    ) -> Game:
        scenario = self.platform.scenarios.get(scenario_id)
        if scenario.status != 'approved' or not scenario.scenes:
            raise ValueError('请先审核并启用有场景的模组')
        if not self.store.get('actor', keeper_actor_id):
            raise ValueError('主持身份不存在')
        seats = [Seat.model_validate(seat) for seat in seats]
        if not scenario.min_players <= len(seats) <= scenario.max_players:
            raise ValueError('玩家人数不符合模组要求')
        if len({seat.actor_id for seat in seats}) != len(seats):
            raise ValueError('每个玩家席位必须使用独立 AI 身份')
        if keeper_actor_id in {seat.actor_id for seat in seats}:
            raise ValueError('主持和玩家不能使用同一身份')
        if len({seat.character_id for seat in seats}) != len(seats):
            raise ValueError('同一卡不能占据多个席位')
        role_ids = {role.id for role in scenario.roles}
        assigned = [seat.role_id for seat in seats if seat.role_id]
        if len(set(assigned)) != len(assigned):
            raise ValueError('秘密身份不能重复分配')
        if any(role not in role_ids for role in assigned):
            raise ValueError('所选秘密身份不存在')
        if scenario.roles and len(scenario.roles) == len(seats):
            if set(assigned) != role_ids:
                raise ValueError('该模组要求为每个席位分配独立秘密身份')
        game = Game(
            name=name,
            scenario_id=scenario.id,
            scenario_version=scenario.version,
            keeper_actor_id=keeper_actor_id,
            seats=seats,
            scene_id=scenario.scenes[0].id,
            budget_nodes=budget_nodes,
            budget_tokens=budget_tokens,
        )
        with self.store.transaction() as transaction:
            for seat in seats:
                record = transaction.get('character', seat.character_id)
                if not record or record['actor_id'] != seat.actor_id:
                    raise ValueError('角色卡不属于所选 AI 身份')
                character = Character.model_validate(record)
                if character.locked_game_id:
                    raise ValueError(f'{character.name} 已被其他游戏占用')
                character.locked_game_id = game.id
                runtime = character.allocations.setdefault('runtime', {})
                game.scheduler['base_day'] = max(
                    game.scheduler.get('base_day', 0),
                    runtime.get('lifetime_day', 0),
                )
                character.version += 1
                transaction.put(
                    'character',
                    character.id,
                    character.model_dump(mode='json'),
                )
            transaction.put('game', game.id, game.model_dump(mode='json'))
            transaction.append_event(game.id, 'status', {'text': '游戏已创建'})
        return game

    def _characters(self, game: Game) -> list[Character]:
        return [
            Character.model_validate(
                self.store.get('character', seat.character_id)
            )
            for seat in game.seats
        ]

    def _visible_events(self, game: Game, actor_id: str | None):
        events = self.store.events(game.id)
        if actor_id is None or actor_id == game.keeper_actor_id:
            return events
        visible = [
            event
            for event in events
            if not event['is_private']
            and (not event['actor_ids'] or actor_id in event['actor_ids'])
        ]
        for event in visible:
            data = event['data']
            if (
                event['kind'] in {'player', 'reaction'}
                and data.get('actor_id') != actor_id
                and data.get('intent_visibility', 'keeper') != 'public'
            ):
                data.pop('intent', None)
        return visible

    def view(self, identifier, actor_id=None) -> dict:
        game = self.get(identifier)
        scenario = self.scenario(game)
        is_keeper = actor_id is None or actor_id == game.keeper_actor_id
        seat = next(
            (seat for seat in game.seats if seat.actor_id == actor_id), None
        )
        if not is_keeper and seat is None:
            raise ValueError('该身份不在游戏中')
        role_id = seat.role_id if seat else None
        viewed_scene = game.scene_id
        if seat:
            viewed_scene = game.scheduler.get('group_scenes', {}).get(
                seat.group_id, game.scene_id
            )
        known = game.knowledge.get(actor_id, []) if actor_id else []
        visible_assets = (
            game.revealed_assets.get(actor_id, []) if actor_id else []
        )
        scenario_view = scenario_for_viewer(
            scenario,
            role_id=role_id,
            is_keeper=is_keeper,
            revealed_ids=known + visible_assets,
            scene_id=viewed_scene,
        )
        if not is_keeper:
            scenario_view.pop('description', None)
            for scene in scenario_view['scenes']:
                scene.pop('public_text', None)
        cards = []
        for character in self._characters(game):
            if is_keeper or character.actor_id == actor_id:
                cards.append(character.model_dump(mode='json'))
            else:
                cards.append(
                    {
                        'id': character.id,
                        'name': character.name,
                        'actor_id': character.actor_id,
                        'occupation': character.occupation,
                    }
                )
        assets = []
        for asset in scenario_view.get('assets', []):
            asset_id = asset['id']
            assets.append(
                {
                    'id': asset_id,
                    'name': asset['name'],
                    'is_map': asset.get('is_map', False),
                    'nodes': asset.get('nodes', []),
                    'positions': self._visible_positions(
                        game, asset_id, actor_id
                    ),
                    'url': f'/api/v1/games/{game.id}/assets/{asset_id}'
                    + (f'?actor_id={actor_id}' if actor_id else ''),
                }
            )
        invitations = [
            item
            for item in self.store.list(
                'invitation', game_id=game.id, statuses=['pending', 'claimed']
            )
            if item['game_id'] == game.id
            and item['status'] in {'pending', 'claimed'}
            and (is_keeper or item['actor_id'] == actor_id)
        ]
        public_game = game.model_dump(mode='json')
        if not is_keeper:
            public_game['scene_id'] = viewed_scene
            public_game.pop('last_keeper', None)
            public_game.pop('flags', None)
            public_game.pop('knowledge', None)
            public_game.pop('revealed_assets', None)
            public_game.pop('scheduler', None)
            if public_game.get('last_error'):
                public_game['last_error'] = '游戏已暂停，等待主持或管理员处理'
            public_game['seats'] = [
                {key: value for key, value in seat.items() if key != 'role_id'}
                for seat in public_game['seats']
            ]
            combat = game.combat
            public_game['combat'] = {
                'round': combat.get('round'),
                'turn': combat.get('turn'),
                'order': [
                    {
                        key: entry[key]
                        for key in ('id', 'name', 'actor_id', 'dex')
                        if key in entry
                    }
                    for entry in combat.get('order', [])
                ],
            }
        return {
            'game': public_game,
            'characters': cards,
            'events': self._visible_events(game, actor_id),
            'assets': assets,
            'invitations': [
                {key: value for key, value in item.items() if key != 'context'}
                for item in invitations
            ],
            'scenario': scenario_view,
        }

    def context(self, game: Game, actor_id: str, purpose: str, extra=None):
        context = self.view(game.id, actor_id)
        context['task'] = purpose
        own_seat = next(
            (seat for seat in game.seats if seat.actor_id == actor_id), None
        )
        context['self_actor_id'] = actor_id
        if own_seat:
            own = next(
                card
                for card in context['characters']
                if card['id'] == own_seat.character_id
            )
            context['self_character_id'] = own['id']
            context['self_character_name'] = own['name']
        for card in context['characters']:
            for key in (
                'creation_rolls',
                'allocations',
                'occupation_definition',
                'source',
                'import_warnings',
                'created_at',
                'locked_game_id',
            ):
                card.pop(key, None)
        context['actor_ids'] = [seat.actor_id for seat in game.seats]
        context['events'] = context['events'][-32:]
        history = self._visible_events(game, actor_id)
        recent_ids = {event['id'] for event in context['events']}
        older = [
            event
            for event in history
            if event['id'] not in recent_ids
            and event['kind'] in {'narration', 'check', 'ruling'}
        ]
        scene = next(
            (
                scene
                for scene in self.scenario(game).scenes
                if scene.id == game.scene_id
            ),
            None,
        )
        query = (
            (scene.title + ' ' + scene.public_text) if scene else game.scene_id
        )
        words = set(
            re.findall(r'[\u4e00-\u9fff]{2}|[A-Za-z]{3,}', query.lower())
        )
        ranked = sorted(
            older,
            key=lambda event: sum(
                str(event['data']).lower().count(word) for word in words
            ),
            reverse=True,
        )
        context['earlier_known_records'] = [
            {
                'sequence': event['sequence'],
                'kind': event['kind'],
                'content': str(event['data'])[:1200],
            }
            for event in ranked[:6]
        ]
        if actor_id == game.keeper_actor_id:
            holds = game.scheduler.get('scene_nodes', {})
            count = holds.get(game.scene_id, 0)
            context['pacing'] = {
                'nodes_in_current_scene': count,
                'scene_goal': scene.keeper_text[:4000] if scene else '',
                'directive': (
                    '本场景已持续多个节点，请明确未完成目标。'
                    '已取得的信息不要重复扣住；若原条件满足，'
                    '收束并开放下一场景，继续真实剧情与时间。'
                )
                if count >= 6
                else '围绕具体调查目标推进。',
            }
            scenario = self.scenario(game)
            context['scheduled_events'] = [
                {
                    'scene_id': item.id,
                    'title': item.title,
                    'conditions': item.conditions,
                    'goal': item.keeper_text[:1200],
                    'settled_nodes': holds.get(item.id, 0),
                }
                for item in scenario.scenes
                if any(
                    key in item.conditions
                    for key in ('time', 'trigger', 'requires', 'event')
                )
            ]
            context['next_scene_options'] = [
                {
                    'id': item.id,
                    'title': item.title,
                    'conditions': item.conditions,
                    'goal': item.keeper_text,
                    'read_aloud_candidate': item.public_text,
                }
                for item in scenario.scenes
                if scene and item.id in scene.next_scene_ids
            ]
        for asset in context['assets']:
            asset.pop('url', None)
        if actor_id == game.keeper_actor_id:
            scenario = self.scenario(game)
            query = ' '.join(
                [
                    game.scene_id,
                    str(game.day),
                    json.dumps(game.flags, ensure_ascii=False),
                ]
                + [str(event['data']) for event in context['events'][-5:]]
            )
            scene = next(
                (
                    scene
                    for scene in scenario.scenes
                    if scene.id == game.scene_id
                ),
                scenario.scenes[0],
            )
            context['source_evidence'] = retrieve_sources(
                scenario,
                query,
                source_ids=scene.source_ids,
                is_keeper=True,
                limit=8,
                max_chars=18000,
            )
            context['related_source_evidence'] = retrieve_sources(
                scenario, query, is_keeper=True, limit=6, max_chars=8000
            )
            context['ending_rules_evidence'] = retrieve_sources(
                scenario,
                '结局 结束 返航 ending finale',
                is_keeper=True,
                limit=12,
                max_chars=12000,
            )
            context['scenario'].pop('source_blocks', None)
            context['scenario'].pop('directory', None)
            context['scenario'].pop('source_index', None)
            catalog = [
                scene.model_dump(mode='json') for scene in scenario.scenes
            ]
            context['scene_catalog'] = [
                {
                    'id': item['id'],
                    'title': item['title'],
                    'next_scene_ids': item.get('next_scene_ids', []),
                    'participants': item.get('conditions', {}).get(
                        'participants', []
                    ),
                }
                for item in catalog
            ]
            context['scenario']['scenes'] = [
                item for item in catalog if item['id'] == game.scene_id
            ]
            context['scenario']['endings'] = [
                {
                    'id': ending.id,
                    'title': ending.title,
                    'text': ending.text[:2200],
                }
                for ending in scenario.endings
            ]
            for collection in ('npcs', 'clues', 'handouts'):
                items = context['scenario'].get(collection, [])
                context['scenario'][collection] = [
                    item
                    for item in items
                    if not item.get('scene_ids')
                    or game.scene_id in item['scene_ids']
                ][:30]
            for collection in ('scenes', 'npcs', 'clues', 'roles', 'endings'):
                for item in context['scenario'].get(collection, []):
                    for key in ('text', 'keeper_text', 'secret_text'):
                        if isinstance(item.get(key), str):
                            limit = 5000 if collection == 'scenes' else 2200
                            text = item[key]
                            item[key] = (
                                text
                                if len(text) <= limit
                                else text[: limit - 500]
                                + '\n[部分省略]\n'
                                + text[-500:]
                            )
        if extra:
            context.update(extra)
        return context

    def _model_context(self, game, actor_id, provider, context):
        context = dict(context)
        context.pop('_images', None)
        if provider.is_vision:
            scenario = self.scenario(game)
            visible_ids = {asset['id'] for asset in context.get('assets', [])}
            relevant = [
                asset
                for asset in scenario.assets
                if asset.id in visible_ids
                and asset.is_map
                and (not asset.scene_ids or game.scene_id in asset.scene_ids)
            ]
            if relevant:
                path = self.asset_path(game.id, relevant[0].id, actor_id)
                media_type = mimetypes.guess_type(path.name)[0] or 'image/png'
                context['_images'] = [
                    {
                        'url': f'data:{media_type};base64,'
                        + base64.b64encode(path.read_bytes()).decode('ascii'),
                        'caption': relevant[0].name,
                    }
                ]
        return context

    def _cycle(self, game: Game):
        cycle = self.store.get('cycle', game.id)
        if not cycle or cycle['revision'] != game.revision:
            cycle = {
                'revision': game.revision,
                'invitations': {},
                'selected': None,
            }
            self.store.put('cycle', game.id, cycle)
        return cycle

    def _visible_positions(self, game, asset_id, actor_id=None):
        positions = game.flags.get('map_positions', {}).get(asset_id, {})
        if actor_id is None or actor_id == game.keeper_actor_id:
            return positions
        own = next(seat for seat in game.seats if seat.actor_id == actor_id)
        visible = {
            seat.character_id
            for seat in game.seats
            if seat.group_id == own.group_id
        }
        return {
            key: value for key, value in positions.items() if key in visible
        }

    def _narration_audience(self, game, reply, *, is_opening=False):
        actors = reply.recipient_actor_ids or self._group_actor_ids(game)
        if not set(actors).issubset({seat.actor_id for seat in game.seats}):
            raise ValueError('秘密消息接收者不在游戏中')
        if is_opening and not set(actors).issubset(
            self._group_actor_ids(game)
        ):
            raise ValueError('开场受众不能超出原镜头授权成员')
        if not set(reply.private_messages).issubset(
            {seat.actor_id for seat in game.seats}
        ):
            raise ValueError('私密消息接收者不在游戏中')
        return actors

    def _account_usage(self, game_id, usage):
        if not usage:
            return
        with self.store.transaction() as transaction:
            latest = transaction.get('game', game_id)
            latest['token_usage'] += usage
            transaction.put('game', game_id, latest)

    def _validate_narration(self, game, reply, *, is_opening=False):
        scenario = self.scenario(game)
        public_audience = self._narration_audience(
            game, reply, is_opening=is_opening
        )
        audiences = [(reply.narration, public_audience)]
        audiences.extend(
            (message, [actor_id])
            for actor_id, message in reply.private_messages.items()
        )
        records = self.store.events(game.id)

        def fragments(text):
            cleaned = re.sub(r'[^\w\u4e00-\u9fff]', '', text).lower()
            return {
                cleaned[index : index + 16]
                for index in range(max(0, len(cleaned) - 15))
            }

        public = ''.join(role.public_text for role in scenario.roles)
        for narration, actor_ids in audiences:
            proposed = fragments(narration)
            for actor_id in actor_ids:
                seat = next(
                    seat for seat in game.seats if seat.actor_id == actor_id
                )
                known = public + ''.join(
                    event['data'].get('text', event['data'].get('speech', ''))
                    for event in records
                    if not event['is_private']
                    and (
                        not event['actor_ids']
                        or actor_id in event['actor_ids']
                    )
                )
                known_ids = set(game.knowledge.get(actor_id, []))
                if actor_id in public_audience:
                    known_ids.update(reply.reveal_clue_ids)
                known += ''.join(
                    clue.text
                    for clue in scenario.clues
                    if clue.id in known_ids
                )
                allowed = fragments(known)
                for role in scenario.roles:
                    if role.id == seat.role_id:
                        continue
                    if proposed & fragments(role.secret_text) - allowed:
                        raise ValueError(
                            '主持叙事包含未获授权的身份秘密：' + role.id
                        )
                for scene in scenario.scenes:
                    participants = scene.conditions.get('participants', [])
                    if not participants or (
                        seat.role_id in participants
                        or actor_id in participants
                    ):
                        continue
                    if proposed & fragments(scene.public_text) - allowed:
                        raise ValueError('主持叙事包含未授权的单人场景读白')

    async def _invoke(self, game, actor_id, purpose, output_type, extra=None):
        cycle = self._cycle(game)
        key = f'{purpose}:{actor_id}'
        if extra and extra.get('reaction_key'):
            key += ':' + str(extra['reaction_key'])
        invitation_id = cycle['invitations'].get(key)
        record = (
            self.store.get('invitation', invitation_id)
            if invitation_id
            else None
        )
        invitation = Invitation.model_validate(record) if record else None
        if invitation and invitation.response is not None:
            value = output_type.model_validate(invitation.response)
            return output_type.model_construct(
                _fields_set=set(invitation.response_fields)
                or value.model_fields_set,
                **{
                    name: getattr(value, name)
                    for name in output_type.model_fields
                },
            )
        if not invitation or invitation.expires_at <= utc_now():
            invitation_context = (
                dict(extra or {})
                if purpose == 'keeper_control'
                else self.context(game, actor_id, purpose, extra)
            )
            invitation_context['task'] = purpose
            invitation = Invitation(
                game_id=game.id,
                actor_id=actor_id,
                revision=game.revision,
                character_id=next(
                    (
                        seat.character_id
                        for seat in game.seats
                        if seat.actor_id == actor_id
                    ),
                    None,
                ),
                purpose=purpose,
                scene_id=game.scene_id,
                expires_at=utc_now()
                + timedelta(seconds=self.platform.settings.invitation_seconds),
                context=invitation_context,
            )
            cycle['invitations'][key] = invitation.id
            self.store.put('cycle', game.id, cycle)
            self.store.put(
                'invitation', invitation.id, invitation.model_dump(mode='json')
            )
        actor = Actor.model_validate(self.store.get('actor', actor_id))
        if actor.connection == 'mcp':
            signal = self._signals.setdefault(invitation.id, asyncio.Event())
            remaining = max(
                0, (invitation.expires_at - utc_now()).total_seconds()
            )
            try:
                await asyncio.wait_for(signal.wait(), timeout=remaining)
            except TimeoutError as error:
                invitation.status = 'expired'
                self.store.put(
                    'invitation',
                    invitation.id,
                    invitation.model_dump(mode='json'),
                )
                raise TimeoutError(f'{actor.name} 未在期限内回应') from error
            refreshed = self.store.get('invitation', invitation.id)
            return output_type.model_validate(refreshed['response'])
        provider = Provider.model_validate(
            self.store.get('provider', actor.provider_id)
        )
        if self.get(game.id).token_usage >= game.budget_tokens:
            raise ValueError('已达到本局 API token 预算，游戏已暂停')
        instruction = (
            KEEPER_INSTRUCTION
            if actor_id == game.keeper_actor_id
            else PLAYER_INSTRUCTION
        )
        if purpose == 'keeper_control':
            instruction = CONTROL_INSTRUCTION
        request_context = self._model_context(
            game, actor_id, provider, invitation.context
        )
        repair = ''
        for attempt in range(2):
            invitation.expires_at = utc_now() + timedelta(
                seconds=provider.timeout_seconds * 4 + 10
            )
            self.store.put(
                'invitation',
                invitation.id,
                invitation.model_dump(mode='json'),
            )
            try:
                result = await self.platform.provider_client.generate(
                    provider,
                    instruction + '\n表演风格：' + actor.style + repair,
                    request_context,
                    output_type,
                )
            except ProviderError as error:
                self._account_usage(
                    game.id, error.diagnostics.get('total_usage', 0)
                )
                self.store.put(
                    'provider_failure',
                    invitation.id,
                    {
                        'game_id': game.id,
                        'invitation_id': invitation.id,
                        'purpose': purpose,
                        'diagnostics': error.diagnostics,
                    },
                )
                raise
            self._account_usage(game.id, result.usage)
            if output_type is not KeeperResponse:
                break
            try:
                self._validate_narration(
                    game, result.value, is_opening=purpose == 'keeper_opening'
                )
                break
            except ValueError as error:
                if attempt:
                    raise
                repair = (
                    '\n上次公开输出未获授权：'
                    + str(error)
                    + '。仅向本人private_messages发送秘密，'
                    '公开叙事改为现场实际可知内容，不改变玩家行动或骰点。'
                )
        current = self.get(game.id)
        if current.revision != invitation.revision:
            raise ConflictError('生成期间游戏已被修正，旧回复已作废')
        self.submit(
            invitation.id,
            new_id(),
            result.value.model_dump(mode='json'),
            actor_id,
            source_fields=list(result.value.model_fields_set),
        )
        return result.value

    def submit(
        self,
        invitation_id,
        submission_id,
        response,
        actor_id,
        source_fields=None,
    ):
        digest = hashlib.sha256(
            json.dumps(response, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        with self.store.transaction() as transaction:
            record = transaction.get('invitation', invitation_id)
            if not record or record['actor_id'] != actor_id:
                raise ValueError('发言邀请不属于该身份')
            receipt = transaction.receipt(invitation_id)
            if receipt:
                if receipt['response_hash'] != digest:
                    raise ValueError('该邀请已经提交了不同的回复')
                return receipt['result']
            invitation = Invitation.model_validate(record)
            game = Game.model_validate(
                transaction.get('game', invitation.game_id)
            )
            if (
                invitation.revision != game.revision
                or invitation.status in {'cancelled', 'expired'}
                or invitation.expires_at <= utc_now()
                or game.status == 'ended'
            ):
                raise ValueError('发言邀请已经过期或失效')
            response_type = (
                (
                    {
                        'keeper_control': KeeperControl,
                        'keeper_repair': RulingRepair,
                    }.get(invitation.purpose, KeeperResponse)
                )
                if actor_id == game.keeper_actor_id
                else PlayerResponse
            )
            if invitation.purpose == 'keeper_repair':
                from aitrpg.application.ruling_repair import scoped_repair_type

                response_type = scoped_repair_type(
                    invitation.context.get('allowed_attacker_id'),
                    invitation.context.get('is_attack_allowed', True),
                )
            parsed = response_type.model_validate(response)
            invitation.response = parsed.model_dump(mode='json')
            invitation.status = 'submitted'
            invitation.submission_id = submission_id
            invitation.response_fields = source_fields or list(
                parsed.model_fields_set
            )
            transaction.put(
                'invitation', invitation.id, invitation.model_dump(mode='json')
            )
            result = {'accepted': True, 'invitation_id': invitation.id}
            transaction.save_receipt(
                invitation.id, submission_id, digest, result
            )
        self._signals.setdefault(invitation_id, asyncio.Event()).set()
        self._offline.setdefault(game.id, set()).discard(actor_id)
        return result

    async def run(self, identifier):
        if identifier in self._tasks and not self._tasks[identifier].done():
            return self.get(identifier)
        game = self.get(identifier)
        if game.status == 'ended':
            raise ValueError('游戏已结束')
        self._offline[identifier] = set()
        self._tasks[identifier] = asyncio.create_task(
            self._run_loop(identifier)
        )
        return game

    async def _run_loop(self, identifier):
        try:
            while self.get(identifier).status != 'ended':
                await self.step(identifier, is_automatic=True)
                if self.get(identifier).status in {'paused', 'error'}:
                    break
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            pass

    async def pause(self, identifier):
        task = self._steps.get(identifier) or self._tasks.get(identifier)
        if task and task is not asyncio.current_task():
            task.cancel()
        game = self.get(identifier)
        if game.status != 'ended':
            game.status = 'paused'
            self.store.put('game', game.id, game.model_dump(mode='json'))
        return game

    async def step(self, identifier, is_automatic=False):
        lock = self._locks.setdefault(identifier, asyncio.Lock())
        async with lock:
            game = self.get(identifier)
            if game.status == 'ended':
                return game
            if (
                game.node_count >= game.budget_nodes
                or game.token_usage >= game.budget_tokens
            ):
                game.status = 'paused'
                game.last_error = '已达到本局预算，请调整预算后继续'
                self.store.put('game', game.id, game.model_dump(mode='json'))
                return game
            self._steps[identifier] = asyncio.current_task()
            game.status = 'running'
            game.scheduler['runner_pid'] = os.getpid()
            game.last_error = ''
            self.store.put('game', game.id, game.model_dump(mode='json'))
            try:
                work = self.store.get('resolution_work', game.id)
                if work and work['revision'] == game.revision:
                    if not work.get('is_prepared', True):
                        resumed = Game.model_validate(work['game'])
                        work = await self._prepare_work(
                            resumed,
                            KeeperResponse.model_validate(work['reply']),
                            work['submitted'],
                            work['cycle'],
                        )
                    return await self._finish_work(game, work, is_automatic)
                if not game.last_keeper:
                    opening_audience = self._group_actor_ids(game)
                    opening = await self._invoke(
                        game,
                        game.keeper_actor_id,
                        'keeper_opening',
                        KeeperResponse,
                        {
                            'instruction': (
                                '开场介绍现场，邀请调查员。'
                                '落实原文可公开的入场关系、委托或共同目标，'
                                '让角色知道为何在此参与；不提前揭示未来真相。'
                                '不要提前解决其行动。'
                            )
                        },
                    )
                    self._validate_narration(game, opening, is_opening=True)
                    game.last_keeper = opening.model_dump(mode='json')
                    game.next_actor_ids = opening.invite_actor_ids
                    game.is_all_players = opening.is_all_players
                    if opening.scene_id:
                        self._validate_scene(game, opening.scene_id)
                        game.scene_id = opening.scene_id
                    if (
                        opening.checks
                        or opening.commands
                        or opening.is_finished
                    ):
                        cycle = self._cycle(game)
                        cycle.update(
                            selected=[],
                            eligible=[],
                            groups=[],
                            is_opening=True,
                        )
                        work = await self._prepare_work(
                            game, opening, [], cycle
                        )
                        self.store.put(
                            'control_work'
                            if work.get('force')
                            else 'resolution_work',
                            game.id,
                            work,
                        )
                        return await self._finish_work(
                            game, work, is_automatic
                        )
                    with self.store.transaction() as transaction:
                        transaction.append_event(
                            game.id,
                            'narration',
                            {'text': opening.narration},
                            actor_ids=(
                                opening.recipient_actor_ids or opening_audience
                            ),
                            is_private=not (
                                opening.recipient_actor_ids or opening_audience
                            ),
                        )
                        for (
                            actor_id,
                            message,
                        ) in opening.private_messages.items():
                            transaction.append_event(
                                game.id,
                                'narration',
                                {'text': message},
                                actor_ids=[actor_id],
                            )
                        latest = self.get(game.id)
                        game.token_usage = latest.token_usage
                        transaction.put(
                            'game', game.id, game.model_dump(mode='json')
                        )
                cycle = self._cycle(game)
                if cycle['selected'] is None:
                    selected, eligible, groups = self._select(game)
                    cycle.update(
                        selected=selected, eligible=eligible, groups=groups
                    )
                    self.store.put('cycle', game.id, cycle)
                    self.store.put(
                        'game', game.id, game.model_dump(mode='json')
                    )
                responses = await asyncio.gather(
                    *[
                        self._invoke(game, actor_id, 'player', PlayerResponse)
                        for actor_id in cycle['selected']
                    ],
                    return_exceptions=True,
                )
                submitted = []
                for actor_id, response in zip(
                    cycle['selected'], responses, strict=True
                ):
                    if isinstance(response, Exception):
                        self._offline.setdefault(game.id, set()).add(actor_id)
                        if game.mode == 'combat' or game.is_all_players:
                            raise response
                        submitted.append(
                            {'actor_id': actor_id, 'timeout': True}
                        )
                    else:
                        submitted.append(
                            {'actor_id': actor_id, **response.model_dump()}
                        )
                reply = await self._invoke(
                    game,
                    game.keeper_actor_id,
                    'keeper_resolution',
                    KeeperResponse,
                    {
                        'player_actions': submitted,
                        'combat_turn': self._combat_turn(game),
                    },
                )
                work = await self._prepare_work(game, reply, submitted, cycle)
                self.store.put(
                    'control_work' if work.get('force') else 'resolution_work',
                    game.id,
                    work,
                )
                return await self._finish_work(game, work, is_automatic)
            except asyncio.CancelledError:
                paused = self.get(identifier)
                paused.status = 'paused'
                self.store.put(
                    'game', paused.id, paused.model_dump(mode='json')
                )
                raise
            except Exception as error:
                failed = self.get(identifier)
                failed.status = 'paused'
                failed.last_error = str(error)[:500]
                self.store.put(
                    'game', failed.id, failed.model_dump(mode='json')
                )
                return failed
            finally:
                self._steps.pop(identifier, None)

    def _group_actor_ids(self, game):
        scene = next(
            (
                scene
                for scene in self.scenario(game).scenes
                if scene.id == game.scene_id
            ),
            None,
        )
        participants = (
            scene.conditions.get('participants', []) if scene else []
        )
        return [
            seat.actor_id
            for seat in game.seats
            if seat.group_id == game.group_id
            and (
                not participants
                or seat.role_id in participants
                or seat.actor_id in participants
            )
        ]

    def _select(self, game):
        cards = {card.id: card for card in self._characters(game)}
        available = [
            seat
            for seat in game.seats
            if seat.actor_id not in self._offline.get(game.id, set())
            and cards[seat.character_id].current_san > 0
            and not {
                'dead',
                'unconscious',
                'dying',
                'permanent_insanity',
            }.intersection(cards[seat.character_id].conditions)
        ]
        groups = list(dict.fromkeys(seat.group_id for seat in available))
        if game.mode == 'combat':
            turn = self._combat_turn(game)
            selected = (
                [turn['actor_id']] if turn and turn.get('actor_id') else []
            )
            return selected, selected, groups
        scenes = game.scheduler.setdefault('group_scenes', {})
        scenes[game.group_id] = game.scene_id
        for group_id in groups:
            scenes.setdefault(group_id, game.scene_id)
        game.group_id = select_group(game, groups)
        game.scene_id = scenes.get(game.group_id, game.scene_id)
        eligible = [
            seat.actor_id
            for seat in available
            if seat.actor_id in self._group_actor_ids(game)
        ]
        selected = select_actors(
            game,
            eligible,
            game.next_actor_ids,
            is_all_players=game.is_all_players,
        )
        return selected, eligible, groups

    def _combat_turn(self, game):
        from aitrpg.application.combat import combat_turn

        return combat_turn(
            game, {card.id: card for card in self._characters(game)}
        )

    async def _game_command(self, game, cards, command, work):
        from aitrpg.application.combat import apply_game_command

        return await apply_game_command(self, game, cards, command, work)

    def _validate_scene(self, game, scene_id):
        if scene_id not in {scene.id for scene in self.scenario(game).scenes}:
            raise ValueError('主持选择了不存在的场景')

    @staticmethod
    def _is_repeated_check(check, work):
        return any(
            event['kind'] == 'check'
            and event['data'].get('character_id') == check.character_id
            and normalize_skill(
                event['data'].get('details', {}).get('skill', '')
            )
            == normalize_skill(check.skill)
            and event['data'].get('reason', '') == check.reason
            for event in work['events']
        )

    def _apply_check(self, game, cards, check, work):
        if check.character_id not in cards:
            raise ValueError('检定角色不在游戏中')
        if check.opponent_character_id:
            if check.opponent_character_id not in cards:
                raise ValueError('NPC对抗须用当前轮次combat_attack')
            skill_value(
                cards[check.opponent_character_id],
                check.opponent_skill or check.skill,
            )
        canonical = normalize_skill(check.skill)
        if game.mode == 'combat' and canonical.startswith(
            ('格斗：', '射击：')
        ):
            turn = self._combat_turn(game)
            if not turn or turn['id'] != check.character_id:
                raise ValueError('攻击检定只能由当前战斗行动者执行')
            if any(
                response.get('actor_id') == cards[check.character_id].actor_id
                and response.get('is_pass')
                for response in work['submitted']
            ):
                raise ValueError('玩家本轮让出机会，不能代为攻击检定')
        if check.is_pushed:
            self._validate_push(game, check)
        roll = skill_check(
            cards[check.character_id],
            check.skill,
            check.difficulty,
            check.bonus_dice,
            is_pushed=check.is_pushed,
            mode=game.mode,
            reason=check.reason,
        )
        work['events'].append(
            {
                'kind': 'check',
                'data': {
                    **roll.model_dump(mode='json'),
                    'character_id': check.character_id,
                    'scene_id': game.scene_id,
                    'pushed_from_id': check.pushed_from_id,
                },
                'is_private': check.is_private,
            }
        )
        work['has_rolls'] = True
        if check.opponent_character_id:
            opponent = cards.get(check.opponent_character_id)
            if opponent is None:
                raise ValueError('对抗角色不在游戏中')
            opposing_roll = skill_check(
                opponent,
                check.opponent_skill or check.skill,
                reason=check.reason,
            )
            work['events'].append(
                {
                    'kind': 'check',
                    'data': {
                        **opposing_roll.model_dump(mode='json'),
                        'character_id': opponent.id,
                    },
                    'is_private': check.is_private,
                }
            )
            winner = opposed_result(roll, opposing_roll)
            work['events'].append(
                {
                    'kind': 'contest',
                    'data': {
                        'winner': winner,
                        'initiator_id': check.character_id,
                        'opponent_id': opponent.id,
                        'reason': check.reason,
                    },
                    'is_private': check.is_private,
                }
            )
        if roll.details.get('is_success'):
            card = cards[check.character_id]
            cards[card.id], _, _ = apply_command(
                card, 'mark_skill', {'skill': roll.details['skill']}
            )

    async def _prepare_work(self, game, reply, submitted, cycle):
        reply = self._normalise_control(reply)
        cards = {card.id: card for card in self._characters(game)}
        previous = self.store.get('resolution_work', game.id)
        work = (
            previous
            if previous and previous['revision'] == game.revision
            else {
                'revision': game.revision,
                'game': game.model_dump(mode='json'),
                'cards': {
                    key: card.model_dump(mode='json')
                    for key, card in cards.items()
                },
                'events': [],
                'reply': reply.model_dump(mode='json'),
                'submitted': submitted,
                'cycle': cycle,
                'has_rolls': False,
                'started_in_combat': game.mode == 'combat',
                'is_opening': cycle.get('is_opening', False),
                'audience_actor_ids': self._group_actor_ids(game),
                'is_prepared': False,
                'check_index': 0,
                'command_index': 0,
            }
        )
        if previous and previous['revision'] == game.revision:
            game = Game.model_validate(work['game'])
            cards = {
                key: Character.model_validate(value)
                for key, value in work['cards'].items()
            }
            work['reply'] = reply.model_dump(mode='json')
        self._checkpoint(game, cards, work)
        for check_index, check in enumerate(reply.checks):
            if check_index < work['check_index']:
                continue
            event_count = len(work['events'])
            try:
                self._apply_check(game, cards, check, work)
            except ValueError as error:
                if len(work['events']) != event_count:
                    raise
                from aitrpg.application.ruling_repair import repair_check

                await repair_check(
                    self, game, cards, check, check_index, work, error
                )
            work['check_index'] = check_index + 1
            self._checkpoint(game, cards, work)
        for command_index, command in enumerate(reply.commands):
            if command_index < work['command_index']:
                continue
            event_count = len(work['events'])
            try:
                await self._apply_command(game, cards, command, work)
            except ValueError as error:
                if len(work['events']) != event_count:
                    raise
                from aitrpg.application.ruling_repair import repair_command

                await repair_command(self, game, cards, command, work, error)
            work['command_index'] = command_index + 1
            self._checkpoint(game, cards, work)
        work['is_prepared'] = True
        self._checkpoint(game, cards, work)
        return work

    def _normalise_control(self, reply):
        parsed = reply.model_copy(deep=True)
        commands = []
        for command in parsed.commands:
            if command.kind == 'flags' and not command.character_id:
                flags = command.parameters.get('flags', command.parameters)
                if not isinstance(flags, dict):
                    raise ValueError('全局旗标必须是对象')
                if any(
                    key in parsed.flags and parsed.flags[key] != value
                    for key, value in flags.items()
                ):
                    raise ValueError('重复的全局旗标值不一致')
                parsed.flags = {**parsed.flags, **flags}
                continue
            if command.kind not in {
                'advance_hours',
                'advance_days',
                'advance_time',
            }:
                commands.append(command)
                continue
            parameters = command.parameters
            hours = parameters.get('hours', 0)
            days = parameters.get('days', 0)
            if command.kind == 'advance_hours':
                hours = parameters.get('hours', parameters.get('amount', 0))
            if command.kind == 'advance_days':
                days = parameters.get('days', parameters.get('amount', 0))
            if (
                parsed.advance_hours
                and hours
                and parsed.advance_hours != hours
            ):
                raise ValueError('重复的时间推进小时数不一致')
            if parsed.advance_days and days and parsed.advance_days != days:
                raise ValueError('重复的时间推进天数不一致')
            if (
                hours
                or command.kind == 'advance_hours'
                or 'hours' in parameters
            ):
                parsed.advance_hours = parsed.advance_hours or hours
            if days or command.kind == 'advance_days' or 'days' in parameters:
                parsed.advance_days = parsed.advance_days or days
        parsed.commands = commands
        validated = KeeperResponse.model_validate(parsed.model_dump())
        return KeeperResponse.model_construct(
            _fields_set=parsed.model_fields_set,
            **{
                name: getattr(validated, name)
                for name in KeeperResponse.model_fields
            },
        )

    def _checkpoint(self, game, cards, work):
        work['game'] = game.model_dump(mode='json')
        work['cards'] = {
            key: card.model_dump(mode='json') for key, card in cards.items()
        }
        self.store.put(
            'control_work' if work.get('force') else 'resolution_work',
            game.id,
            work,
        )

    def _validate_push(self, game, check):
        if not check.pushed_from_id:
            raise ValueError('孤注一掷必须引用先前失败的检定')
        events = self.store.events(game.id)
        previous = next(
            (
                event['data']
                for event in events
                if event['kind'] == 'check'
                and event['data'].get('id') == check.pushed_from_id
            ),
            None,
        )
        if (
            not previous
            or previous.get('character_id') != check.character_id
            or previous.get('scene_id') != game.scene_id
            or previous['details'].get('skill') != check.skill
            or previous['details'].get('is_success')
            or previous.get('pushed_from_id')
        ):
            raise ValueError('只有同场景本人失败的普通技能检定可重投')
        if any(
            event['data'].get('pushed_from_id') == check.pushed_from_id
            for event in events
        ):
            raise ValueError('同一个失败检定只能孤注一掷一次')

    async def _apply_command(self, game, cards, command, work):
        if command.kind == 'combat_attack':
            attacker = cards.get(command.parameters.get('attacker_id'))
            if attacker and any(
                response.get('actor_id') == attacker.actor_id
                and response.get('is_pass')
                for response in work['submitted']
            ):
                raise ValueError('玩家本轮让出机会，不能代为主动攻击')
        sanity_roll = None
        if command.kind == 'sanity':
            used = work.setdefault('sanity_uses', {})
            requested = command.parameters.get(
                'sanity_check_id', command.parameters.get('check_id')
            )
            candidates = [
                event['data']
                for event in work['events']
                if event['kind'] == 'check'
                and event['data'].get('character_id') == command.character_id
                and str(
                    event['data'].get('details', {}).get('skill', '')
                ).lower()
                in {'san', 'sanity', '理智'}
                and event['data']['id'] not in used
                and (not requested or event['data']['id'] == requested)
            ]
            if requested and not candidates:
                raise ValueError('指定理智检定不存在、归属错误或已消费')
            if len(candidates) > 1:
                raise ValueError('多个理智检定须明确sanity_check_id')
            if candidates:
                sanity_roll = Roll.model_validate(
                    {
                        key: value
                        for key, value in candidates[0].items()
                        if key in Roll.model_fields
                    }
                )
        if command.kind == 'condition' and not command.character_id:
            name = command.parameters.get('name', '')
            is_present = command.parameters.get('is_present', True)
            if (
                not isinstance(name, str)
                or not name
                or type(is_present) is not bool
            ):
                raise ValueError('全局状态旗标需要名称与布尔值')
            game.flags[name] = is_present
            work['events'].append(
                {
                    'kind': 'ruling',
                    'data': {
                        'command': 'world_condition',
                        'reason': command.reason,
                        'details': {name: is_present},
                        'authority': '主持裁定',
                    },
                    'is_private': True,
                }
            )
            return
        if command.kind == 'custom' and not command.character_id:
            if 'changes' in command.parameters:
                raise ValueError('全局裁定不能无角色指定地修改角色状态')
            note = command.parameters.get('note', command.reason)
            if not isinstance(note, str) or not note.strip():
                raise ValueError('全局裁定记录需要文字依据')
            partial = command.parameters.get('clue_partial')
            if partial and partial not in {
                clue.id for clue in self.scenario(game).clues
            }:
                raise ValueError('局部线索记录引用不存在的线索')
            work['events'].append(
                {
                    'kind': 'ruling',
                    'data': {
                        'reason': command.reason,
                        'command': 'keeper_note',
                        'details': command.parameters,
                        'authority': '主持裁定',
                    },
                    'is_private': True,
                }
            )
            return
        if command.kind in {
            'start_combat',
            'end_combat',
            'split_groups',
            'combat_attack',
        }:
            await self._game_command(game, cards, command, work)
            return
        if command.character_id not in cards:
            raise ValueError('状态裁定角色不在游戏中')
        card = cards[command.character_id]
        if command.kind == 'mark_skill' and not command.parameters.get(
            'skill'
        ):
            successful = [
                event['data']
                for event in work['events']
                if event['kind'] == 'check'
                and event['data'].get('character_id') == card.id
                and event['data'].get('details', {}).get('is_success')
            ]
            marked = [data['details'].get('skill') for data in successful]
            if marked and all(skill in card.skill_marks for skill in marked):
                return
            raise ValueError('成长标记必须指定已成功的技能')
        if command.kind == 'background':
            updates = command.parameters.get('updates', {})
            if not isinstance(updates, dict) or any(
                not isinstance(value, str) for value in updates.values()
            ):
                raise ValueError('背景更新必须是文字字段')
            card = card.model_copy(deep=True)
            card.background.update(updates)
            rolls, details = [], {'background_updates': updates}
        elif command.kind == 'custom':
            changes = command.parameters.get('changes', {})
            allowed = {
                'current_hp',
                'current_mp',
                'current_san',
                'conditions',
                'inventory',
                'experiences',
                'relationships',
                'background',
                'skills',
                'attributes',
                'weapons',
                'assets',
            }
            if not set(changes).issubset(allowed):
                raise ValueError('自定义裁定不能改变身份、占用或任意字段')
            merged = card.model_dump()
            for key, value in changes.items():
                if key in {'background', 'skills', 'attributes', 'assets'}:
                    if not isinstance(value, dict):
                        raise ValueError('自定义字段变更必须是对象')
                    merged[key].update(value)
                else:
                    merged[key] = value
            card = derived_character(Character.model_validate(merged))
            if any(value < 0 for value in card.skills.values()):
                raise ValueError('技能不能为负数')
            self._validate_card_state(card)
            rolls, details = [], {'authority': '主持裁定', 'changes': changes}
        else:
            healer = None
            if command.kind == 'heal' and command.parameters.get('healer_id'):
                healer = cards.get(command.parameters['healer_id'])
                if healer is None:
                    raise ValueError('治疗者不在游戏中')
            card, rolls, details = apply_command(
                card,
                command.kind,
                command.parameters,
                day=game.scheduler.get('base_day', 0) + game.day,
                healer=healer,
                sanity_roll=sanity_roll,
            )
            if command.kind == 'sanity' and details.get('sanity_check_id'):
                work['sanity_uses'][details['sanity_check_id']] = command.id
        cards[card.id] = card
        for roll in rolls:
            work['events'].append(
                {
                    'kind': 'check',
                    'data': {
                        **roll.model_dump(mode='json'),
                        'character_id': roll.details.get('healer_id', card.id),
                    },
                    'is_private': command.is_private,
                }
            )
            work['has_rolls'] = True
        work['events'].append(
            {
                'kind': 'ruling',
                'data': {
                    'character_id': card.id,
                    'reason': command.reason,
                    'command': command.kind,
                    'details': details,
                },
                'is_private': command.is_private,
            }
        )

    def _validate_card_state(self, card):
        if not (
            0 <= card.current_hp <= card.max_hp
            and 0 <= card.current_mp <= card.max_mp
            and 0 <= card.current_san <= card.max_san
        ):
            raise ValueError('当前状态不能超出角色上限')

    async def _finish_work(self, original, work, is_automatic):
        game = Game.model_validate(work['game'])
        cards = {
            key: Character.model_validate(value)
            for key, value in work['cards'].items()
        }
        reply = KeeperResponse.model_validate(work['reply'])
        repairs = work.get('command_repairs', {})
        completed = [
            command
            for key, record in repairs.items()
            if key.startswith('check:')
            for command in RulingRepair.model_validate(
                record['response']
            ).commands
        ]
        for command in reply.commands:
            if command.id in repairs:
                repair = RulingRepair.model_validate(
                    repairs[command.id]['response']
                )
                completed.extend(repair.commands)
            else:
                completed.append(command)
        reply.commands = completed
        for record in repairs.values():
            repair = RulingRepair.model_validate(record['response'])
            reply.narration = repair.narration
            reply.private_messages = repair.private_messages
            reply.improvisation = repair.improvisation
            reply.flags = repair.flags
        if work['has_rolls']:
            from aitrpg.application.feedback import finish_feedback

            reply = await finish_feedback(
                self, original, game, cards, reply, work
            )
        group_scenes = game.scheduler.setdefault('group_scenes', {})
        reply = await self._synchronise_controls(original, reply, work)
        group_scenes[game.group_id] = game.scene_id
        if reply.group_id:
            if reply.group_id not in {seat.group_id for seat in game.seats}:
                raise ValueError('主持选择了不存在的分队')
            game.group_id = reply.group_id
            game.scene_id = group_scenes.get(game.group_id, game.scene_id)
        if reply.scene_id:
            self._validate_scene(game, reply.scene_id)
            game.scene_id = reply.scene_id
        if not work.get('time_applied'):
            hours = reply.advance_days * 24 + reply.advance_hours
            self._advance_clock(game, cards, hours, work['events'])
            work['time_applied'] = True
            self._checkpoint(game, cards, work)
        game.flags.update(reply.flags)
        game.next_actor_ids = reply.invite_actor_ids
        game.is_all_players = reply.is_all_players
        game.last_keeper = reply.model_dump(mode='json')
        game.scheduler.setdefault('group_scenes', {})[game.group_id] = (
            game.scene_id
        )
        if (
            work.get('started_in_combat', False)
            and game.mode == 'combat'
            and game.combat.get('order')
        ):
            game.combat['turn'] = game.combat.get('turn', 0) + 1
            if game.combat['turn'] >= len(game.combat['order']):
                game.combat['turn'] = 0
                game.combat['round'] = game.combat.get('round', 1) + 1
                game.combat['defenses'] = {}
        if game.mode == 'combat':
            game.combat['completed_command_ids'] = []
        game.node_count += 1
        scene_nodes = game.scheduler.setdefault('scene_nodes', {})
        scene_nodes[original.scene_id] = (
            scene_nodes.get(original.scene_id, 0) + 1
        )
        game.scheduler = record_selection(
            game,
            work['cycle'].get('eligible', []),
            work['cycle'].get('selected', []),
            work['cycle'].get('groups', []),
        )
        self._validate_narration(
            original, reply, is_opening=work.get('is_opening', False)
        )
        recipients = self._narration_audience(
            original, reply, is_opening=work.get('is_opening', False)
        )
        scenario = self.scenario(game)
        if not set(reply.reveal_clue_ids).issubset(
            {clue.id for clue in scenario.clues}
        ):
            raise ValueError('揭示的线索不存在')
        if not set(reply.reveal_asset_ids).issubset(
            {asset.id for asset in scenario.assets}
        ):
            raise ValueError('揭示的素材不存在')
        for actor_id in recipients:
            game.knowledge[actor_id] = list(
                dict.fromkeys(
                    game.knowledge.get(actor_id, []) + reply.reveal_clue_ids
                )
            )
            game.revealed_assets[actor_id] = list(
                dict.fromkeys(
                    game.revealed_assets.get(actor_id, [])
                    + reply.reveal_asset_ids
                )
            )
        game.status = (
            'ended'
            if reply.is_finished
            else ('running' if is_automatic else 'paused')
        )
        game.ending = reply.ending if reply.is_finished else ''
        audience = work.get(
            'audience_actor_ids', self._group_actor_ids(original)
        )
        with self.store.transaction() as transaction:
            current = Game.model_validate(transaction.get('game', game.id))
            if current.revision != original.revision:
                raise ConflictError('裁定基础已变化，旧结果不能应用')
            game.token_usage = current.token_usage
            game.budget_nodes = current.budget_nodes
            game.budget_tokens = current.budget_tokens
            for map_key in ('map_positions', 'map_regions'):
                if map_key in current.flags:
                    game.flags[map_key] = current.flags[map_key]
            for actor_id, identifiers in current.revealed_assets.items():
                game.revealed_assets[actor_id] = list(
                    dict.fromkeys(
                        game.revealed_assets.get(actor_id, []) + identifiers
                    )
                )
            game.revision = original.revision + 1
            for character_id, card in cards.items():
                previous = transaction.get('character', character_id)
                card.version = previous['version'] + 1
                card.allocations.setdefault('runtime', {})['lifetime_day'] = (
                    game.scheduler.get('base_day', 0) + game.day
                )
                if reply.is_finished:
                    card.locked_game_id = None
                    card.experiences.append(f'{game.name}：{reply.ending}')
                transaction.put(
                    'character', character_id, card.model_dump(mode='json')
                )
            for submitted in work['submitted']:
                if submitted.get('timeout'):
                    transaction.append_event(
                        game.id,
                        'status',
                        {
                            'text': '玩家回应超时',
                            'actor_id': submitted['actor_id'],
                        },
                    )
                else:
                    transaction.append_event(
                        game.id,
                        'player',
                        submitted,
                        actor_ids=audience,
                        is_private=not audience,
                    )
            for event in work['events']:
                transaction.append_event(
                    game.id,
                    event['kind'],
                    event['data'],
                    actor_ids=audience,
                    is_private=event.get('is_private', False) or not audience,
                )
            narration_event = transaction.append_event(
                game.id,
                'narration',
                {'text': reply.narration},
                actor_ids=recipients,
                is_private=not recipients,
            )
            if reply.advance_days or reply.advance_hours:
                game.scheduler['clock_anchor'] = narration_event['sequence']
            for actor_id, message in reply.private_messages.items():
                transaction.append_event(
                    game.id,
                    'narration',
                    {'text': message},
                    actor_ids=[actor_id],
                )
            if reply.improvisation:
                transaction.append_event(
                    game.id,
                    'improvisation',
                    {'text': reply.improvisation},
                    is_private=True,
                )
            transaction.put('game', game.id, game.model_dump(mode='json'))
            transaction.put(
                'snapshot',
                f'{game.id}@{game.revision}',
                {
                    'game': game.model_dump(mode='json'),
                    'characters': {
                        key: card.model_dump(mode='json')
                        for key, card in cards.items()
                    },
                },
            )
            transaction.delete('resolution_work', game.id)
            transaction.delete('cycle', game.id)
        return game

    def _advance_clock(self, game, cards, hours, events):
        new_clock = game.hour + hours
        elapsed_hours = math.floor(new_clock) - math.floor(game.hour)
        elapsed_days = math.floor(new_clock / 24)
        game.day += elapsed_days
        game.hour = new_clock % 24
        if not elapsed_hours:
            return
        for character_id, card in cards.items():
            if 'dead' in card.conditions:
                continue
            card, rolls, details = apply_command(
                card,
                'recover',
                {'days': elapsed_days, 'hours': elapsed_hours},
                day=game.scheduler.get('base_day', 0) + game.day,
            )
            cards[character_id] = card
            for roll in rolls:
                events.append(
                    {
                        'kind': 'check',
                        'data': {
                            **roll.model_dump(mode='json'),
                            'character_id': character_id,
                        },
                        'is_private': False,
                    }
                )
            events.append(
                {
                    'kind': 'ruling',
                    'data': {
                        'character_id': character_id,
                        'command': 'natural_recovery',
                        'reason': '游戏时间推进',
                        'details': details,
                    },
                    'is_private': False,
                }
            )

    async def synchronise_controls(self, identifier):
        await self.pause(identifier)
        async with self._locks.setdefault(identifier, asyncio.Lock()):
            game = self.get(identifier)
            if game.status == 'ended':
                raise ValueError('结束后的游戏不能修正运行状态')
            if self.store.get('resolution_work', identifier):
                raise ValueError('尚有未完成裁定，请先处理当前错误')
            records = self.store.events(identifier)
            narratives = [
                event for event in records if event['kind'] == 'narration'
            ]
            if not narratives:
                raise ValueError('尚无已完成叙事可供核对')
            work = self.store.get('control_work', identifier)
            if not work or work.get('revision') != game.revision:
                work = {
                    'events': [],
                    'force': True,
                    'revision': game.revision,
                }
            reply = await self._synchronise_controls(
                game,
                KeeperResponse(narration=narratives[-1]['data']['text']),
                work,
            )
            cards = {card.id: card for card in self._characters(game)}
            self._advance_clock(
                game,
                cards,
                reply.advance_days * 24 + reply.advance_hours,
                work['events'],
            )
            if reply.scene_id:
                game.scene_id = reply.scene_id
                game.scheduler.setdefault('group_scenes', {})[
                    game.group_id
                ] = reply.scene_id
            with self.store.transaction() as transaction:
                current = transaction.get('game', identifier)
                if current['revision'] != game.revision:
                    raise ConflictError('核对期间游戏状态已变化')
                game.token_usage = current['token_usage']
                game.revision += 1
                game.last_error = ''
                game.scheduler['clock_anchor'] = narratives[-1]['sequence']
                for card in cards.values():
                    card.version += 1
                    card.allocations.setdefault('runtime', {})[
                        'lifetime_day'
                    ] = game.scheduler.get('base_day', 0) + game.day
                    transaction.put(
                        'character', card.id, card.model_dump(mode='json')
                    )
                for event in work['events']:
                    transaction.append_event(
                        identifier,
                        event['kind'],
                        event['data'],
                        actor_ids=self._group_actor_ids(game),
                        is_private=event.get('is_private', False),
                    )
                transaction.append_event(
                    identifier,
                    'intervention',
                    {'text': '主持核对已发生叙事的时间与场景'},
                    is_private=True,
                )
                transaction.put(
                    'game', identifier, game.model_dump(mode='json')
                )
                transaction.put(
                    'snapshot',
                    f'{identifier}@{game.revision}',
                    {
                        'game': game.model_dump(mode='json'),
                        'characters': {
                            key: card.model_dump(mode='json')
                            for key, card in cards.items()
                        },
                    },
                )
                transaction.delete('control_work', identifier)
                transaction.delete('cycle', identifier)
            return game

    async def _synchronise_controls(self, game, reply, work):
        cues = (
            r'第二天|次日|翌日|过夜|离岸|登岸|下船|抵达|到达|小时后|半小时后'
        )
        need_clock = work.get('force') or (
            not (reply.advance_days or reply.advance_hours)
            and re.search(cues, reply.narration)
        )
        need_scene = work.get('force') or (
            not reply.scene_id
            and any(
                key.startswith(('at_', 'on_foot_', 'party_ashore')) and value
                for key, value in reply.flags.items()
            )
        )
        if not need_clock and not need_scene:
            return reply
        scenario = self.scenario(game)
        old = [
            event
            for event in self.store.events(game.id)
            if event['kind'] == 'narration'
            and (
                work.get('force')
                or event['sequence'] > game.scheduler.get('clock_anchor', 0)
            )
        ][-10 if work.get('force') else -5 :]
        narratives = [event['data'].get('text', '') for event in old] + [
            reply.narration
        ]
        control_context = {
            'current': {
                'day': game.day,
                'hour': game.hour,
                'scene_id': game.scene_id,
            },
            'narratives': narratives,
            'audit_instruction': (
                '这是对已提交历史的补漏核对，current字段可能漏记过去的'
                '跨日或误把路线讨论当成到达。以已发生叙事确定实际时空。'
                '不能因字段声称某地点就忽略叙事只是在讨论未来去那里。'
            )
            if work.get('force')
            else '',
            'is_estimation_allowed': self.platform.settings.is_estimated_time,
            'scene_catalog': [
                {
                    'id': scene.id,
                    'title': scene.title,
                    'description': (
                        scene.public_text + '\n' + scene.keeper_text
                    )[:1000],
                    'next_scene_ids': scene.next_scene_ids,
                    'conditions': scene.conditions,
                }
                for scene in scenario.scenes
            ],
        }
        if 'control_projection' not in work:
            projection = await self._invoke(
                game,
                game.keeper_actor_id,
                'keeper_control',
                KeeperControl,
                extra=control_context,
            )
            work['control_projection'] = projection.model_dump(mode='json')
            self.store.put(
                'control_work' if work.get('force') else 'resolution_work',
                game.id,
                work,
            )
        projection = KeeperControl.model_validate(work['control_projection'])
        changed = reply.model_copy(deep=True)
        joined = '\n'.join(narratives)
        invalid_quotes = [
            key
            for key, status in (
                ('clock_quote', projection.clock_status),
                ('scene_quote', projection.scene_status),
            )
            if status in {'supported', 'estimated'}
            and (
                not getattr(projection, key)
                or getattr(projection, key) not in joined
            )
        ]
        if invalid_quotes and not work.get('control_repaired'):
            projection = await self._invoke(
                game,
                game.keeper_actor_id,
                'keeper_control',
                KeeperControl,
                extra={
                    **control_context,
                    'reaction_key': 'quote_repair',
                    'previous': projection.model_dump(mode='json'),
                    'validation_error': (
                        '引用必须是一段连续原文，禁止拼接或省略：'
                        + ', '.join(invalid_quotes)
                        + '。同时核对场景的剧情阶段与行进方向。'
                    ),
                },
            )
            work['control_projection'] = projection.model_dump(mode='json')
            work['control_repaired'] = True
            self.store.put(
                'control_work' if work.get('force') else 'resolution_work',
                game.id,
                work,
            )
        if (
            projection.clock_status in {'supported', 'estimated'}
            and need_clock
        ):
            if (
                not projection.clock_quote
                or projection.clock_quote not in joined
            ):
                raise ValueError('时间投影缺少已发生叙事依据')
            if (
                projection.clock_status == 'estimated'
                and not self.platform.settings.is_estimated_time
            ):
                raise ValueError('时间仅有近似依据，当前配置要求人工核对')
            if projection.target_day is None or projection.target_hour is None:
                raise ValueError('时间投影缺少绝对时刻')
            delta = (
                projection.target_day * 24
                + projection.target_hour
                - game.day * 24
                - game.hour
            )
            if delta < 0:
                raise ValueError('控制投影不能使游戏时间倒退')
            changed.advance_hours = delta
        elif projection.clock_status == 'needs_review' and need_clock:
            raise ValueError('已发生叙事的时间需要核对：' + projection.reason)
        if projection.scene_status == 'supported' and (
            need_scene or need_clock
        ):
            if (
                not projection.scene_quote
                or projection.scene_quote not in joined
            ):
                raise ValueError('场景投影缺少已发生叙事依据')
            self._validate_scene(game, projection.scene_id)
            changed.scene_id = projection.scene_id
        elif (
            projection.scene_status == 'needs_review'
            and (need_scene or need_clock)
            and not reply.scene_id
        ):
            raise ValueError('场景控制需要核对：' + projection.reason)
        if not work.get('projection_recorded'):
            work['events'].append(
                {
                    'kind': 'control_projection',
                    'data': projection.model_dump(mode='json'),
                    'is_private': True,
                }
            )
            work['projection_recorded'] = True
            self.store.put(
                'control_work' if work.get('force') else 'resolution_work',
                game.id,
                work,
            )
        return KeeperResponse.model_validate(changed.model_dump())

    def update_budget(self, identifier, budget_nodes, budget_tokens):
        game = self.get(identifier)
        updated = Game.model_validate(
            {
                **game.model_dump(),
                'budget_nodes': budget_nodes,
                'budget_tokens': budget_tokens,
            }
        )
        self.store.put('game', game.id, updated.model_dump(mode='json'))
        return updated

    def update_map(self, identifier, asset_id, positions, revealed_regions):
        game = self.get(identifier)
        scenario = self.scenario(game)
        asset = next(
            (asset for asset in scenario.assets if asset.id == asset_id), None
        )
        if asset is None or not asset.is_map:
            raise ValueError('所选素材不是该局的地图')
        card_ids = {seat.character_id for seat in game.seats}
        actors = {seat.actor_id for seat in game.seats}
        region_ids = {region['id'] for region in asset.regions}
        clean_positions = {}
        for card_id, position in positions.items():
            if card_id not in card_ids:
                raise ValueError('位置标记不属于该局角色')
            if position.get('node_id'):
                node = next(
                    (
                        node
                        for node in asset.nodes
                        if node.get('id') == position['node_id']
                    ),
                    None,
                )
                if node is None or 'x' not in node or 'y' not in node:
                    raise ValueError('该节点尚无审核后的地图坐标')
                position = {
                    'x': node['x'],
                    'y': node['y'],
                    'node_id': node['id'],
                }
            if any(
                type(position.get(key)) not in (int, float)
                or not 0 <= position[key] <= 1
                for key in ('x', 'y')
            ):
                raise ValueError('地图位置必须是 0 到 1 的归一化坐标')
            clean_positions[card_id] = position
        if not set(revealed_regions).issubset(actors):
            raise ValueError('地图揭示对象不在游戏中')
        if any(
            not set(regions).issubset(region_ids)
            for regions in revealed_regions.values()
        ):
            raise ValueError('揭示的地图区域不存在')
        with self.store.transaction() as transaction:
            latest = Game.model_validate(transaction.get('game', identifier))
            latest.flags.setdefault('map_positions', {})[asset_id] = (
                clean_positions
            )
            latest.flags.setdefault('map_regions', {}).setdefault(
                asset_id, {}
            ).update(revealed_regions)
            for actor_id in revealed_regions:
                items = latest.revealed_assets.setdefault(actor_id, [])
                if asset_id not in items:
                    items.append(asset_id)
            transaction.put('game', latest.id, latest.model_dump(mode='json'))
            transaction.append_event(
                latest.id, 'map', {'asset_id': asset_id}, is_private=True
            )
        return latest

    def _command_key(self, command):
        kind = {'san': 'sanity', 'growth': 'grow'}.get(
            command.kind, command.kind
        )
        parameters = dict(command.parameters)
        defaults = {
            'damage': {'armor': 0},
            'condition': {'is_present': True},
            'heal': {'source': 'first_aid'},
            'recover': {'days': 0},
        }
        for key, value in defaults.get(kind, {}).items():
            parameters.setdefault(key, value)
        return json.dumps(
            [kind, command.character_id, parameters], sort_keys=True
        )

    async def intervene(self, identifier, text, character_id=None, patch=None):
        await self.pause(identifier)
        lock = self._locks.setdefault(identifier, asyncio.Lock())
        async with lock:
            game = self.get(identifier)
            if game.status == 'ended':
                raise ValueError('结束后的游戏不能修正运行状态')
            with self.store.transaction() as transaction:
                if character_id and patch:
                    if character_id not in {
                        seat.character_id for seat in game.seats
                    }:
                        raise ValueError('角色不属于该游戏')
                    allowed = {
                        'current_hp',
                        'current_mp',
                        'current_san',
                        'conditions',
                        'background',
                        'inventory',
                        'experiences',
                    }
                    if not set(patch).issubset(allowed):
                        raise ValueError('修正仅允许角色状态与背景字段')
                    previous = transaction.get('character', character_id)
                    card = Character.model_validate(
                        {
                            **previous,
                            **patch,
                            'version': previous['version'] + 1,
                        }
                    )
                    self._validate_card_state(card)
                    transaction.put(
                        'character', card.id, card.model_dump(mode='json')
                    )
                game.revision += 1
                transaction.put('game', game.id, game.model_dump(mode='json'))
                transaction.append_event(
                    game.id,
                    'intervention',
                    {
                        'text': text,
                        'character_id': character_id,
                        'patch': patch,
                    },
                    is_private=True,
                )
                for record in transaction.list(
                    'invitation',
                    game_id=game.id,
                    statuses=['pending', 'claimed'],
                ):
                    if record['game_id'] == game.id and record['status'] in {
                        'pending',
                        'claimed',
                    }:
                        record['status'] = 'cancelled'
                        transaction.put('invitation', record['id'], record)
                transaction.delete('cycle', game.id)
                transaction.delete('resolution_work', game.id)
                transaction.delete('control_work', game.id)
            return game

    async def finish(self, identifier, ending='用户结束本局'):
        await self.pause(identifier)
        async with self._locks.setdefault(identifier, asyncio.Lock()):
            with self.store.transaction() as transaction:
                game = Game.model_validate(transaction.get('game', identifier))
                if game.status == 'ended':
                    return game
                game.status = 'ended'
                game.ending = ending
                game.revision += 1
                for seat in game.seats:
                    card = Character.model_validate(
                        transaction.get('character', seat.character_id)
                    )
                    card.locked_game_id = None
                    card.version += 1
                    card.experiences.append(f'{game.name}：{ending}')
                    transaction.put(
                        'character', card.id, card.model_dump(mode='json')
                    )
                transaction.put('game', game.id, game.model_dump(mode='json'))
                transaction.delete('cycle', game.id)
                transaction.delete('resolution_work', game.id)
                transaction.append_event(game.id, 'status', {'text': ending})
            return game

    def issue_token(self, game_id, actor_id):
        game = self.get(game_id)
        if actor_id not in {
            game.keeper_actor_id,
            *[seat.actor_id for seat in game.seats],
        }:
            raise ValueError('身份不在游戏中')
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        self.store.put(
            'agent_token',
            digest,
            {'game_id': game_id, 'actor_id': actor_id, 'is_revoked': False},
        )
        return token

    def authenticate(self, token):
        record = self.store.get(
            'agent_token', hashlib.sha256(token.encode()).hexdigest()
        )
        if not record or record.get('is_revoked'):
            raise ValueError('接入凭据无效或已撤销')
        return record

    async def wait_for_invitation(self, token, wait_seconds=20):
        identity = self.authenticate(token)
        deadline = asyncio.get_running_loop().time() + min(
            max(wait_seconds, 0), 20
        )
        while True:
            for record in self.store.list(
                'invitation',
                game_id=identity['game_id'],
                actor_id=identity['actor_id'],
                statuses=['pending', 'claimed'],
            ):
                if (
                    record['actor_id'] == identity['actor_id']
                    and record['game_id'] == identity['game_id']
                    and record['status'] in {'pending', 'claimed'}
                ):
                    invitation = Invitation.model_validate(record)
                    game = self.get(invitation.game_id)
                    if (
                        invitation.expires_at > utc_now()
                        and invitation.revision == game.revision
                    ):
                        invitation.status = 'claimed'
                        self.store.put(
                            'invitation',
                            invitation.id,
                            invitation.model_dump(mode='json'),
                        )
                        return invitation.model_dump(mode='json')
            game = self.get(identity['game_id'])
            if (
                game.status == 'ended'
                or asyncio.get_running_loop().time() >= deadline
            ):
                return {
                    'invitation': None,
                    'game_status': game.status,
                    'retry_after': 2,
                }
            await asyncio.sleep(0.2)

    def asset_path(self, game_id, asset_id, actor_id=None) -> Path:
        game = self.get(game_id)
        seat = next(
            (seat for seat in game.seats if seat.actor_id == actor_id), None
        )
        is_keeper = actor_id is None or actor_id == game.keeper_actor_id
        if not is_keeper and seat is None:
            raise ValueError('该身份不在游戏中')
        path = self.platform.scenarios.asset_for_viewer(
            self.scenario(game),
            asset_id,
            role_id=seat.role_id if seat else None,
            is_keeper=is_keeper,
            revealed_ids=game.revealed_assets.get(actor_id, []),
            revealed_regions=game.flags.get('map_regions', {})
            .get(asset_id, {})
            .get(actor_id, []),
        )
        positions = self._visible_positions(game, asset_id, actor_id)
        if not positions:
            return path
        fingerprint = hashlib.sha256(
            json.dumps(
                [str(path), path.stat().st_mtime_ns, positions], sort_keys=True
            ).encode()
        ).hexdigest()
        directory = self.platform.settings.data_dir / 'map_cache'
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f'{fingerprint}.png'
        if not destination.exists():
            with Image.open(path) as original:
                rendered = original.convert('RGBA')
                draw = ImageDraw.Draw(rendered)
                radius = max(7, rendered.width // 80)
                for index, position in enumerate(positions.values(), start=1):
                    x = int(position['x'] * rendered.width)
                    y = int(position['y'] * rendered.height)
                    draw.ellipse(
                        (x - radius, y - radius, x + radius, y + radius),
                        fill='#9d6544',
                        outline='white',
                        width=2,
                    )
                    draw.text(
                        (x - radius / 3, y - radius / 2),
                        str(index),
                        fill='white',
                    )
                rendered.save(destination)
        return destination
