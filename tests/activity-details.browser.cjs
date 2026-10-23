// 隔离浏览器回归：先运行 fixtures/activity_details_server.py 与本地静态前端。
// PLANBOX_PLAYWRIGHT_PATH 可指向已有 Playwright；本测试不安装依赖、不访问外网。
const assert = require('node:assert/strict');
const { mkdir } = require('node:fs/promises');
const { chromium } = require(process.env.PLANBOX_PLAYWRIGHT_PATH || 'playwright');

(async () => {
  const browser = await chromium.launch({ headless: true,
    executablePath: process.env.PLANBOX_CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  let releaseA;
  const gateA = new Promise((resolve) => { releaseA = resolve; });
  let holdA = true, failB = false, failHistoryB = false, abortPatch = false, externalRequests = 0;
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === 'http://127.0.0.1:5176') return route.continue();
    if (url.origin !== 'http://127.0.0.1:8000') {
      externalRequests += 1;
      return route.abort();
    }
    if (url.pathname === '/api/evidences/event-a' && holdA) await gateA;
    if (url.pathname === '/api/evidences/event-b' && failB) {
      return route.fulfill({ status: 500, contentType: 'application/json',
        body: JSON.stringify({ detail: 'synthetic evidence failure' }) });
    }
    if (url.pathname === '/api/events/event-b/corrections' && failHistoryB) {
      return route.fulfill({ status: 500, contentType: 'application/json', body: '{}' });
    }
    if (url.pathname === '/api/events/event-a' && route.request().method() === 'PATCH' && abortPatch) {
      return route.abort('failed');
    }
    const response = await route.fetch({ url: `http://127.0.0.1:8023${url.pathname}${url.search}` });
    return route.fulfill({ response });
  });
  try {
    await page.goto('http://127.0.0.1:5176');
    await page.getByRole('button', { name: 'Activities', exact: true }).click();
    const a = page.locator('article.source-row').filter({ hasText: '合成活动A' });
    const b = page.locator('article.source-row').filter({ hasText: '合成活动B' });
    await a.getByRole('button', { name: '查看证据与修改' }).click();
    await b.getByRole('button', { name: '查看证据与修改' }).click();
    await b.getByText(/B限定片段/).first().waitFor();
    assert.doesNotMatch(await b.textContent(), /A限定片段/);
    holdA = false; releaseA();
    await a.getByText(/A限定片段/).first().waitFor();
    assert.doesNotMatch(await a.textContent(), /B限定片段|不应直接展示的旧片段/);
    await a.getByLabel('选择字段', { exact: true }).selectOption('organizer');
    await a.getByLabel('更正后的主办方', { exact: true }).fill('更正机构甲');
    await a.getByLabel('更正原因（选填）', { exact: true }).fill('合成更正验证');
    await a.getByLabel('关联来源（选填）', { exact: true }).selectOption('source-a');
    await a.getByRole('button', { name: '保存更正', exact: true }).click();
    await a.getByText('当前值：更正机构甲', { exact: true }).waitFor();
    await a.getByText('原文可定位 · 与当前值不同，需核对', { exact: true }).waitFor();
    assert.doesNotMatch(await b.textContent(), /更正机构甲/);
    await a.getByRole('button', { name: '收起核对' }).click();
    await a.getByRole('button', { name: '查看证据与修改' }).click();
    await a.getByLabel('选择字段', { exact: true }).selectOption('organizer');
    await a.getByText('当前值：更正机构甲', { exact: true }).waitFor();
    await a.getByLabel('撤回原因（撤回时必填）').fill('合成撤回验证');
    await a.getByRole('button', { name: '撤回这次保存', exact: true }).click();
    await a.getByText('当前值：主办方甲', { exact: true }).waitFor();
    await a.getByText('撤回记录', { exact: false }).first().waitFor();
    await mkdir('.testdata/t003-ui-artifacts', { recursive: true });
    await a.screenshot({ path: '.testdata/t003-ui-artifacts/activity-desktop.png' });

    await a.getByLabel('选择字段', { exact: true }).selectOption('schedule');
    await a.getByRole('button', { name: '添加赛程节点' }).click();
    const node = a.getByRole('group', { name: '赛程 1', exact: true });
    await node.getByLabel('名称', { exact: true }).fill('合成初赛');
    await node.getByLabel('开始', { exact: true }).fill('2027-01-06');
    await node.getByLabel('结束', { exact: true }).fill('2027-01-07');
    await a.getByRole('button', { name: '保存更正', exact: true }).click();
    await a.getByText('当前值：共 1 个赛程节点', { exact: true }).waitFor();
    await a.getByRole('button', { name: '移除赛程 1', exact: true }).click();
    await a.getByRole('button', { name: '保存更正', exact: true }).click();
    await a.getByText('当前值：共 0 个赛程节点', { exact: true }).waitFor();
    await a.getByLabel('选择字段', { exact: true }).selectOption('registration_deadline_at');
    await a.getByLabel('更正后的报名截止', { exact: true }).fill('2027-02-30');
    await a.getByRole('button', { name: '保存更正', exact: true }).click();
    await a.getByText(/字段格式不符合要求/).waitFor();
    assert.equal(await a.getByLabel('更正后的报名截止', { exact: true }).inputValue(), '2027-02-30');
    await a.getByLabel('选择字段', { exact: true }).selectOption('organizer');

    await a.getByLabel('更正后的主办方', { exact: true }).fill('待保存输入');
    const concurrent = await context.request.patch('http://127.0.0.1:8023/api/events/event-a',
      { data: { organizer: '并发变更' } });
    assert.equal(concurrent.status(), 200);
    await a.getByRole('button', { name: '保存更正', exact: true }).click();
    await a.getByText('活动已被更正，请重新读取后再修改', { exact: true }).waitFor();
    assert.equal(await a.getByLabel('更正后的主办方', { exact: true }).inputValue(), '待保存输入');
    await a.getByRole('button', { name: '重新读取', exact: true }).click();
    await a.getByText('当前值：并发变更', { exact: true }).waitFor();
    abortPatch = true;
    await a.getByLabel('更正后的主办方', { exact: true }).fill('断网输入保留');
    await a.getByRole('button', { name: '保存更正', exact: true }).click();
    await a.getByText(/网络中断，保存结果未知/).waitFor();
    assert.equal(await a.getByLabel('更正后的主办方', { exact: true }).inputValue(), '断网输入保留');
    await b.getByRole('button', { name: '收起核对' }).click();
    failB = true;
    await b.getByRole('button', { name: '查看证据与修改' }).click();
    await b.getByText('证据读取失败，请重新读取', { exact: true }).waitFor();
    assert.doesNotMatch(await b.textContent(), /此活动尚无字段证据/);
    await b.getByRole('button', { name: '收起核对' }).click();
    failB = false; failHistoryB = true;
    await b.getByRole('button', { name: '查看证据与修改' }).click();
    await b.getByText('更正历史读取失败，暂不能安全编辑', { exact: true }).waitFor();
    assert.equal(await b.getByRole('button', { name: '保存更正', exact: true }).count(), 0);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await a.locator('.correction-form').screenshot({ path: '.testdata/t003-ui-artifacts/edit-mobile.png' });
    assert.deepEqual(pageErrors, []);
    assert.equal(externalRequests, 0);
    console.log('PASS: A/B乱序隔离、原文定位、保存/重开/撤回、结构化赛程增删、非法日期/陈旧版本拒绝、断网输入保留、证据/历史失败、390px无溢出；外部请求0。');
  } finally {
    releaseA();
    await context.close();
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
