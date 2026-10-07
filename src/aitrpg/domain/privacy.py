import re
from collections.abc import Iterable
from typing import Any

from aitrpg.domain.models import Scenario


def is_visible(
    item: Any,
    role_id: str | None = None,
    is_keeper: bool = False,
    revealed_ids: Iterable[str] | None = None,
) -> bool:
    if is_keeper:
        return True
    value = item if isinstance(item, dict) else item.model_dump()
    if value.get('id') in set(revealed_ids or ()):
        return True
    visibility = value.get('visibility', 'keeper')
    return visibility == 'public' or (
        visibility == 'roles' and role_id in value.get('role_ids', [])
    )


def scenario_for_viewer(
    scenario: Scenario,
    role_id: str | None = None,
    is_keeper: bool = False,
    revealed_ids: Iterable[str] | None = None,
    scene_id: str | None = None,
) -> dict[str, Any]:
    result = {
        'id': scenario.id,
        'version': scenario.version,
        'title': scenario.title,
        'description': scenario.description,
        'era': scenario.era,
        'ruleset': scenario.ruleset,
        'scenes': [],
        'roles': [],
    }
    known = set(revealed_ids or ())
    hidden_scenes = {
        scene.id
        for scene in scenario.scenes
        if not is_keeper
        and scene.conditions.get('participants')
        and role_id not in scene.conditions['participants']
    }
    for scene in scenario.scenes:
        if scene_id and scene.id != scene_id:
            continue
        participants = scene.conditions.get('participants', [])
        if not is_keeper and participants and role_id not in participants:
            continue
        item = {'id': scene.id, 'title': scene.title}
        item['public_text'] = scene.public_text
        if is_keeper:
            item.update(scene.model_dump())
        result['scenes'].append(item)
    for role in scenario.roles:
        item = {
            'id': role.id,
            'name': role.name,
            'public_text': role.public_text,
        }
        if is_keeper or role.id == role_id:
            item['secret_text'] = role.secret_text
            item['preset'] = role.preset
        result['roles'].append(item)
    for category in ('npcs', 'clues', 'handouts', 'endings'):
        visible = []
        for content in getattr(scenario, category):
            if not is_visible(content, role_id, is_keeper, revealed_ids):
                continue
            if content.id not in known and content.scene_ids:
                if set(content.scene_ids) <= hidden_scenes:
                    continue
            if scene_id and content.scene_ids:
                if scene_id not in content.scene_ids:
                    continue
            visible.append(
                {
                    'id': content.id,
                    'title': content.title,
                    'text': content.text,
                }
            )
        result[category] = visible
    result['assets'] = [
        {
            'id': asset.id,
            'name': asset.name,
            'mime_type': asset.mime_type,
            'caption': asset.caption,
            'is_map': asset.is_map,
            'nodes': [
                node
                for node in asset.nodes
                if is_visible(node, role_id, is_keeper, revealed_ids)
            ],
            'regions': asset.regions if is_keeper else [],
        }
        for asset in scenario.assets
        if is_visible(asset, role_id, is_keeper, revealed_ids)
        and (
            asset.id in known
            or not asset.scene_ids
            or not set(asset.scene_ids) <= hidden_scenes
        )
        and (
            not scene_id or not asset.scene_ids or scene_id in asset.scene_ids
        )
    ]
    if is_keeper:
        result['custom_rules'] = scenario.custom_rules
        result['source_index'] = [
            {
                'id': block.id,
                'file': block.file,
                'locator': block.locator,
                'kind': block.kind,
            }
            for block in scenario.source_blocks
            if block.kind not in {'character_cell', 'formula'}
        ]
    return result


def retrieve_sources(
    scenario: Scenario,
    query: str,
    *,
    source_ids: Iterable[str] | None = None,
    is_keeper: bool = False,
    limit: int = 8,
    max_chars: int = 16000,
) -> list[dict[str, Any]]:
    # 原文的同一段可能混有剧透，不向玩家开放原始索引。
    if not is_keeper:
        return []
    requested = set(source_ids or ())
    words = set(
        re.findall(r'[a-zA-Z0-9_]+|[\u4e00-\u9fff]{2,}', query.lower())
    )
    for word in tuple(words):
        if len(word) > 3 and not word.isascii():
            words.update(
                word[index : index + 2] for index in range(len(word) - 1)
            )
    ranked = []
    for index, block in enumerate(scenario.source_blocks):
        if requested and block.id not in requested:
            continue
        text = block.text.lower()
        score = sum(text.count(word) for word in words)
        if requested or score or not words:
            ranked.append((-score, index, block))
    ranked.sort(key=lambda item: (item[0], item[1]))
    result = []
    remaining = max(0, min(max_chars, 64000))
    for _, _, block in ranked[: max(0, min(limit, 32))]:
        if not remaining:
            break
        value = block.model_dump()
        value['text'] = block.text[:remaining]
        value['is_truncated'] = len(block.text) > remaining
        remaining -= len(value['text'])
        result.append(value)
    return result
