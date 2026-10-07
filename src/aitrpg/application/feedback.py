from aitrpg.application.ruling_repair import repair_check
from aitrpg.application.ruling_repair import repair_command
from aitrpg.domain.models import KeeperResponse
from aitrpg.domain.models import RuleCommand


async def finish_feedback(service, original, game, cards, reply, work):
    if work.get('feedback_reply'):
        reply = KeeperResponse.model_validate(work['feedback_reply'])
    if work.get('feedback_complete'):
        return reply
    executed = [
        RuleCommand.model_validate(command)
        for command in work.get(
            'executed_commands',
            [command.model_dump(mode='json') for command in reply.commands],
        )
    ]
    for stage in range(work.get('feedback_stage', 0), 8):
        extra = {
            'actual_rule_results': work['events'],
            'updated_characters': work['cards'],
            'pending_narration': reply.narration,
            'combat_turn': service._combat_turn(game),
            'already_applied_commands': [
                command.model_dump(mode='json') for command in executed
            ],
            'instruction': (
                '根据真实结果回填叙事，已处理命令不能再执行。'
                '被拒的旧攻击没有排队。当前攻击要有combat_attack。'
                '仅允许实际新发现直接触发的必要后续检定（如看清人脸'
                '才做SAN），禁止重复已有相同检定或另起调查行动。'
                'sanity命令引用已有SAN的sanity_check_id，不能重骰。'
                '原叙事没填的控制字段仍保留，只填写需改变的字段。'
            ),
        }
        if stage:
            extra['reaction_key'] = f'results-{stage}'
        feedback = await service._invoke(
            original,
            original.keeper_actor_id,
            'keeper_feedback',
            KeeperResponse,
            extra,
        )
        feedback = service._normalise_control(feedback)
        progress = work.setdefault('feedback_progress', {}).setdefault(
            str(stage), {'check': 0, 'command': 0}
        )
        before = len(work['events'])
        for index, check in enumerate(feedback.checks):
            if index < progress['check']:
                continue
            if service._is_repeated_check(check, work):
                raise ValueError('结果回填不能重复申请检定')
            count = len(work['events'])
            try:
                service._apply_check(game, cards, check, work)
            except ValueError as error:
                if len(work['events']) != count:
                    raise
                repaired = await repair_check(
                    service,
                    game,
                    cards,
                    check,
                    f'feedback-{stage}-{index}',
                    work,
                    error,
                )
                _merge_repair(feedback, repaired)
                executed.extend(repaired.commands)
            progress['check'] = index + 1
            progress['had_new_rolls'] = True
            service._checkpoint(game, cards, work)
        keys = {service._command_key(command) for command in executed}
        identifiers = {command.id for command in executed}
        for index, command in enumerate(feedback.commands):
            if index < progress['command']:
                continue
            key = service._command_key(command)
            if not (
                key in keys
                or key in work.get('feedback_keys', [])
                or command.id in identifiers
                or command.origin_command_id in identifiers
            ):
                count = len(work['events'])
                try:
                    await service._apply_command(game, cards, command, work)
                except ValueError as error:
                    if len(work['events']) != count:
                        raise
                    repaired = await repair_command(
                        service, game, cards, command, work, error
                    )
                    _merge_repair(feedback, repaired)
                    executed.extend(repaired.commands)
                else:
                    executed.append(command)
                keys.add(key)
                identifiers.add(command.id)
                work.setdefault('feedback_keys', []).append(key)
                if any(
                    event['kind'] == 'check'
                    for event in work['events'][count:]
                ):
                    progress['had_new_rolls'] = True
            progress['command'] = index + 1
            work['executed_commands'] = [
                command.model_dump(mode='json') for command in executed
            ]
            service._checkpoint(game, cards, work)
        merged = reply.model_dump(mode='json')
        for key in feedback.model_fields_set:
            if key not in {'commands', 'checks'}:
                merged[key] = getattr(feedback, key)
        reply = KeeperResponse.model_validate(merged)
        work['feedback_reply'] = reply.model_dump(mode='json')
        work['feedback_stage'] = stage + 1
        had_new_rolls = any(
            event['kind'] == 'check' for event in work['events'][before:]
        )
        had_new_rolls = had_new_rolls or progress.get('had_new_rolls', False)
        if not had_new_rolls:
            work['feedback_complete'] = True
        service._checkpoint(game, cards, work)
        if work.get('feedback_complete'):
            return reply
    raise ValueError('本节点结果依赖过长，需要主持收束后再继续')


def _merge_repair(feedback, repair):
    feedback.narration = repair.narration
    feedback.private_messages = repair.private_messages
    feedback.improvisation = repair.improvisation
    feedback.flags = repair.flags
