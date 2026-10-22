const assert = require('node:assert/strict');
const { test } = require('node:test');

async function helpers() {
  return import('../frontend/src/time.ts');
}

test('仅日期的今天在晚间仍未过期，次日才过期', async () => {
  const { deadlineStatus, formatRelativeDate } = await helpers();
  const now = new Date('2026-10-20T23:59:00+08:00');
  assert.equal(deadlineStatus('2026-10-20', now), 'urgent');
  assert.match(formatRelativeDate('2026-10-20', now), /今天.*仅日期/);
  assert.equal(deadlineStatus('2026-10-19', now), 'expired');
  assert.match(formatRelativeDate('2026-10-21', now), /明天/);
});

test('精确截止按实际时刻，不同偏移等价', async () => {
  const { deadlineStatus, formatRelativeDate } = await helpers();
  const now = new Date('2026-10-20T09:00:00+08:00');
  assert.equal(deadlineStatus('2026-10-20T09:01:00+08:00', now), 'urgent');
  assert.equal(deadlineStatus('2026-10-20T00:59:00Z', now), 'expired');
  assert.equal(formatRelativeDate('2026-10-20T00:59:00Z', now), '已过期');
});

test('无时区、不完整、非法日期不能作为倒计时', async () => {
  const { deadlineStatus, formatRelativeDate } = await helpers();
  for (const value of ['2026-10-20T09:00:00', '1月5日', '2026-02-30',
    '2026-10-20T09:00:00-00:00', '2026-10-20T09:00:00+08:99',
    '2026-10-20T24:00:00+08:00', '明天18:00']) {
    assert.equal(deadlineStatus(value), 'needs_review');
    assert.equal(formatRelativeDate(value), '时间待核对');
  }
  assert.equal(deadlineStatus(null), 'unknown');
});

test('跨年和指定展示时区按日历日判断', async () => {
  const { deadlineStatus, formatRelativeDate } = await helpers();
  const now = new Date('2026-12-31T23:30:00+08:00');
  assert.match(formatRelativeDate('2027-01-01', now), /明天/);
  assert.equal(deadlineStatus('2026-12-31', now), 'urgent');
  assert.equal(deadlineStatus('2026-12-31', new Date('2027-01-01T01:00:00Z'), 'UTC'), 'expired');
});

test('用户提示用字段名称与原因，不暴露接口字段路径', async () => {
  const { timeReviewMessage } = await helpers();
  assert.equal(timeReviewMessage([{ field: 'event_start_at', code: 'missing_timezone' }]), '活动开始：时区未确认');
  assert.equal(timeReviewMessage(undefined), '');
});
