const api = require('../../utils/api')
const { sharePayload } = require('../../utils/share')

function emptyForm() {
  return {
    title: '', articleUrl: '', summary: '', rawText: '', charCount: 0,
    pendingId: '', extractionId: '', busy: false, finished: false,
    error: '', hint: '分享参数只用于预填。请核对标题和链接，并粘贴文章正文。',
    newShareAvailable: false,
  }
}

Page({
  data: emptyForm(),

  onLoad(options) {
    this._payload = {}
    this._shareKey = ''
    this.applyShare(getApp().globalData.sharePayload || sharePayload({ query: options }))
  },

  onShow() {
    const payload = getApp().globalData.sharePayload
    if (!payload || JSON.stringify(payload) === this._shareKey) return
    // 从文章页回来不覆盖正在编辑的正文；新的分享由用户显式载入。
    if (this.data.busy || this.data.pendingId || this.data.rawText || this.data.title || this.data.articleUrl) {
      this.setData({ newShareAvailable: true })
    } else {
      this.applyShare(payload)
    }
  },

  applyShare(payload) {
    this._payload = payload || {}
    this._shareKey = payload ? JSON.stringify(payload) : ''
    this._urlEdited = false
    this.setData(Object.assign(emptyForm(), {
      title: this._payload.title || '',
      articleUrl: this._payload.article_url || '',
      summary: this._payload.summary || '',
    }))
  },

  onTitleInput(event) { this.setData({ title: event.detail.value }) },
  onUrlInput(event) {
    this._urlEdited = true
    this.setData({ articleUrl: event.detail.value })
  },
  onTextInput(event) {
    const rawText = event.detail.value
    this.setData({ rawText, charCount: Array.from(rawText.trim()).length })
  },

  /** 点击后才发起两段式写入。串行请求且不自动重试，防止连点重复建档。
   * needs_content 是正常补全状态；失败保留正文与已知 ID。
   * ready 仅代表正文保存成功，抽取反馈以 extraction_started 为准。
   */
  async onSubmit() {
    if (this.data.busy || this.data.finished) return
    const rawText = this.data.rawText.trim()
    const length = Array.from(rawText).length
    if ((rawText || this.data.pendingId) && (length < 50 || length > 200000)) {
      this.setData({ error: '正文需为 50～200000 字，请粘贴完整正文后提交。' })
      return
    }
    this.setData({ busy: true, error: '' })
    try {
      if (!this.data.pendingId) {
        const result = await api.intake(Object.assign({}, this._payload, {
          title: this.data.title.trim() || null,
          article_url: this.data.articleUrl.trim() || null,
          // 用户清空链接后，不能再从原始 query 悄悄恢复旧链接。
          query: this._urlEdited ? null : (this._payload.query || null),
        }))
        this.setData({ pendingId: result.source_item_id })
        if (result.state === 'ready' && !rawText) {
          this.showSaved(result)
          return
        }
        this.setData({ hint: result.next_action || '条目已建立，请在文章页复制正文，回到这里粘贴后提交。' })
      }
      if (!rawText) return
      const result = await api.complete(this.data.pendingId, {
        raw_text: rawText,
        title: this.data.title.trim() || null,
        auto_extract: true,
      })
      this.showSaved(result)
    } catch (error) {
      const warning = this.data.pendingId ? '' : ' 建档结果可能未知，请先在 Web 收集箱核对，避免重复创建。'
      this.setData({ error: error.message + warning })
    } finally {
      this.setData({ busy: false })
    }
  },

  showSaved(result) {
    this.setData({
      finished: true,
      extractionId: result.extraction_id || '',
      hint: result.extraction_started
        ? '正文已保存，已触发抽取。请到 Web 版 Review 核对结果。'
        : '正文已保存；本次未触发新的抽取（可能是重复提交或抽取失败）。请到 Web 收集箱核对。',
    })
  },

  onStartNew() {
    if (this.data.busy) return
    const reset = () => {
      const payload = this.data.newShareAvailable ? getApp().globalData.sharePayload : null
      if (!payload) getApp().globalData.sharePayload = null
      this.applyShare(payload)
    }
    if (!this.data.finished && (this.data.rawText || this.data.pendingId || this.data.title || this.data.articleUrl)) {
      wx.showModal({
        title: '开始新的收集？',
        content: '将清空当前页面的输入。已保存的条目仍在 Web 收集箱，未提交的正文不会保存。',
        success: (result) => { if (result.confirm) reset() },
      })
    } else {
      reset()
    }
  },
})
