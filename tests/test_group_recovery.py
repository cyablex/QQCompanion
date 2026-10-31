"""Recent @ messages survive transient generation failures without blind sends."""
import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from test_qq_faces_groups import groups as groups  # noqa: E402,F401
from test_relay_fallback import setup as setup  # noqa: E402,F401
from engine import Engine  # noqa: E402
from store import BudgetExceeded  # noqa: E402
from relay_fallback import transport_diagnostic  # noqa: E402

APIConnectionError=type('APIConnectionError',(Exception,),{})


@pytest.mark.asyncio
@pytest.mark.parametrize('error_name',['APIConnectionError','APITimeoutError'])
async def test_provider_connection_failure_uses_official_once_and_records_safe_cause(setup,error_name):
    cfg,store=setup
    calls=[]
    async def generate(*args,**kwargs):
        calls.append(kwargs.get('official_fallback',False))
        if not calls[-1]:
            exc=type(error_name,(Exception,),{})('private-key-and-prompt')
            exc.__cause__=ConnectionError('private-url')
            raise exc
        return '完成',dict(input_other=2,input_cached=0,output=2)
    e=Engine(store,generate,None,lambda:cfg)
    assert await e.generate_aux('memory','s',[])=='完成'
    assert calls==[False,True]
    assert store.get('official_fallback_stats')['reason']=='connection'
    diagnostic=store.get('model_transport_error')
    assert diagnostic['chain']==[error_name,'ConnectionError']
    assert 'private' not in json.dumps(diagnostic)
    assert len(store.db.execute('SELECT * FROM calls').fetchall())==2


@pytest.mark.asyncio
async def test_network_fallback_still_respects_budget(setup):
    cfg,store=setup
    cfg['daily_cny']=.05
    calls=[]
    async def generate(*args,**kwargs):
        calls.append(True)
        raise APIConnectionError()
    with pytest.raises(BudgetExceeded):
        await Engine(store,generate,None,lambda:cfg).generate_aux('memory','s',[])
    assert calls==[True]


@pytest.mark.asyncio
async def test_official_network_failure_is_not_recursive(setup):
    cfg,store=setup
    calls=[]
    async def generate(*args,**kwargs):
        calls.append(kwargs.get('official_fallback',False))
        raise APIConnectionError()
    with pytest.raises(APIConnectionError):
        await Engine(store,generate,None,lambda:cfg).generate_aux('memory','s',[])
    assert calls==[False,True]


@pytest.fixture
def clock(monkeypatch):
    moment=[time.time()]
    real_sleep=asyncio.sleep
    async def sleep(seconds):
        moment[0]+=seconds
        await real_sleep(0)
    monkeypatch.setattr('group_chat.time.time',lambda:moment[0])
    monkeypatch.setattr('group_chat.asyncio.sleep',sleep)
    return moment


async def mention(chats,ident=1,sender=33333):
    await chats.receive(dict(group_id=900000003,user_id=sender,message_id=ident,
        message=[dict(type='at',data={'qq':'22222'}),dict(type='text',data={'text':'你好'})]))
    await asyncio.gather(*list(chats.tasks.values()))


@pytest.mark.asyncio
async def test_failed_mention_is_persistent_and_resumes_without_another_at(groups,clock):
    chats,store,cfg,calls,sent=groups
    actual=chats.engine.generate
    attempts=[]
    async def generate(*args,**kwargs):
        attempts.append(True)
        if len(attempts)==1:
            raise APIConnectionError()
        return await actual(*args,**kwargs)
    chats.engine.generate=generate
    await mention(chats)
    job=store.get('reply_job:group:900000003')
    assert job and job['attempts']==1 and not sent
    # A normal picture after the failed @ must not consume its recovery job.
    await chats.receive(dict(group_id=900000003,user_id=44444,message_id=2,
        message=[dict(type='image',data={'file':'unread'})]))
    await asyncio.gather(*list(chats.tasks.values()))
    assert store.get('reply_job:group:900000003') and not sent
    clock[0]=job['due']+1
    chats.resume_pending()
    await asyncio.gather(*list(chats.tasks.values()))
    assert len(sent)==1 and not store.get('reply_job:group:900000003')
    chats.resume_pending()
    assert not chats.tasks


@pytest.mark.asyncio
@pytest.mark.parametrize('cause',['expire','identity','forget','disabled','unknown'])
async def test_invalid_or_uncertain_group_jobs_do_not_repeat(groups,clock,cause):
    chats,store,cfg,calls,sent=groups
    async def fail(*args,**kwargs):raise APIConnectionError()
    chats.engine.generate=fail
    await mention(chats)
    job=store.get('reply_job:group:900000003')
    if cause=='expire':
        clock[0]=job['expires']+1
    elif cause=='identity':
        cfg['bot_qq']='99999'
    elif cause=='forget':
        store.forget('group:900000003')
    elif cause=='disabled':
        cfg['group_enabled']=False
    else:
        store.action('uncertain','group:900000003','reply',['maybe sent'])
        store.outcome('uncertain','unknown')
    clock[0]=max(clock[0],job['due']+1)
    chats.resume_pending()
    await asyncio.gather(*list(chats.tasks.values()))
    assert not sent and not store.get('reply_job:group:900000003')


