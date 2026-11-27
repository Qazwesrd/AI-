import BeginnerQuickStart from "../components/beginner-quick-start";

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
      ["🖼️", "本地图片生成", "/local-image-studio"],
      ["✂️", "AI视频剪辑", "/video-editor"],\n      ["🎞️", "本地视频工作台", "/media-studio"],
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
      ["🧩", "本地模型与引擎", "/local-models"],
      ["🧭", "AI资源包新手配置向导", "/setup-wizard"],
      ["🖼️", "本地图片生成工作台", "/local-image-studio"],
      ["🎞️", "视频生成与音频中心", "/media-studio"],
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

          {/* 新手与专业模式快速开始 */}
          <div className="mb-4 grid gap-3 md:grid-cols-3">
            <a href="/setup-wizard" className="glass rounded-2xl border border-cyan-300/30 p-4 transition hover:border-cyan-200/70">
              <div className="text-2xl">🧭</div><h2 className="mt-2 font-bold text-cyan-100">AI资源包配置向导</h2>
              <p className="mt-1 text-xs leading-5 text-purple-100/70">一步一步检查图片模型、视频模型、目录与引擎状态。</p>
              <span className="mt-3 inline-block text-xs text-cyan-200">开始配置 →</span>
            </a>
            <a href="/media-studio" className="glass rounded-2xl border border-fuchsia-300/30 p-4 transition hover:border-fuchsia-200/70">\n              <div className="text-2xl">🎞️</div><h2 className="mt-2 font-bold text-fuchsia-100">本地视频工作台</h2>\n              <p className="mt-1 text-xs leading-5 text-purple-100/70">查看 Wan/VACE 本地视频模型，并进入视频生成与音频中心。</p>\n              <span className="mt-3 inline-block text-xs text-fuchsia-200">进入视频工作台 →</span>\n            </a>\n            <a href="/local-models" className="glass rounded-2xl p-4 transition hover:border-purple-200/70">
              <div className="text-2xl">🧩</div><h2 className="mt-2 font-bold text-white">本地模型管理</h2>
              <p className="mt-1 text-xs leading-5 text-purple-100/70">查看模型清单、硬件建议和本地引擎识别结果。</p>
              <span className="mt-3 inline-block text-xs text-purple-200">管理模型 →</span>
            </a>
            <a href="/help" className="glass rounded-2xl p-4 transition hover:border-purple-200/70">
              <div className="text-2xl">📘</div><h2 className="mt-2 font-bold text-white">完整使用说明书</h2>
              <p className="mt-1 text-xs leading-5 text-purple-100/70">查看每个功能的用途、入口、操作流程与常见问题。</p>
              <span className="mt-3 inline-block text-xs text-purple-200">阅读说明 →</span>
            </a>
          </div>
          <BeginnerQuickStart />

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
                  ["🎥", "本地视频工作台", "/media-studio"],\n                  ["✂️", "智能视频剪辑", "/video-editor"],
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






