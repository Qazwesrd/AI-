"use client";

export default function AICenterPage() {
  return (
    <main className="cosmic min-h-screen text-white">
      <div className="nebula nebula-one" />
      <div className="nebula nebula-two" />
      <div className="relative z-10 mx-auto min-h-screen max-w-5xl p-5 lg:p-8">
        <header className="mb-8">
          <a href="/" className="text-sm text-purple-300">← 返回首页</a>
          <h1 className="mt-3 text-3xl font-black">AI 创作中心</h1>
          <p className="mt-3 max-w-3xl text-sm leading-7 text-purple-100/80">
            本软件不内置 DeepSeek、豆包、OpenAI 等第三方云端 AI API，不要求填写 API 地址、API Key 或付费授权，也不会自动向这些服务提交 API 请求。
          </p>
        </header>

        <section className="grid gap-5 md:grid-cols-2">
          <a href="/local-models" className="rounded-2xl border border-emerald-300/30 bg-emerald-950/30 p-6 transition hover:border-emerald-200">
            <div className="text-2xl">🖥️ 本地 AI 模型</div>
            <p className="mt-3 text-sm leading-7 text-emerald-100/80">
              连接电脑上已经安装的 Ollama 和本地模型，在本机完成剧本、分镜、提示词等创作。不会调用第三方云端 AI API。
            </p>
            <span className="mt-5 inline-block rounded-lg bg-emerald-500/20 px-4 py-2 text-sm font-semibold text-emerald-100">进入本地模型中心 →</span>
          </a>

          <a href="/ai-tools" className="rounded-2xl border border-sky-300/30 bg-sky-950/30 p-6 transition hover:border-sky-200">
            <div className="text-2xl">🌐 网页版 AI 协作</div>
            <p className="mt-3 text-sm leading-7 text-sky-100/80">
              打开 ChatGPT、DeepSeek、豆包等网页版工具，由你自行登录并手动确认提交。软件负责整理章节、任务和提示词，不读取网页账号密码，也不配置 API。
            </p>
            <span className="mt-5 inline-block rounded-lg bg-sky-500/20 px-4 py-2 text-sm font-semibold text-sky-100">进入网页协作中心 →</span>
          </a>
        </section>

        <section className="mt-6 rounded-2xl border border-purple-300/20 bg-[#160d2acc] p-6">
          <h2 className="text-lg font-bold">费用与隐私说明</h2>
          <ul className="mt-3 list-disc space-y-2 pl-5 text-sm leading-7 text-purple-100/80">
            <li>不内置第三方云端 AI API，不需要用户提供任何 API Key。</li>
            <li>本地模型在本机运行；模型下载或安装可能消耗网络流量和磁盘空间。</li>
            <li>网页版 AI 由用户自行登录和操作；其账号权益、免费额度及收费规则由对应网站决定。</li>
            <li>软件不会自动购买服务、充值或开启付费订阅。</li>
          </ul>
        </section>
      </div>
    </main>
  );
}
