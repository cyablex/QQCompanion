"""Assess one optional contact, distinguishing a quiet choice from a failed attempt."""

import random
from datetime import datetime

from policy import day_start
from song_offers import prepare as prepare_offer
from store import TZ


def outcome(engine, cfg, contact, kind, now, state, error=''):
    key = f"outreach_result:{cfg['bot_qq']}:{contact}"
    previous = engine.store.get(key, {})
    failures = previous.get('failures', 0) + 1 if state == 'failed' else 0
    if state == 'sent':
        minutes = 90 if kind == 'proactive' else cfg.get('friend_proactive_interval_hours', 1.5) * 60
    elif state == 'failed':
        minutes = 5 if failures == 1 else 15 if failures == 2 else 120
    elif state == 'skipped':
        minutes = random.randint(45, 90) if kind == 'proactive' else random.randint(90, 180)
    else:
        minutes = 30
    due = now + minutes * 60
    engine.store.set('owner_proactive_next' if kind == 'proactive' else 'friend_proactive_next:' + contact, due)
    engine.store.set(key, dict(state=state, at=now, next=due, failures=failures, error=error))
    if state == 'failed' and failures < 3:
        scheduler = f"outreach_due:{cfg['bot_qq']}:{'owner' if kind == 'proactive' else 'friends'}"
        engine.store.set(scheduler, min(engine.store.get(scheduler, due), due))


async def assess(engine, contact, cfg, kind, instruction, now):
    assessment = 'owner_proactive_assessment' if kind == 'proactive' else 'friend_proactive_assessment:' + contact
    engine.store.set(assessment, dict(at=now, incoming=engine.store.last_incoming(contact)))
    try:
        result = await engine.decision(contact, kind, instruction)
        if not result:
            outcome(engine, cfg, contact, kind, now, 'cancelled')
            return False
        if result.get('decision_error'):
            outcome(engine, cfg, contact, kind, now, 'failed', result['decision_error'])
            return False
        if result.get('action') != 'reply' or not any(result.get('messages', [])):
            outcome(engine, cfg, contact, kind, now, 'skipped')
            return False
        if kind == 'friend_proactive':
            result = dict(result, messages=[' '.join(result['messages'])[:300]])
        result, _offer = prepare_offer(engine, contact, cfg, result, now)
        if not result:
            outcome(engine, cfg, contact, kind, now, 'skipped')
            return False
        result = await engine.prepare_proactive_drawing(contact, result, cfg)
        if not result:
            outcome(engine, cfg, contact, kind, now, 'failed', 'drawing_unavailable')
            return False
        fresh = engine.config_reader()
        if not fresh.get('proactive') or fresh['revision'] != cfg['revision']:
            outcome(engine, cfg, contact, kind, now, 'cancelled')
            return False
        delivery_key = None
        if kind == 'friend_proactive':
            moment = datetime.fromtimestamp(now, TZ)
            slot = engine.store.action_count(kind, day_start(moment), contact) + 1
            delivery_key = f"friend_proactive:{cfg['bot_qq']}:{contact}:{moment.strftime('%Y-%m-%d')}:{slot}"
        sent = await engine.deliver(contact, kind, result, key=delivery_key)
        outcome(engine, cfg, contact, kind, now, 'sent' if sent else 'cancelled')
        return sent
    except Exception as exc:
        outcome(engine, cfg, contact, kind, now, 'failed', type(exc).__name__)
        raise
