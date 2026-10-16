const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

const root = path.resolve(__dirname, '../miniprogram')
const body = '这是一份合成的活动正文，包含活动介绍、报名方式和待核对的信息。'.repeat(4)
const clone = (value) => JSON.parse(JSON.stringify(value))
const needs = { source_item_id: 'src_test', state: 'needs_content', extraction_started: false, next_action: '请粘贴正文' }
const ready = { source_item_id: 'src_test', state: 'ready', extraction_started: true, extraction_id: 'ext_test' }

// 加载真实 App/Page/API 模块，只替换微信宿主；不创建后端数据或付费调用。
function runtime(replies = []) {
  const requests = []
  const cache = new Map()
  let app, page, modal
  const wx = {
    request(options) {
      requests.push(clone({ url: options.url, data: options.data, method: options.method, timeout: options.timeout }))
      const reply = replies.shift()
      assert.ok(reply, 'unexpected request')
      queueMicrotask(() => reply.fail ? options.fail({ errMsg: reply.fail })
        : options.success({ statusCode: reply.status || 200, data: reply.data }))
      // 故意返回非 Promise 的 RequestTask，确保代码使用 success/fail 回调。
      return { abort() {} }
    },
    showModal(options) { modal = options },
  }
  function load(filename) {
    if (cache.has(filename)) return cache.get(filename).exports
    const module = { exports: {} }
    cache.set(filename, module)
    vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
      module, exports: module.exports, wx,
      require: (relative) => load(path.resolve(path.dirname(filename), relative + '.js')),
      getApp: () => app,
      App: (definition) => { app = definition },
      Page: (definition) => {
        page = definition
        page.data = clone(definition.data)
        page.setData = (update) => Object.assign(page.data, clone(update))
      },
    }, { filename })
    return module.exports
  }
  load(path.join(root, 'app.js'))
  load(path.join(root, 'pages/inbox/index.js'))
  return { app, page, requests, load, modal: () => modal }
}

test('工程配置与注册页面齐全，AppID 留空、默认仅指向本机', () => {
  const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json')))
  const project = JSON.parse(fs.readFileSync(path.join(root, 'project.config.json')))
  assert.equal(project.appid, '')
  assert.equal(project.compileType, 'miniprogram')
  assert.equal(project.setting.urlCheck, true)
  for (const page of app.pages) {
    for (const ext of ['js', 'wxml', 'wxss', 'json']) {
      assert.ok(fs.statSync(path.join(root, page + '.' + ext)).isFile())
    }
    JSON.parse(fs.readFileSync(path.join(root, page + '.json')))
  }
  JSON.parse(fs.readFileSync(path.join(root, app.sitemapLocation)))
  assert.equal(require('../miniprogram/config').apiBaseUrl, 'http://127.0.0.1:8000')
})

test('冷启动编码参数预填，首次 onShow 不创建条目、不用摘要填充正文', () => {
  const { app, page, requests } = runtime()
  const options = { scene: 1007, query: { title: encodeURIComponent('合成活动'), article_url: encodeURIComponent('https://mp.weixin.qq.com/s/test'), summary: '摘要' } }
  app.onLaunch(options)
  app.onShow(options)
  page.onLoad({})
  page.onShow()
  assert.equal(page.data.title, '合成活动')
  assert.equal(page.data.articleUrl, 'https://mp.weixin.qq.com/s/test')
  assert.equal(page.data.summary, '摘要')
  assert.equal(page.data.rawText, '')
  assert.equal(app.globalData.sharePayload.scene, '1007')
  assert.equal(requests.length, 0)
})

test('扁平分享字段不重复编码到 query，避免合法长标题和摘要触发 422', async () => {
  const { app, page, requests } = runtime([{ data: ready }])
  app.onLaunch({ query: { title: '题'.repeat(300), summary: '文'.repeat(1000) } })
  page.onLoad({})
  await page.onSubmit()
  assert.ok(requests[0].data.query === null, '扁平字段不应重复填入 query')
  assert.equal(requests[0].data.title.length, 300)
  assert.equal(requests[0].data.summary.length, 1000)
})

test('无分享可直接打开，页面参数兜底且畸形编码不会白屏', () => {
  const { app, page } = runtime()
  app.onLaunch({ scene: 1001, query: {} })
  page.onLoad({ title: '100%活动', url: 'https://mp.weixin.qq.com/s/manual' })
  assert.equal(page.data.title, '100%活动')
  assert.equal(page.data.articleUrl, 'https://mp.weixin.qq.com/s/manual')
})

