import assert from 'node:assert/strict';
import test from 'node:test';
import {approvalTitle, splitApprovalPreview} from '../src/approval-view.js';

test('moves the approval tier out of the preview tail and into the title', () => {
  const split = splitApprovalPreview('$ pytest -q\ncwd: /repo\n\n[审批层] T2 本地执行');
  assert.equal(split.tier, 'T2 本地执行');
  assert.equal(split.body, '$ pytest -q\ncwd: /repo');
  assert.equal(approvalTitle('bash', split.tier), '审批 · T2 本地执行 · bash');

  const trailing = splitApprovalPreview('--- a/a.txt\n+++ b/a.txt\n+hello\n\n[审批层] T1 工作区写入\n\n');
  assert.equal(trailing.tier, 'T1 工作区写入');
  assert.match(trailing.body, /\+hello$/);

  assert.deepEqual(splitApprovalPreview('普通预览'), {body: '普通预览', tier: null});
  assert.equal(approvalTitle('write_file', null), '审批 · write_file');
});
