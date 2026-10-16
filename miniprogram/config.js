// 开发者工具访问本机后端；真机需改为自己实际可达的服务地址。
// 不从分享 query 读取 API 地址，避免把粘贴正文发到分享者指定的服务器。
module.exports = {
  apiBaseUrl: 'http://127.0.0.1:8000',
  requestTimeoutMs: 60000,
}
