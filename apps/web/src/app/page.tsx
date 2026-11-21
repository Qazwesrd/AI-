import CreationWorkflowGuide from "../components/creation-workflow-guide";

const features = [
  {
    number: "01",
    icon: "🎬",
    title: "AI漫剧制作",
    description: "导入小说，自动识别人物、场景和道具，生成剧本、分镜与漫剧视频。",
    action: "开始制作",
    href: "/novels"
  },
  {
    number: "02",
    icon: "✂️",
    title: "AI智能剪辑",
    description: "上传原视频，根据文字要求自动剪辑；也可以直接根据文字生成视频。",
    action: "开始剪辑",
    href: "/video-editor"
  },
  {
    number: "03",
    icon: "📝",
    title: "剧本与智能分镜",
    description: "小说自动改编剧本，智能规划镜头、动作、对白和场景。",
    action: "开始创作",
    href: "/storyboard"
  },
  {
    number: "04",
    icon: "🧊",
    title: "三维视觉素材",
    description: "生成人物三视图、场景与道具视觉素材，保持角色形象一致。",
    action: "开始生成",
    href: "/visual-studio"
  },
  {
    number: "05",
    icon: "🚀",
    title: "多平台视频发布",
    description: "管理国内外平台账号，批量准备视频、封面和发布文案，跟踪发布任务。",
    action: "进入发布中心",
    href: "/publishing"
  },
  {
    number: "06",
    icon: "🧬",
    title: "人物与素材状态追踪",
    description: "按章节追踪人物外貌、服装、伤势、装备与道具归属，保留证据和历史版本。",
    action: "查看剧情状态",
    href: "/asset-timeline"
  }
];

const navigation = [
  {
    title: "创作空间",
    items: [
      ["✍️", "小说创作", "/novels"],
      ["📁", "项目管理", "/novels"],
      ["👤", "人物角色", "/visual-studio"],
      ["🧬", "人物与素材状态追踪", "/asset-timeline"],
      ["🏞️", "场景设定", "/visual-studio"],
      ["💎", "道具素材", "/visual-studio"],
      ["🌌", "世界观设定", "/novels"]
    ]
  },
  {
    title: "AI创作中心",
    items: [
      ["🎬", "AI漫剧制作", "/novels"],
      ["✂️", "AI视频剪辑", "/video-editor"],
      ["📜", "剧本与分镜", "/storyboard"],
      ["🧊", "三维素材生成", "/visual-studio"],
      ["🧭", "统一任务中心", "/tasks"],
      ["🌐", "AI网页工具协同中心", "/ai-tools"],
      ["🤝", "网页版 AI 协作执行中心", "/ai-collaboration"],
      ["🧠", "小说深度分析与剧情审核", "/ai-analysis"]
    ]
  },
  {
    title: "多媒体与发布",
    items: [
      ["🚀", "多平台发布中心", "/publishing"],
      ["👤", "发布账号管理", "/publishing/accounts"]
    ]
  },
  {
    title: "系统设置",
    items: [
      ["⚙️", "AI模型管理", "/ai-center"],
      ["📊", "AI调用记录", "/ai-center#usage-logs"],
      ["🗄️", "素材库", "/assets"],
      ["🔧", "网络与付费权限", "/ai-center"],
      ["💾", "项目数据备份与恢复", "/backup-center"],
      ["🩺", "系统运行诊断", "/system-diagnostics"],
      ["📘", "使用说明书", "/help"]
    ]
  }
];

