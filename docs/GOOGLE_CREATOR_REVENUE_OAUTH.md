# Google 创作者收益 OAuth 配置（本机）

本功能仅读取 YouTube Analytics 预估收入和 AdSense 付款记录，不会操作收款方式，也不会发起提现。

## 1. 创建 Google OAuth 客户端
1. 在 Google Cloud Console 创建或选择项目。
2. 启用 YouTube Analytics API 和 AdSense Management API。
3. 配置 OAuth consent screen；如果应用处于 Testing 状态，将自己的 Google 账户加入 Test users。
4. 创建 OAuth Client ID，应用类型选择 Desktop app。
5. 复制 Client ID。Desktop app 客户端密钥不是可依赖的机密；GOOGLE_OAUTH_CLIENT_SECRET 为可选环境变量。

授权请求仅申请 openid、userinfo.email、yt-analytics-monetary.readonly、adsense.readonly。
本机回调固定为 http://127.0.0.1:8000/api/revenue/oauth/google/callback。Google Desktop OAuth 使用 loopback IP 回调和 PKCE。

## 2. 设置本机环境变量
- GOOGLE_OAUTH_CLIENT_ID：你的 Desktop OAuth Client ID
- AI_COMIC_TOKEN_ENCRYPTION_KEY：Fernet 密钥

生成 Fernet 密钥（在项目 Python 环境执行）：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

将输出保存到本机环境变量或仅本机使用的配置文件中。不要提交到 GitHub，不要发送给任何人。丢失或更换该密钥后，旧令牌无法解密，需要重新授权。

## 3. 连接与同步
1. 启动本机 API（默认端口 8000）和桌面 UI（默认端口 3000）。
2. 打开“收益与提现管理”。
3. 点击“连接 Google / YouTube”并在 Google 官方页面登录、授权。
4. 授权完成后点击“同步真实收入”。

同步项目：
- YouTube Analytics：最近 30 天的 estimatedRevenue，标记为 estimated。
- AdSense Management API：读取 accounts.payments.list 返回的 unpaid / paid payment 记录，分别标记 payable / paid。

注意：YouTube Analytics 是预估收入；AdSense payment 是平台付款记录。两者不是同一种金额，不应相加理解为可提现余额。Google API 可能因账号资格、频道权限、AdSense 账户状态、API 启用情况或 OAuth 审核状态而拒绝访问。

## 4. 提现边界
本功能只读取官方收入与付款记录。AdSense Payments API 的 list 是只读查询，不是付款发起接口。软件不会展示 Google 软件内提现执行按钮；请在 AdSense 官方账户中完成收款设置与提现。