test('嵌套 q 原样进入后端字段，webviewUrl 不冒充文章链接', async () => {
  const { app, page, requests } = runtime([{ data: needs }])
  app.onShow({ scene: 1008, query: { q: encodeURIComponent('url=https://mp.weixin.qq.com/s/nested'), webviewUrl: 'https://example.invalid/view' } })
  page.onLoad({})
  await page.onSubmit()
  assert.equal(requests[0].data.query, 'url=https://mp.weixin.qq.com/s/nested')
  assert.equal(requests[0].data.article_url, null)
  assert.equal(requests[0].data.webview_url, 'https://example.invalid/view')
  assert.equal(page.data.pendingId, needs.source_item_id)
  assert.equal(page.data.finished, false)
  assert.equal(page.data.hint, '请粘贴正文')
})

test('首次粘贴正文后串行建档与补全，成功锁定防连点', async () => {
  const { page, requests } = runtime([{ data: needs }, { data: ready }])
  page.onLoad({ title: '合成标题' })
  page.onTextInput({ detail: { value: '  ' + body + '  ' } })
  const submitting = page.onSubmit()
  await page.onSubmit()
  await submitting
  assert.equal(requests.length, 2)
  assert.equal(requests[0].url, 'http://127.0.0.1:8000/api/share/intake')
  assert.equal(requests[1].url, 'http://127.0.0.1:8000/api/share/src_test/complete')
  assert.deepEqual(requests[1].data, { raw_text: body, title: '合成标题', auto_extract: true })
  assert.equal(page.data.finished, true)
  assert.equal(page.data.extractionId, 'ext_test')
  assert.equal(page.data.busy, false)
  await page.onSubmit()
  assert.equal(requests.length, 2)
})

test('空参数建档后在同一 ID 补全，短正文在本地阻止', async () => {
  const { page, requests } = runtime([{ data: needs }, { data: ready }])
  page.onLoad({})
  await page.onSubmit()
  assert.equal(requests.length, 1)
  page.onTextInput({ detail: { value: '太短' } })
  await page.onSubmit()
  assert.match(page.data.error, /50/)
  assert.equal(requests.length, 1)
  page.onTextInput({ detail: { value: body } })
  await page.onSubmit()
  assert.equal(requests.length, 2)
  assert.equal(page.data.finished, true)
})

test('Unicode 字数按字符计算；空白、超长输入不会作为正文提交', async () => {
  const { page, requests } = runtime([{ data: needs }, { data: ready }])
  page.onLoad({})
  page.onTextInput({ detail: { value: '😀'.repeat(49) } })
  await page.onSubmit()
  assert.equal(page.data.charCount, 49)
  assert.equal(requests.length, 0)
  page.onTextInput({ detail: { value: '文'.repeat(200001) } })
  await page.onSubmit()
  assert.equal(requests.length, 0)
  page.onTextInput({ detail: { value: '😀'.repeat(50) } })
  await page.onSubmit()
  assert.equal(page.data.finished, true)
})

test('HTTP 400/404/422/500 通过 success 回调返回时仍显示失败并保留输入和 ID', async (t) => {
  for (const [status, detail, message] of [
    [400, '清理噪声后正文太短', /正文太短/],
    [404, 'Source item not found', /条目不存在/],
    [422, [{ msg: 'String too long', input: 'PRIVATE_BODY' }], /输入格式/],
    [500, 'PRIVATE_STACK', /服务暂时不可用/],
  ]) {
    await t.test(String(status), async () => {
      const { page, requests } = runtime([{ data: needs }, { status, data: { detail } }])
      page.onLoad({})
      page.onTextInput({ detail: { value: body } })
      await page.onSubmit()
      assert.equal(requests.length, 2)
      assert.equal(page.data.pendingId, needs.source_item_id)
      assert.equal(page.data.rawText, body)
      assert.equal(page.data.finished, false)
      assert.equal(page.data.busy, false)
      assert.match(page.data.error, message)
      assert.doesNotMatch(page.data.error, /PRIVATE/)
    })
  }
})

