// 复用 activity_details_server.py 和静态前端；只操作自身合成草稿。
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
  let externalRequests = 0;
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === 'http://127.0.0.1:5176') return route.continue();
    if (url.origin !== 'http://127.0.0.1:8000') { externalRequests++; return route.abort(); }
    const response = await route.fetch({ url: `http://127.0.0.1:8023${url.pathname}${url.search}` });
    return route.fulfill({ response });
  });
  try {
    await page.goto('http://127.0.0.1:5176');
    await page.getByRole('button', { name: 'Review', exact: true }).click();
    const card = page.locator('article.review-card').filter({ hasText: '合成待核对草稿' });
    const confirm = card.getByRole('button', { name: '确认入库', exact: true });
    assert.equal(await confirm.isDisabled(), true);
    await card.getByRole('button', { name: '修改草稿与依据', exact: true }).click();
    await card.getByLabel('选择字段', { exact: true }).selectOption('registration_deadline_at');
    const input = card.getByLabel('更正后的报名截止', { exact: true });
    await input.fill('2027-01-05');
    await card.getByLabel('更正原因（选填）', { exact: true }).fill('已核对完整年份');
    await card.getByRole('button', { name: '保存更正', exact: true }).click();
    await card.getByText('当前值：2027-01-05', { exact: true }).waitFor();
    assert.equal(await confirm.isDisabled(), true); // 编辑尚未收起。
    await card.getByRole('button', { name: '收起编辑，返回确认', exact: true }).click();
    assert.equal(await confirm.isEnabled(), true);
    await card.getByRole('button', { name: '修改草稿与依据', exact: true }).click();
    await card.getByLabel('选择字段', { exact: true }).selectOption('registration_deadline_at');
    await card.getByText('当前值：2027-01-05', { exact: true }).waitFor();
    await input.fill('2027-02-30');
    await card.getByRole('button', { name: '保存更正', exact: true }).click();
    await card.getByText('当前值：2027-02-30', { exact: true }).waitFor();
    await card.getByRole('button', { name: '收起编辑，返回确认', exact: true }).click();
    assert.equal(await confirm.isDisabled(), true);
    await card.getByRole('button', { name: '修改草稿与依据', exact: true }).click();
    await card.getByLabel('撤回原因（撤回时必填）').fill('撤回无效日期');
    await card.getByRole('button', { name: '撤回这次保存', exact: true }).click();
    await card.getByLabel('选择字段', { exact: true }).selectOption('registration_deadline_at');
    await card.getByText('当前值：2027-01-05', { exact: true }).waitFor();
    await mkdir('.testdata/t003-ui-artifacts', { recursive: true });
    await card.locator('.review-fields').screenshot({ path: '.testdata/t003-ui-artifacts/review-desktop.png' });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await card.locator('.correction-form').screenshot({ path: '.testdata/t003-ui-artifacts/review-mobile.png' });
    await card.getByRole('button', { name: '收起编辑，返回确认', exact: true }).click();

    // 外部合成编辑使列表版本过期；确认应拒绝，并保留待核对草稿。
    const old = await (await context.request.get('http://127.0.0.1:8023/api/review-queue/review-a')).json();
    const change = await context.request.patch('http://127.0.0.1:8023/api/review-queue/review-a',
      { data: { changes: { organizer: '合成更正机构' }, expected_revision: old.revision } });
    assert.equal(change.status(), 200);
    await confirm.click();
    await page.getByText('草稿已被更正，请重新读取后再确认', { exact: true }).waitFor();
    assert.equal(await card.count(), 1);
    await page.getByRole('button', { name: '刷新', exact: true }).click();
    await card.getByRole('button', { name: '修改草稿与依据', exact: true }).click();
    await card.getByLabel('选择字段', { exact: true }).selectOption('organizer');
    await card.getByText('当前值：合成更正机构', { exact: true }).waitFor();
    await card.getByRole('button', { name: '收起编辑，返回确认', exact: true }).click();
    // 键盘路径验收：focus + Enter 必须与点击等价触发确认
    await confirm.focus();
    await page.keyboard.press('Enter');
    await page.getByText('已确认入库；是否参加请在机会库标记（感兴趣/已报名/放弃）', { exact: true }).waitFor();
    // 确认事实不预设参与决定：入库活动初始状态为待核对
    await page.getByText('待核对', { exact: true }).first().waitFor();
    await card.waitFor({ state: 'detached' });
    await page.getByRole('button', { name: 'Activities', exact: true }).click();
    const event = page.locator('article.source-row').filter({ hasText: '合成待核对草稿' });
    await event.getByRole('button', { name: '查看证据与修改', exact: true }).click();
    await event.getByRole('button', { name: '查看入库前草稿', exact: true }).click();
    await event.getByText(/这里保留入库前草稿与更正历史/).waitFor();
    const nested = event.getByRole('region', { name: '草稿核对' });
    // section 有可访问名称时为 region；已确认草稿只读。
    assert.equal(await nested.getByRole('button', { name: '保存更正', exact: true }).count(), 0);
    await nested.getByText('原因：已核对完整年份', { exact: true }).waitFor();
    assert.deepEqual(pageErrors, []);
    assert.equal(externalRequests, 0);
    console.log('PASS: 草稿问题阻止确认、保存/重开、无效时间保留、撤回、窄屏、键盘确认、陈旧确认拒绝、已存版本入库（不预设参与状态）、入库前历史只读；外部请求0。');
  } finally {
    await context.close(); await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
