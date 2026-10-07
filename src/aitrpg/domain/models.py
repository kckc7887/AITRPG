from datetime import UTC
from datetime import datetime
from typing import Any
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator


def new_id() -> str:
    return uuid4().hex


def utc_now() -> datetime:
    return datetime.now(UTC)


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Entity(Model):
    id: str = Field(default_factory=new_id)
    version: int = 1
    created_at: datetime = Field(default_factory=utc_now)


class Provider(Entity):
    name: str
    protocol: Literal['openai', 'anthropic'] = 'openai'
    base_url: str
    model: str = 'deepseek-flash'
    key_env: str = 'DEEPSEEK_API_KEY'
    keyring_id: str | None = None
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_output_tokens: int = Field(default=4096, ge=128, le=65536)
    timeout_seconds: float = Field(default=120, ge=5, le=600)
    is_enabled: bool = True
    is_json_mode: bool = True
    is_tool_mode: bool = False
    is_vision: bool = False
    thinking_mode: Literal['default', 'enabled', 'disabled'] = 'default'


class Actor(Entity):
    name: str
    connection: Literal['api', 'mcp'] = 'api'
    provider_id: str | None = None
    style: str = ''


class Roll(Model):
    id: str = Field(default_factory=new_id)
    expression: str
    dice: list[int] = Field(default_factory=list)
    total: int
    reason: str = ''
    details: dict[str, Any] = Field(default_factory=dict)


class Character(Entity):
    actor_id: str
    name: str
    occupation: str = ''
    occupation_definition: dict[str, Any] = Field(default_factory=dict)
    age: int = Field(default=30, ge=1, le=120)
    sex: str = ''
    era: str = '1920s'
    residence: str = ''
    birthplace: str = ''
    attributes: dict[str, int]
    skills: dict[str, int] = Field(default_factory=dict)
    allocations: dict[str, Any] = Field(default_factory=dict)
    skill_marks: list[str] = Field(default_factory=list)
    luck: int = 50
    max_hp: int = Field(ge=1)
    current_hp: int
    max_mp: int = Field(ge=0)
    current_mp: int
    max_san: int = Field(default=99, ge=0)
    current_san: int
    movement: int = 8
    damage_bonus: str = '0'
    build: int = 0
    conditions: list[str] = Field(default_factory=list)
    inventory: list[dict[str, Any]] = Field(default_factory=list)
    weapons: list[dict[str, Any]] = Field(default_factory=list)
    assets: dict[str, Any] = Field(default_factory=dict)
    background: dict[str, str] = Field(default_factory=dict)
    experiences: list[str] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)
    creation_rolls: list[Roll] = Field(default_factory=list)
    source: str = ''
    import_warnings: list[str] = Field(default_factory=list)
    locked_game_id: str | None = None

    @field_validator('attributes')
    @classmethod
    def validate_attributes(cls, value: dict[str, int]) -> dict[str, int]:
        required = {'STR', 'CON', 'SIZ', 'DEX', 'APP', 'INT', 'POW', 'EDU'}
        if set(value) != required or any(
            number < 1 for number in value.values()
        ):
            raise ValueError('角色必须包含八项正整数属性')
        return value


class SourceBlock(Model):
    id: str = Field(default_factory=new_id)
    file: str
    locator: str
    text: str
    kind: str = 'text'


class ReviewIssue(Model):
    id: str = Field(default_factory=new_id)
    message: str
    severity: Literal['warning', 'blocker'] = 'warning'
    source_ids: list[str] = Field(default_factory=list)
    is_resolved: bool = False


class ScenarioScene(Model):
    id: str
    title: str
    public_text: str = ''
    keeper_text: str = ''
    source_ids: list[str] = Field(default_factory=list)
    next_scene_ids: list[str] = Field(default_factory=list)
    conditions: dict[str, Any] = Field(default_factory=dict)


class ScenarioRole(Model):
    id: str
    name: str
    public_text: str = ''
    secret_text: str = ''
    preset: dict[str, Any] = Field(default_factory=dict)
    source_ids: list[str] = Field(default_factory=list)


class ScenarioContent(Model):
    id: str
    title: str
    text: str = ''
    visibility: Literal['public', 'keeper', 'roles'] = 'keeper'
    role_ids: list[str] = Field(default_factory=list)
    scene_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class ScenarioAsset(Model):
    id: str = Field(default_factory=new_id)
    name: str
    path: str
    mime_type: str
    visibility: Literal['public', 'keeper', 'roles'] = 'keeper'
    role_ids: list[str] = Field(default_factory=list)
    scene_ids: list[str] = Field(default_factory=list)
    caption: str = ''
    is_map: bool = False
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    regions: list[dict[str, Any]] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class Scenario(Entity):
    title: str
    description: str = ''
    era: str = '1920s'
    ruleset: str = 'coc7'
    min_players: int = Field(default=3, ge=1)
    max_players: int = Field(default=4, ge=1)
    status: Literal['draft', 'approved'] = 'draft'
    author: str = ''
    rights: str = ''
    source: str = ''
    scenes: list[ScenarioScene] = Field(default_factory=list)
    roles: list[ScenarioRole] = Field(default_factory=list)
    npcs: list[ScenarioContent] = Field(default_factory=list)
    clues: list[ScenarioContent] = Field(default_factory=list)
    handouts: list[ScenarioContent] = Field(default_factory=list)
    endings: list[ScenarioContent] = Field(default_factory=list)
    assets: list[ScenarioAsset] = Field(default_factory=list)
    source_blocks: list[SourceBlock] = Field(default_factory=list)
    review_issues: list[ReviewIssue] = Field(default_factory=list)
    custom_rules: list[str] = Field(default_factory=list)
    directory: str = ''