export default function Home() {
  return (
    <main className="cosmic min-h-screen">
      <div className="black-hole" aria-hidden="true" />
      <div className="nebula nebula-one" />
      <div className="nebula nebula-two" />
      <div className="nebula-cloud nebula-cloud-a" />
      <div className="nebula-cloud nebula-cloud-b" />
      <div className="nebula-cloud nebula-cloud-c" />
      <div className="nebula-cloud nebula-cloud-d" />
      <div className="nebula-cloud nebula-cloud-e" />
      <div className="nebula-cloud nebula-cloud-f" />
            
            
<div className="meteor meteor-one" />
      <div className="meteor meteor-two" />
      <div className="meteor meteor-three" />
      <div className="meteor meteor-four" />
      <div className="meteor meteor-five" />

      <div className="main-layout relative z-10 flex min-h-screen">

        {/* 左侧导航 */}
        <aside className="sidebar w-[235px] shrink-0 min-h-screen p-5">
          <div className="flex items-center gap-3 mb-9">
            <div className="text-4xl">🎞️</div>
            <div>
              <div className="text-lg font-bold text-white">
                AI漫剧制作大师
              </div>
              <div className="text-xs text-purple-300 mt-1">
                AI Comic Studio
              </div>
            </div>
          </div>

          <a href="#" className="nav-item nav-active mb-5">
            <span>⌂</span> 首页
          </a>

          {navigation.map((group) => (
            <div key={group.title} className="mb-6">
              <div className="text-xs text-purple-300 mb-2 px-3">
                {group.title}
              </div>

              {group.items.map((item) => (
                <a
                  href={item[2]}
                  key={item[1]}
                  className="nav-item"
                >
                  <span>{item[0]}</span>
                  {item[1]}
                </a>
              ))}
            </div>
          ))}

          <div className="glass rounded-2xl p-4 mt-8">
            <div className="text-2xl mb-2">🚀</div>
            <div className="text-sm font-bold text-white">
              探索无限可能
            </div>
            <p className="text-xs text-purple-300 mt-2">
              开启你的AI漫剧创作之旅
            </p>
          </div>
        </aside>

        {/* 主内容 */}
        <section className="flex-1 min-w-0 p-6 lg:p-9">

          {/* 顶部栏 */}
          <header className="flex justify-between items-center mb-10">
            <div>
              <div className="text-xs text-purple-300 mb-1">
                AI CREATIVE WORKSPACE
              </div>
              <div className="text-sm text-purple-100">
                欢迎来到你的AI创作工作室
              </div>
            </div>

            <div className="glass rounded-full px-4 py-2 text-sm flex items-center gap-2">
              <span className="status-dot" />
              <span>本地工作空间</span>
            </div>
            <a href="/workspace" className="rounded-full border border-fuchsia-300/30 bg-purple-950/60 px-4 py-2 text-sm text-fuchsia-100 transition hover:bg-purple-800/60">
              🧭 打开项目工作台
            </a>
          </header>

          {/* 欢迎区域 */}
          <section className="mb-9">
            <div className="text-purple-300 text-sm tracking-[5px] mb-3">
              CREATE YOUR OWN UNIVERSE
            </div>

            <h1 className="text-4xl lg:text-5xl font-black leading-tight">
              <span className="text-white">让故事</span>
              <span className="bg-gradient-to-r from-purple-300 via-fuchsia-400 to-pink-300 bg-clip-text text-transparent">
                {" "}变成现实
              </span>
            </h1>

            <p className="text-purple-200 mt-4 text-sm lg:text-base">
              从一段文字，到一部精彩的AI漫剧。
              让想象力在宇宙中自由绽放。
            </p>
          </section>

          {/* 一条主线完成创作 */}
          <section className="glass rounded-2xl p-5 md:p-6 mb-9">
            <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
              <div>
                <div className="text-xs tracking-widest text-purple-300 mb-1">SIMPLE CREATION FLOW</div>
                <h2 className="text-xl font-bold text-white">按这 6 步完成一部漫剧</h2>
                <p className="text-xs text-purple-200 mt-2">按顺序操作即可；每一步完成并确认后，再进入下一步。已有项目可从上次进度继续。</p>
              </div>
              <a href="/help" className="text-xs text-cyan-200 underline underline-offset-4">查看完整操作说明</a>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
              {[
                ["01", "导入小说", "新建项目、导入 TXT / 文本并整理章节。", "/novels"],
                ["02", "分析剧情", "提取人物、关系、场景、道具和世界观，检查后确认。", "/ai-analysis"],
                ["03", "确认视觉素材", "制作并确认人物、场景、道具素材，保持形象一致。", "/visual-studio"],
                ["04", "改编剧本与分镜", "规划分集、编写剧本、拆分镜头并关联素材。", "/storyboard"],
                ["05", "生成与合成视频", "生成镜头视频、配音并合成本集成片。", "/media-studio"],
                ["06", "质检与发布", "检查成片、准备封面文案，再手动创建发布任务。", "/publishing"]
              ].map(([number, title, description, href]) => (
                <a key={number} href={href} className="group rounded-xl border border-purple-400/20 bg-slate-950/35 p-4 transition-colors hover:border-purple-300/70 hover:bg-purple-950/40">
                  <div className="flex items-center gap-3 mb-2">
                    <span className="flex h-8 w-8 items-center justify-center rounded-full border border-purple-300/40 text-xs font-bold text-purple-200">{number}</span>
                    <span className="font-semibold text-white">{title}</span>
                    <span className="ml-auto text-purple-300 transition-transform group-hover:translate-x-1">→</span>
                  </div>
                  <p className="pl-11 text-xs leading-5 text-purple-200">{description}</p>
                </a>
              ))}
            </div>
            <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-2 text-xs text-purple-200">
              <div className="rounded-lg bg-purple-950/35 p-3">📌 <strong>先确认，再继续：</strong>AI 分析和素材建议需要人工检查。</div>
              <div className="rounded-lg bg-purple-950/35 p-3">💰 <strong>费用由你决定：</strong>付费服务必须先核实价格并确认。</div>
              <div className="rounded-lg bg-purple-950/35 p-3">🔒 <strong>不会擅自执行：</strong>不自动调用 AI、不自动充值、不自动发布。</div>
            </div>
          </section>

          {/* 四大功能卡片 */}
          <section className="feature-grid grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5 gap-4 mb-9">
            {features.map((feature) => (
              <article
                key={feature.number}
                className="glass feature-card rounded-2xl p-5 flex flex-col"
              >
                <div className="flex justify-between items-center mb-5">
                  <span className="feature-number">
                    {feature.number}
                  </span>
                  <span className="text-4xl">
                    {feature.icon}
                  </span>
                </div>

                <h2 className="text-lg font-bold text-white mb-3">
                  {feature.title}
                </h2>

                <p className="text-xs text-purple-200 leading-6 flex-1">
                  {feature.description}
                </p>

                <a
                  href={feature.href}
                  className="primary-button text-sm text-center mt-5 inline-block"
                >
                  {feature.action} →
                </a>
              </article>
            ))}
          </section>

          <CreationWorkflowGuide />

          {/* 下方区域 */}
          <section className="grid grid-cols-1 xl:grid-cols-3 gap-5">

            <div className="glass rounded-2xl p-6 xl:col-span-2">
              <div className="flex justify-between items-center mb-6">
                <h2 className="font-bold text-lg">
                  ✨ 创作工作台
                </h2>
                <span className="text-xs text-purple-300">
                  READY TO CREATE
                </span>
              </div>

              <div className="rounded-xl bg-purple-950/50 border border-purple-500/30 p-5">
                <div className="text-purple-200 text-sm mb-3">
                  开始你的第一个AI漫剧项目
                </div>

                <p className="text-xs text-purple-300 mb-5">
                  导入小说、文章，或者直接输入文字要求。
                  AI将帮助你完成从剧本到视频的创作。
                </p>

                <a href="/novels" className="primary-button inline-block text-center">
                  ＋ 创建新项目
                </a>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-5">
                {[
                  ["📖", "小说解析", "/novels"],
                  ["👥", "人物识别", "/visual-studio"],
                  ["🎞️", "智能分镜", "/storyboard"],
                  ["🎥", "视频生成", "/media-studio"],
                  ["🧭", "统一任务", "/tasks"],
                  ["📘", "使用说明", "/help"]
                ].map(([icon, name, href]) => (
                  <a
                    href={href}
                    key={name}
                    className="rounded-xl bg-purple-900/30 border border-purple-500/20 p-4 text-center hover:border-purple-300/70 transition-colors"
                  >
                    <div className="text-2xl mb-2">{icon}</div>
                    <div className="text-xs text-purple-200">{name}</div>
                  </a>
                ))}
              </div>
            </div>

            <div className="glass rounded-2xl p-6">
              <h2 className="font-bold text-lg mb-5">
                ⚡ 系统状态
              </h2>

              <div className="space-y-4 text-sm">
                <div className="flex justify-between">
                  <span className="text-purple-200">本地工作空间</span>
                  <span className="text-green-400">本地运行</span>
                </div>

                <div className="flex justify-between">
                  <span className="text-purple-200">AI模型服务</span>
                  <span className="text-yellow-300">进入 AI 管理中心查看</span>
                </div>

                <div className="flex justify-between">
                  <span className="text-purple-200">视频处理引擎</span>
                  <span className="text-yellow-300">随桌面组件提供</span>
                </div>

                <div className="flex justify-between">
                  <span className="text-purple-200">桌面程序</span>
                  <span className="text-green-400">桌面端</span>
                </div>
              </div>

              <div className="border-t border-purple-500/30 mt-6 pt-5">
                <div className="text-sm font-bold mb-2">
                  🌠 创作无限可能
                </div>
                <p className="text-xs text-purple-300 leading-6">
                  让文字拥有画面，让故事拥有生命。
                </p>
              </div>
            </div>

          </section>

          <footer className="text-center text-xs text-purple-400/70 mt-10">
            AI漫剧制作大师 · AI Comic Studio
          </footer>

        </section>
      </div>
    </main>
  );
}






