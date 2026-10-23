const assert = require('node:assert/strict');
const { test } = require('node:test');

test('字段更正保留未知、false、赛程和时间精度', async () => {
  const { editValue, scheduleRows } = await import('../frontend/src/activity-details.ts');
  assert.equal(editValue('is_online', 'false'), false);
  assert.equal(editValue('is_online', ''), null);
  assert.equal(editValue('registration_deadline_at', '2027-01-05'), '2027-01-05');
  assert.equal(editValue('organizer', ''), null);
  assert.deepEqual(scheduleRows([{ name: '初赛', extra: '附加说明' }]), [{ name: '初赛', extra: '附加说明' }]);
  assert.throws(() => scheduleRows({}));
  assert.throws(() => scheduleRows([null]));
  assert.throws(() => editValue('title', '  '));
});

test('证据关系与布尔展示不伪装为确认结果', async () => {
  const { relationLabel, displayValue, fieldLabel } = await import('../frontend/src/activity-details.ts');
  assert.equal(displayValue(false), '否');
  assert.equal(displayValue(null), '未知');
  assert.equal(displayValue([{ name: '初赛', start_at: '2027-01-05' }]), '名称：初赛；开始：2027-01-05');
  assert.equal(fieldLabel('schedule.0.start_at'), '赛程节点');
  assert.match(relationLabel('matches_current'), /一致/);
  assert.match(relationLabel('differs_from_current'), /需核对/);
  assert.match(relationLabel('unknown'), /待核对/);
});

test('外部来源只提供 HTTP(S) 无凭据链接', async () => {
  const { sourceLink } = await import('../frontend/src/activity-details.ts');
  for (const value of ['javascript:alert(1)', 'data:text/html,abc', 'https://a:b@example.com',
    'https://example.com\n', '//example.com', 'https:\\example.com']) {
    assert.equal(sourceLink(value), null);
  }
  assert.equal(sourceLink('https://example.com/a?q=1'), 'https://example.com/a?q=1');
});

test('分页不能让不完整的整次更正被撤回，完整加载后才启用', async () => {
  const { correctionGroups } = await import('../frontend/src/activity-details.ts');
  const a = { id: 'a', operation_id: 'op', can_retract: true, operation_field_count: 2 };
  const b = { ...a, id: 'b' };
  assert.equal(correctionGroups([a])[0].retractable, false);
  assert.equal(correctionGroups([a, b, b])[0].retractable, true);
  assert.equal(correctionGroups([a, { ...b, can_retract: false }])[0].retractable, false);
});