@pytest.mark.asyncio
async def test_three_failed_group_attempts_are_bounded(groups,clock):
    chats,store,cfg,calls,sent=groups
    attempted=[]
    async def fail(*args,**kwargs):
        attempted.append(True)
        raise APIConnectionError()
    chats.engine.generate=fail
    await mention(chats)
    for _ in range(3):
        job=store.get('reply_job:group:900000003')
        if job:
            clock[0]=job['due']+1
        chats.resume_pending()
        await asyncio.gather(*list(chats.tasks.values()))
    assert len(attempted)==3 and not sent and not store.get('reply_job:group:900000003')


@pytest.mark.asyncio
async def test_new_mention_replaces_old_failed_source(groups,clock):
    chats,store,cfg,calls,sent=groups
    actual=chats.engine.generate
    async def fail(*args,**kwargs):raise APIConnectionError()
    chats.engine.generate=fail
    await mention(chats)
    chats.engine.generate=actual
    await mention(chats,ident=2,sender=44444)
    assert len(sent)==1
    origin=store.get('song_origin:group:900000003')
    assert origin['requester']=='44444' and origin['event_id'].endswith(':2')


def test_exception_cycle_is_bounded_without_error_text():
    e=APIConnectionError('private')
    e.__cause__=e
    assert transport_diagnostic(e)==['APIConnectionError']


@pytest.mark.asyncio
async def test_queue_recovery_is_independent_of_autonomous_scheduler(groups,clock,monkeypatch):
    sys.path.insert(0,str(ROOT/'astrbot'))
    monkeypatch.setenv('QQCOMPANION_ROOT',str(ROOT))
    spec=importlib.util.spec_from_file_location('companion_recovery_test',ROOT/'plugins/astrbot_plugin_qq_companion/main.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    started=[]
    async def check():started.append('transport')
    def resume():started.append('resume')
    fake=SimpleNamespace(bot=object(),reply_queue=SimpleNamespace(ready=lambda:['group:900000003']),check_messaging=check,resume_replies=resume,store=groups[1])
    monkeypatch.setattr(module,'read_settings',lambda:groups[2])
    async def stop(_):raise asyncio.CancelledError()
    monkeypatch.setattr(module.asyncio,'sleep',stop)
    with pytest.raises(asyncio.CancelledError):
        await module.QQCompanion.recover_replies(fake)
    assert started==['transport','resume']


@pytest.mark.asyncio
async def test_recent_unsent_group_job_survives_worker_restart(groups,clock):
    from group_chat import GroupChats
    chats,store,cfg,calls,sent=groups
    async def fail(*args,**kwargs):
        raise APIConnectionError()
    chats.engine.generate=fail
    await mention(chats)
    job=store.get('reply_job:group:900000003')
    restored=GroupChats(chats.plugin)
    restored.engine.config_reader=lambda:cfg
    clock[0]=job['due']+1
    restored.resume_pending()
    await asyncio.gather(*list(restored.tasks.values()))
    assert len(sent)==1 and not store.get('reply_job:group:900000003')


@pytest.mark.asyncio
async def test_new_picture_during_failed_generation_keeps_the_at_request(groups,clock):
    chats,store,cfg,calls,sent=groups
    cfg.update(model_route='relay',relay_connection={'pricing_cny':{'input_other':.6,'input_cached':.012,'output':2.4}})
    original=chats.engine.generate
    attempted=[]
    async def generate(*args,**kwargs):
        attempted.append(True)
        if len(attempted)==1:
            store.incoming('photo','group:900000003','[群聊附件暂未解析]')
            raise APIConnectionError()
        return await original(*args,**kwargs)
    chats.engine.generate=generate
    await mention(chats)
    assert len(sent)==1 and len(attempted)==2
    assert not store.get('reply_job:group:900000003')


@pytest.mark.asyncio
async def test_new_group_message_cannot_unlock_an_uncertain_original_send(groups,clock):
    chats,store,cfg,calls,sent=groups
    attempted=[]
    async def action(*args,**kwargs):
        attempted.append(True)
        clock[0]+=1
        store.incoming('photo','group:900000003','[群聊附件暂未解析]')
        raise RuntimeError('no receipt')
    chats.plugin.bot.call_action=action
    await mention(chats)
    assert not store.has_unknown('group:900000003')  # New input normally unlocks chat.
    assert attempted==[True] and not store.get('reply_job:group:900000003')
    clock[0]+=65
    chats.resume_pending()
    assert not chats.tasks