test('建档网络失败不自动重试，并提示结果未知', async () => {
  const { page, requests } = runtime([{ fail: 'request:fail timeout SECRET' }])
  page.onLoad({})
  page.onTextInput({ detail: { value: body } })
  await page.onSubmit()
  assert.equal(requests.length, 1)
  assert.equal(page.data.pendingId, '')
  assert.equal(page.data.rawText, body)
  assert.match(page.data.error, /结果可能未知/)
  assert.doesNotMatch(page.data.error, /SECRET/)
})

test('补全网络失败后手动重试仅重发 complete，重复正文响应不会误称新草稿', async () => {
  const { page, requests } = runtime([{ data: needs }, { fail: 'request:fail timeout' }, { data: { ...ready, extraction_started: false, extraction_id: null } }])
  page.onLoad({})
  page.onTextInput({ detail: { value: body } })
  await page.onSubmit()
  await page.onSubmit()
  assert.equal(requests.length, 3)
  assert.ok(requests[2].url.endsWith('/src_test/complete'))
  assert.equal(page.data.finished, true)
  assert.match(page.data.hint, /未触发新的抽取/)
  assert.doesNotMatch(page.data.hint, /已生成/)
})

test('intake ready 分别展示保存与抽取结果；用户粘贴的正文仍会补全', async () => {
  const first = runtime([{ data: { ...ready, extraction_started: false } }])
  first.page.onLoad({ summary: body })
  await first.page.onSubmit()
  assert.equal(first.requests.length, 1)
  assert.match(first.page.data.hint, /未触发新的抽取/)
  const second = runtime([{ data: ready }, { data: ready }])
  second.page.onLoad({ summary: '其他摘要' })
  second.page.onTextInput({ detail: { value: body } })
  await second.page.onSubmit()
  assert.equal(second.requests.length, 2)
  assert.equal(second.requests[1].data.raw_text, body)
})

test('2xx 畸形响应或补全 ID 不匹配不会显示成功', async () => {
  const first = runtime([{ data: '<html>error</html>' }])
  first.page.onLoad({})
  await first.page.onSubmit()
  assert.match(first.page.data.error, /返回格式异常/)
  const second = runtime([{ data: needs }, { data: { ...ready, source_item_id: 'src_other' } }])
  second.page.onLoad({})
  second.page.onTextInput({ detail: { value: body } })
  await second.page.onSubmit()
  assert.equal(second.page.data.finished, false)
  assert.match(second.page.data.error, /当前条目不符/)
})

test('热启动新分享保留旧正文；取消切换无损，确认切换才载入新参数', () => {
  const { app, page, requests, modal } = runtime()
  app.onLaunch({ query: { title: '第一篇' } })
  page.onLoad({})
  page.onTextInput({ detail: { value: body } })
  app.onShow({ query: { title: '第二篇' } })
  page.onShow()
  assert.equal(page.data.title, '第一篇')
  assert.equal(page.data.rawText, body)
  assert.equal(page.data.newShareAvailable, true)
  page.onStartNew()
  modal().success({ confirm: false })
  assert.equal(page.data.rawText, body)
  page.onStartNew()
  modal().success({ confirm: true })
  assert.equal(page.data.title, '第二篇')
  assert.equal(page.data.rawText, '')
  assert.equal(requests.length, 0)
})

test('普通切回前台不重填、不重建；完成后可清空并收集另一篇', async () => {
  const { app, page, requests } = runtime([{ data: ready }])
  app.onLaunch({ query: { title: '第一篇', summary: body } })
  page.onLoad({})
  app.onShow({ scene: 1001, query: {} })
  page.onShow()
  assert.equal(page.data.title, '第一篇')
  await page.onSubmit()
  page.onStartNew()
  page.onShow()
  assert.equal(page.data.title, '')
  assert.equal(page.data.pendingId, '')
  assert.equal(requests.length, 1)
})

test('清空预填链接后不让后端从旧 query 恢复；query 无法覆盖 API 地址', async () => {
  const { app, page, requests } = runtime([{ data: needs }])
  app.onLaunch({ query: { article_url: 'https://mp.weixin.qq.com/s/old', apiBaseUrl: 'https://example.invalid' } })
  page.onLoad({})
  page.onUrlInput({ detail: { value: '' } })
  await page.onSubmit()
  assert.equal(requests[0].data.article_url, null)
  assert.equal(requests[0].data.query, null)
  assert.ok(requests[0].url.startsWith('http://127.0.0.1:8000/'))
})