class Seat(Model):
    actor_id: str
    character_id: str
    role_id: str | None = None
    group_id: str = 'main'


class Game(Entity):
    name: str
    scenario_id: str
    scenario_version: int
    keeper_actor_id: str
    seats: list[Seat]
    status: Literal['paused', 'running', 'waiting', 'ended', 'error'] = (
        'paused'
    )
    scene_id: str = ''
    group_id: str = 'main'
    mode: Literal['exploration', 'combat'] = 'exploration'
    revision: int = 0
    node_count: int = 0
    token_usage: int = 0
    budget_nodes: int = Field(default=1000, ge=1)
    budget_tokens: int = Field(default=10000000, ge=1000)
    day: int = 0
    hour: float = Field(default=8, ge=0, lt=24)
    flags: dict[str, Any] = Field(default_factory=dict)
    knowledge: dict[str, list[str]] = Field(default_factory=dict)
    revealed_assets: dict[str, list[str]] = Field(default_factory=dict)
    scheduler: dict[str, Any] = Field(default_factory=dict)
    combat: dict[str, Any] = Field(default_factory=dict)
    next_actor_ids: list[str] = Field(default_factory=list)
    is_all_players: bool = False
    ending: str = ''
    last_error: str = ''
    last_keeper: dict[str, Any] = Field(default_factory=dict)


class Invitation(Entity):
    game_id: str
    actor_id: str
    character_id: str | None = None
    revision: int
    purpose: Literal[
        'player',
        'keeper_opening',
        'keeper_resolution',
        'keeper_feedback',
        'keeper_control',
        'keeper_repair',
        'reaction',
    ]
    scene_id: str
    status: Literal[
        'pending', 'claimed', 'submitted', 'expired', 'cancelled'
    ] = 'pending'
    expires_at: datetime
    context: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] | None = None
    submission_id: str | None = None
    response_fields: list[str] = Field(default_factory=list)


class PlayerResponse(Model):
    speech: str = Field(default='', max_length=4000)
    intent: str = Field(default='', max_length=4000)
    intent_visibility: Literal['keeper', 'public'] = 'keeper'
    is_pass: bool = False
    defense: Literal['dodge', 'fight_back', 'cover'] | None = None
    defense_weapon_index: int | None = Field(default=None, ge=0)


class KeeperControl(Model):
    clock_status: Literal[
        'unchanged', 'supported', 'estimated', 'needs_review'
    ]
    scene_status: Literal['unchanged', 'supported', 'needs_review']
    target_day: int | None = Field(default=None, ge=0)
    target_hour: float | None = Field(default=None, ge=0, lt=24)
    scene_id: str = ''
    clock_quote: str = ''
    scene_quote: str = ''
    reason: str = ''


class CheckRequest(Model):
    character_id: str
    skill: str
    difficulty: Literal['regular', 'hard', 'extreme'] = 'regular'
    bonus_dice: int = Field(default=0, ge=-2, le=2)
    reason: str = ''
    is_private: bool = False
    is_pushed: bool = False
    pushed_from_id: str | None = None
    opponent_character_id: str | None = None
    opponent_skill: str = ''


class RuleCommand(Model):
    id: str = Field(default_factory=new_id)
    origin_command_id: str | None = None
    kind: str
    character_id: str = ''
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason: str
    is_private: bool = False


class RulingRepair(Model):
    commands: list[RuleCommand] = Field(default_factory=list)
    checks: list[CheckRequest] = Field(default_factory=list)
    narration: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)
    private_messages: dict[str, str] = Field(default_factory=dict)
    improvisation: str = ''
    flags: dict[str, Any] = Field(default_factory=dict)


class KeeperResponse(Model):
    narration: str = Field(default='', max_length=12000)
    invite_actor_ids: list[str] = Field(default_factory=list)
    is_all_players: bool = False
    checks: list[CheckRequest] = Field(default_factory=list)
    commands: list[RuleCommand] = Field(default_factory=list)
    scene_id: str = ''
    group_id: str = ''
    reveal_clue_ids: list[str] = Field(default_factory=list)
    reveal_asset_ids: list[str] = Field(default_factory=list)
    recipient_actor_ids: list[str] = Field(default_factory=list)
    private_messages: dict[str, str] = Field(default_factory=dict)
    flags: dict[str, Any] = Field(default_factory=dict)
    advance_days: int = Field(default=0, ge=0, le=365)
    advance_hours: float = Field(default=0, ge=0, le=8760)
    is_finished: bool = False
    ending: str = ''
    improvisation: str = ''
