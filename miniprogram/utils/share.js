function text(value) {
  if (typeof value !== 'string') return ''
  try {
    return decodeURIComponent(value)
  } catch (_) {
    // 畸形百分号编码仍按原文预填，不能导致页面无法打开。
    return value
  }
}

/** 将启动参数映射到既有 intake 字段；没有分享内容时返回 null。
 * 支持嵌套 q 和扁平 query；只解码一层，不将 webview_url 当文章链接。
 * title/summary 都是分享者预置的数据，不代表微信提供了文章正文。
 */
function sharePayload(options) {
  const query = (options && options.query) || {}
  const payload = {
    scene: String((options && options.scene) || query.scene || ''),
    // 扁平字段已有独立契约，重复编码整包会让合法标题/摘要撑爆 query 的 2000 字上限。
    query: text(query.q),
    title: text(query.title),
    summary: text(query.summary),
    article_url: text(query.article_url || query.url),
    webview_url: text(query.webview_url || query.webviewUrl),
    referer: text(query.referer),
  }
  return Object.keys(payload).some((key) => key !== 'scene' && key !== 'query' && payload[key]) || query.q
    ? payload : null
}

module.exports = { sharePayload }
