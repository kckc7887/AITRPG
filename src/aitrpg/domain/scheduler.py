import random
from copy import deepcopy

from aitrpg.domain.models import Game


def select_group(game: Game, groups: list[str]) -> str:
    if not groups:
        return game.group_id
    current = game.group_id if game.group_id in groups else groups[0]
    waits = game.scheduler.get('group_waits', {})
    if game.scheduler.get('group_nodes', 0) < 3 or len(groups) == 1:
        return current
    candidates = [group for group in groups if group != current]
    return max(candidates, key=lambda group: waits.get(group, 0))


def select_actors(
    game: Game,
    eligible: list[str],
    preferred: list[str] | None = None,
    *,
    is_all_players: bool = False,
    rng=None,
) -> list[str]:
    generator = rng or random.SystemRandom()
    eligible = list(dict.fromkeys(eligible))
    if not eligible or is_all_players:
        return eligible
    preferred = [actor for actor in (preferred or []) if actor in eligible]
    waits = game.scheduler.get('waits', {})
    overdue = sorted(
        [
            actor
            for actor in eligible
            if waits.get(actor, 0) >= 2 * len(eligible)
        ],
        key=lambda actor: waits.get(actor, 0),
        reverse=True,
    )
    maximum = min(3, len(eligible))
    if len(preferred) == 1 and not overdue:
        count = 1
    else:
        count = generator.choices(
            list(range(1, maximum + 1)), weights=[4, 4, 2][:maximum]
        )[0]
    count = max(count, min(len(overdue), maximum))
    chosen = overdue[:count]
    remaining = [actor for actor in eligible if actor not in chosen]
    last = game.scheduler.get('last_actor')
    consecutive = game.scheduler.get('consecutive', 0)
    if consecutive >= 2 and last in remaining and len(remaining) > 1:
        remaining.remove(last)
    while remaining and len(chosen) < count:
        weights = [
            (1 + waits.get(actor, 0)) * (3 if actor in preferred else 1)
            for actor in remaining
        ]
        actor = generator.choices(remaining, weights=weights)[0]
        chosen.append(actor)
        remaining.remove(actor)
    return chosen


def record_selection(
    game: Game, eligible: list[str], chosen: list[str], groups: list[str]
) -> dict:
    state = deepcopy(game.scheduler)
    waits = state.setdefault('waits', {})
    counts = state.setdefault('counts', {})
    for actor in eligible:
        waits[actor] = 0 if actor in chosen else waits.get(actor, 0) + 1
    for actor in chosen:
        counts[actor] = counts.get(actor, 0) + 1
        if state.get('last_actor') == actor:
            state['consecutive'] = state.get('consecutive', 0) + 1
        else:
            state['last_actor'] = actor
            state['consecutive'] = 1
    group_waits = state.setdefault('group_waits', {})
    for group in groups:
        group_waits[group] = (
            0 if group == game.group_id else group_waits.get(group, 0) + 1
        )
    previous = state.get('last_group')
    state['group_nodes'] = (
        state.get('group_nodes', 0) + 1 if previous == game.group_id else 1
    )
    state['last_group'] = game.group_id
    return state
