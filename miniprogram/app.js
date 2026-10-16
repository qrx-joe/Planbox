const { sharePayload } = require('./utils/share')

App({
  onLaunch(options) {
    this.captureShare(options)
  },

  onShow(options) {
    this.captureShare(options)
  },

  /** 冷启动与热启动只捕获参数，不在生命周期中创建后端条目。 */
  captureShare(options) {
    const payload = sharePayload(options)
    if (payload) this.globalData.sharePayload = payload
  },

  globalData: { sharePayload: null },
})
