from __future__ import annotations

import asyncio
import hashlib
import json
import math
import secrets
from datetime import timedelta
from pathlib import Path

from PIL import Image
from PIL import ImageDraw

from aitrpg.adapters.storage import ConflictError
from aitrpg.domain.models import Actor
from aitrpg.domain.models import Character
from aitrpg.domain.models import Game
from aitrpg.domain.models import Invitation
from aitrpg.domain.models import KeeperResponse
from aitrpg.domain.models import PlayerResponse
from aitrpg.domain.models import Provider
from aitrpg.domain.models import Scenario
from aitrpg.domain.models import Seat
from aitrpg.domain.models import new_id
from aitrpg.domain.models import utc_now
from aitrpg.domain.privacy import retrieve_sources
from aitrpg.domain.privacy import scenario_for_viewer
from aitrpg.domain.rules import apply_command
from aitrpg.domain.rules import derived_character
from aitrpg.domain.rules import opposed_result
from aitrpg.domain.rules import skill_check
from aitrpg.domain.scheduler import record_selection
from aitrpg.domain.scheduler import select_actors
from aitrpg.domain.scheduler import select_group

PLAYER_INSTRUCTION = (
    '你是CoC7调查员，只根据本人角色、可见记录和现场行动。'
    '认真扮演背景、动机与关系。不要假装知道未提供的剧本秘密。'
    '台词写speech，具体行动写intent；有必要可观察或让出机会。'
    '不得自行投骰、宣称检定结果或修改状态。'
    '意图默认只给主持，公开行动可设intent_visibility=public。'
    '防御邀请时选择defense=dodge/fight_back/cover。'
)
KEEPER_INSTRUCTION = (
    '你是CoC7主持人。保留模组核心真相，允许合理即兴并记录improvisation。'
    '公平安排不同数量和组合的调查员，避免机械车轮。'
    '检查只填写checks，平台会给真实结果；未知结果前不要叙述成功或失败。'
    '普通状态用commands请求，所有骰点和计算由平台执行。'
    '公开narration不能包含主持专用资料、非现场角色的秘密或私密判定。'
    '命令参数：damage(amount,armor=0)、sanity(success_loss,failure_loss)、'
    'heal(source=first_aid或medicine,wound_id可选)、'
    'recover(hours=0,days=0)、grow(skills列表)、'
    'spend_mp(amount)、condition(name,is_present布尔)、'
    'add_item(item对象)、remove_item(item_id)、mark_skill(skill)、'
    'background(updates对象)、custom(changes对象，须有裁定依据)。'
    '場景切换使用scene_id；split_groups的groups为组名到actor_id列表。'
    'start_combat的npcs条目必须有id,name,dex,hp,fighting,dodge,damage,armor；'
    '并提供source_id或明确improvisation，readied_guns列出已准备枪械角色卡id。'
    'combat_attack参数attacker_id,target_id,weapon_index,mode=melee或firearm；'
    '玩家伤害技能来自真实角色卡，不传weapon_damage或替玩家选defense。'
    '战斗行动顺序由平台规定，NPC不能凭空改变调查员行动次数。'
    '结束用end_combat。结束模组时is_finished=true且说明ending。'
    '场景只选scene_catalog已列id，邀请使用actor_id，检定使用character_id。'
    '时间用advance_hours/advance_days推进，平台自动处理自然恢复；'
    '不重复发同一时段recover。'
    '必须根据真实调查进度推进，不需要逐字复演书面时长。'
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
            if record['status'] in {'running', 'waiting'}:
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
            for item in self.store.list('invitation')
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
        context['actor_ids'] = [seat.actor_id for seat in game.seats]
        context['events'] = context['events'][-32:]
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
            context['scenario'].pop('source_blocks', None)
            context['scenario'].pop('directory', None)
            catalog = context['scenario'].get('scenes', [])
            context['scene_catalog'] = [
                {
                    'id': item['id'],
                    'title': item['title'],
                    'next_scene_ids': item.get('next_scene_ids', []),
                }
                for item in catalog
            ]
            context['scenario']['scenes'] = [
                item for item in catalog if item['id'] == game.scene_id
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
                            item[key] = item[key][:6000]
        if extra:
            context.update(extra)
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
                context=self.context(game, actor_id, purpose, extra),
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
        result = await self.platform.provider_client.generate(
            provider,
            instruction + '\n表演风格：' + actor.style,
            invitation.context,
            output_type,
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
        with self.store.transaction() as transaction:
            latest = transaction.get('game', game.id)
            latest['token_usage'] += result.usage
            transaction.put('game', game.id, latest)
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
                KeeperResponse
                if actor_id == game.keeper_actor_id
                else PlayerResponse
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
                    opening = await self._invoke(
                        game,
                        game.keeper_actor_id,
                        'keeper_opening',
                        KeeperResponse,
                        {
                            'instruction': (
                                '开场介绍现场，邀请调查员。'
                                '不要提前解决其行动。'
                            )
                        },
                    )
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
                        cycle.update(selected=[], eligible=[], groups=[])
                        work = await self._prepare_work(
                            game, opening, [], cycle
                        )
                        self.store.put('resolution_work', game.id, work)
                        return await self._finish_work(
                            game, work, is_automatic
                        )
                    with self.store.transaction() as transaction:
                        transaction.append_event(
                            game.id,
                            'narration',
                            {'text': opening.narration},
                            actor_ids=self._group_actor_ids(game),
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
                self.store.put('resolution_work', game.id, work)
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

    async def _prepare_work(self, game, reply, submitted, cycle):
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
        self._checkpoint(game, cards, work)
        for check_index, check in enumerate(reply.checks):
            if check_index < work['check_index']:
                continue
            if check.character_id not in cards:
                raise ValueError('检定角色不在游戏中')
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
                if check.skill in card.skills:
                    cards[card.id], _, _ = apply_command(
                        card, 'mark_skill', {'skill': check.skill}
                    )
            work['check_index'] = check_index + 1
            self._checkpoint(game, cards, work)
        for command_index, command in enumerate(reply.commands):
            if command_index < work['command_index']:
                continue
            await self._apply_command(game, cards, command, work)
            work['command_index'] = command_index + 1
            self._checkpoint(game, cards, work)
        work['is_prepared'] = True
        self._checkpoint(game, cards, work)
        return work

    def _checkpoint(self, game, cards, work):
        work['game'] = game.model_dump(mode='json')
        work['cards'] = {
            key: card.model_dump(mode='json') for key, card in cards.items()
        }
        self.store.put('resolution_work', game.id, work)

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
            card, rolls, details = apply_command(
                card,
                command.kind,
                command.parameters,
                day=game.scheduler.get('base_day', 0) + game.day,
            )
        cards[card.id] = card
        for roll in rolls:
            work['events'].append(
                {
                    'kind': 'check',
                    'data': {
                        **roll.model_dump(mode='json'),
                        'character_id': card.id,
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
        if work['has_rolls']:
            feedback = await self._invoke(
                original,
                original.keeper_actor_id,
                'keeper_feedback',
                KeeperResponse,
                {
                    'actual_rule_results': work['events'],
                    'updated_characters': work['cards'],
                    'pending_narration': reply.narration,
                    'already_applied_commands': [
                        command.model_dump(mode='json')
                        for command in reply.commands
                    ],
                    'instruction': (
                        '根据真实结果完成叙事和后果。已处理命令不要重复，'
                        '不再请求新检定；新检定留到下一节点。'
                        '重复裁定引用origin_command_id，不再执行；'
                        '只提出实际结果带来的新后果。'
                    ),
                },
            )
            if feedback.checks:
                raise ValueError(
                    '结果回填不能重复申请检定，请修正主持提示后重试'
                )
            already = {
                self._command_key(command) for command in reply.commands
            }
            command_ids = {command.id for command in reply.commands}
            for command in feedback.commands:
                key = self._command_key(command)
                if key in work.get('feedback_keys', []):
                    continue
                if (
                    key in already
                    or command.id in command_ids
                    or command.origin_command_id in command_ids
                ):
                    continue
                await self._apply_command(game, cards, command, work)
                already.add(key)
                command_ids.add(command.id)
                work.setdefault('feedback_keys', []).append(key)
                self._checkpoint(game, cards, work)
            merged = reply.model_dump(mode='json')
            for key in feedback.model_fields_set:
                if key not in {'commands', 'checks'}:
                    merged[key] = getattr(feedback, key)
            reply = KeeperResponse.model_validate(merged)
        group_scenes = game.scheduler.setdefault('group_scenes', {})
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
            new_clock = game.hour + hours
            elapsed_hours = math.floor(new_clock) - math.floor(game.hour)
            elapsed_days = math.floor(new_clock / 24)
            game.day += elapsed_days
            game.hour = new_clock % 24
            if elapsed_hours:
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
                        work['events'].append(
                            {
                                'kind': 'check',
                                'data': {
                                    **roll.model_dump(mode='json'),
                                    'character_id': character_id,
                                },
                                'is_private': False,
                            }
                        )
                    work['events'].append(
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
        game.scheduler = record_selection(
            game,
            work['cycle'].get('eligible', []),
            work['cycle'].get('selected', []),
            work['cycle'].get('groups', []),
        )
        recipients = self._group_actor_ids(game)
        if reply.recipient_actor_ids:
            if not set(reply.recipient_actor_ids).issubset(
                {seat.actor_id for seat in game.seats}
            ):
                raise ValueError('秘密消息接收者不在游戏中')
            recipients = reply.recipient_actor_ids
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
                        actor_ids=work.get(
                            'audience_actor_ids',
                            self._group_actor_ids(original),
                        ),
                    )
            for event in work['events']:
                transaction.append_event(
                    game.id,
                    event['kind'],
                    event['data'],
                    actor_ids=work.get(
                        'audience_actor_ids', self._group_actor_ids(original)
                    ),
                    is_private=event.get('is_private', False),
                )
            transaction.append_event(
                game.id,
                'narration',
                {'text': reply.narration},
                actor_ids=recipients,
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
                for record in transaction.list('invitation'):
                    if record['game_id'] == game.id and record['status'] in {
                        'pending',
                        'claimed',
                    }:
                        record['status'] = 'cancelled'
                        transaction.put('invitation', record['id'], record)
                transaction.delete('cycle', game.id)
                transaction.delete('resolution_work', game.id)
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
            for record in self.store.list('invitation'):
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
