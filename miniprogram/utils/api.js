const config = require('../config')

function httpError(status, data) {
  if (status === 404) return '收集条目不存在，请在 Web 收集箱核对后重新收集。'
  if (status === 422) {
    const detail = data && data.detail
    const messages = Array.isArray(detail) ? detail.map((item) => item.msg).join('；') : ''
    return `输入格式不符合接口要求${messages ? `：${messages}` : '，请检查标题、链接和正文长度。'}`
  }
  if (status === 400 && data && typeof data.detail === 'string') return data.detail
  return status >= 500 ? `服务暂时不可用（HTTP ${status}），请稍后重试。`
    : `提交被拒绝（HTTP ${status}），请检查服务配置和输入。`
}

/** 使用 wx.request 回调封装 Promise；HTTP 非 2xx 与网络失败均拒绝。
 * 超时单位为毫秒，无自动重试；尤其 intake 不是幂等建档接口。
 * 仅显示可读错误，不输出原文、完整网络异常或服务端错误栈。
 */
function post(path, data) {
  return new Promise((resolve, reject) => {
    const base = config.apiBaseUrl.replace(/\/+$/, '')
    if (!/^https?:\/\/[^\s/?#]+(?:\/[^\s?#]*)?$/.test(base)) {
      reject(new Error('请先在 miniprogram/config.js 配置可访问的 API 地址。'))
      return
    }
    wx.request({
      url: base + path,
      method: 'POST',
      header: { 'content-type': 'application/json' },
      timeout: config.requestTimeoutMs,
      data,
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(response.data)
        } else {
          reject(new Error(httpError(response.statusCode, response.data)))
        }
      },
      fail(error) {
        const message = (error && error.errMsg) || ''
        reject(new Error(message.indexOf('url not in domain list') !== -1
          ? '请求域名未获允许，请检查开发者工具的本地设置或小程序服务器域名配置。'
          : '网络请求失败或超时，请检查网络与 API 地址后重试。'))
      },
    })
  })
}

function validateResult(data) {
  if (!data || typeof data.source_item_id !== 'string' || !data.source_item_id
    || !['needs_content', 'ready'].includes(data.state)
    || typeof data.extraction_started !== 'boolean'
    || (data.state === 'needs_content' && typeof data.next_action !== 'string')
    || (data.extraction_id != null && typeof data.extraction_id !== 'string')) {
    throw new Error('服务返回格式异常，请在 Web 收集箱核对是否已保存。')
  }
  return data
}

/** 建立分享条目；返回 needs_content 或 ready，不推断抽取成功。 */
async function intake(payload) {
  return validateResult(await post('/api/share/intake', payload))
}

/** 在同一条目补全文本；失败由页面保留 ID 和正文以便手动重试。 */
async function complete(sourceItemId, payload) {
  const data = validateResult(await post(`/api/share/${encodeURIComponent(sourceItemId)}/complete`, payload))
  if (data.source_item_id !== sourceItemId || data.state !== 'ready') {
    throw new Error('补全结果与当前条目不符，请在 Web 收集箱核对。')
  }
  return data
}

module.exports = { intake, complete }
