"""P9：权限来源、有效期与逐条撤销的结构化契约。"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.config import Config
from app.runtime.permission_view import PermissionGrantNotFound, permission_overview, revoke_grant
from app.safety.policy import ApprovalGate
from tests.test_stdio import Harness, agent_for


def _session_key(root: str, tool: str, target: str) -> tuple[str, str, str]:
    return (root, tool, target)


def test_gate_lists_stable_grants_and_revokes_each_scope(tmp_path):
    root = str(tmp_path.resolve())
    other = str((tmp_path / 'other').resolve())
    gate = ApprovalGate()
    gate.workspace_allowed.update({root, other})
    bash_target = json.dumps({'command': 'npm test', 'cwd': root}, ensure_ascii=False, sort_keys=True)
    gate.session_allowed.add(_session_key(root, 'bash', bash_target))
    gate.session_allowed.add(_session_key(root, 'write_file', str(tmp_path / 'a.txt')))

    grants = gate.grants(tmp_path)
    assert [grant['kind'] for grant in grants] == ['workspace', 'session', 'session']
    assert grants[0]['scope'] == 'workspace' and grants[0]['id'].startswith('ws-')
    assert all(grant['validity'] == 'process' and grant['revocable'] is True for grant in grants)
    assert grants[1]['tool'] == 'bash' and 'npm test' in grants[1]['target']
    assert grants[2]['tool'] == 'write_file' and grants[2]['target'] == 'a.txt'
    assert [grant['id'] for grant in gate.grants(tmp_path)] == [grant['id'] for grant in grants]
    assert all('other' not in grant['workspace'] for grant in grants)

    revoked = gate.revoke_grant(tmp_path, grants[0]['id'])
    assert revoked is not None and revoked['kind'] == 'workspace'
    assert root not in gate.workspace_allowed and other in gate.workspace_allowed
    assert gate.permissions(tmp_path)['session_rules'] == 2
    assert gate.revoke_grant(tmp_path, grants[0]['id']) is None

    revoked = gate.revoke_grant(tmp_path, grants[1]['id'])
    assert revoked is not None and revoked['tool'] == 'bash'
    assert gate.permissions(tmp_path)['session_rules'] == 1
    assert gate.revoke_grant(tmp_path, 'ss-' + '0' * 12) is None


def test_permission_overview_reports_sources_validity_and_policy_error(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_PERMISSIONS', '')
    project = tmp_path / '.vibe' / 'permissions.json'
    project.parent.mkdir(parents=True)
    project.write_text(json.dumps({
        'version': 1,
        'rules': [
            {'tool': 'bash', 'command_prefix': 'rm', 'decision': 'once', 'note': '删除逐次确认'},
            {'tool': 'mcp_*', 'scopes': ['once']},
        ],
    }, ensure_ascii=False), encoding='utf-8')
    agent = agent_for(tmp_path, [])
    agent._reload_permission_policy()
    root = str(tmp_path.resolve())
    agent.approval.workspace_allowed.add(root)

    overview = permission_overview(agent, tmp_path)
    assert overview['autonomy'] == {'level': 'ask', 'label': '每次询问', 'source': 'default', 'requested': 'ask', 'cap': None}
    assert [grant['kind'] for grant in overview['grants']] == ['workspace']
    assert len(overview['persistent']) == 2
    assert {entry['source'] for entry in overview['persistent']} == {'project'}
    assert all(entry['path'] == str(project) for entry in overview['persistent'])
    assert all(entry['validity'] == 'file' and entry['revocable'] is False for entry in overview['persistent'])
    assert any('- [project] bash prefix=rm → once' in entry['rule'] for entry in overview['persistent'])
    assert overview['error'] is None

    agent._cli_autonomy = 'edits'
    agent._reload_permission_policy()
    changed = permission_overview(agent, tmp_path)
    assert changed['autonomy']['source'] == 'cli'
    assert changed['autonomy']['requested'] == 'edits' and changed['autonomy']['level'] == 'edits'

    project.write_text(json.dumps({
        'version': 1, 'autonomy': 'ask',
        'rules': [{'tool': 'bash', 'command_prefix': 'rm', 'decision': 'once'}],
    }, ensure_ascii=False), encoding='utf-8')
    agent._reload_permission_policy()
    capped = permission_overview(agent, tmp_path)
    assert capped['autonomy']['cap'] == 'ask' and capped['autonomy']['level'] == 'ask'
    assert capped['autonomy']['requested'] == 'edits'

    grant_id = overview['grants'][0]['id']
    revoked = revoke_grant(agent, tmp_path, grant_id)
    assert revoked['revoked']['kind'] == 'workspace' and revoked['revoked']['id'] == grant_id
    assert revoked['grants'] == []
    with pytest.raises(PermissionGrantNotFound):
        revoke_grant(agent, tmp_path, grant_id)

    project.write_text('{broken', encoding='utf-8')
    agent._reload_permission_policy()
    broken = permission_overview(agent, tmp_path)
    assert 'project 策略' in str(broken['error'])


def test_stdio_serves_permission_overview_and_revokes_by_grant_id(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_PERMISSIONS', '')
    agent = agent_for(tmp_path, [])
    root = str(tmp_path.resolve())
    agent.approval.workspace_allowed.add(root)
    agent.approval.session_allowed.add(_session_key(root, 'write_file', str(tmp_path / 'a.txt')))

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path))
        ready = await harness.until('ready')
        assert 'permission-panel' in ready['capabilities']
        harness.send('command', 'p1', command='permissions', args={'action': 'list'})
        overview = await harness.until('permission_overview', 'p1')
        ids = [grant['id'] for grant in overview['grants']]
        assert len(ids) == 2 and overview['revoked'] is None
        harness.send('command', 'p2', command='permissions', args={'action': 'revoke', 'grant_id': ids[0]})
        revoked = await harness.until('permission_overview', 'p2')
        assert revoked['revoked']['kind'] == 'workspace'
        assert [grant['kind'] for grant in revoked['grants']] == ['session']
        harness.send('command', 'p3', command='permissions', args={'action': 'revoke', 'grant_id': 'ss-' + '0' * 12})
        error = await harness.until('error', 'p3')
        assert error['code'] == 'permission_grant_not_found'
        harness.send('command', 'p4', command='permissions', args={'action': 'list', 'extra': 1})
        bad = await harness.until('error', 'p4')
        assert bad['code'] == 'invalid_request'
        harness.send('command', 'p5', command='permissions_reset')
        reset = await harness.until('command_result', 'p5')
        assert '已撤销当前工作区的本进程授权' in reset['text']
        harness.send('command', 'p6', command='permissions')
        text = await harness.until('command_result', 'p6')
        assert 'workspace 授权' in text['text'] and '授权档位' in text['text']
        await harness.close()

    asyncio.run(scenario())
