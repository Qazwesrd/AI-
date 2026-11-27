"use client";

import Link from "next/link";

const sections = [
  { title: "AI 资源包与本地模型配置", icon: "🧭", href: "/setup-wizard", steps: ["从首页打开「AI资源包配置向导」，按步骤检查本地 ComfyUI 图片与视频引擎。", "先完整解压 AI 资源包并保留目录结构；配置前备份现有模型目录，不要覆盖同名文件。", "图片模型按资源说明放入 checkpoints、loras、vae、upscale_models、controlnet 等对应目录；视频模型按当前节点要求放入 diffusion_models、checkpoints、vae、text_encoders 或 clip。", "回到图片模型管理和视频中心重新扫描，核对文件名、类别和节点选择器。", "扫描到文件或节点可见都不代表加载成功；分别进入图片工作台和视频中心做低分辨率、短时长的实际生成测试。"] },
  { title: "从小说开始制作", icon: "📖", href: "/novels", steps: ["进入「小说创作 / 项目管理」，新建项目或选择已有项目。", "导入 TXT 小说，检查章节目录和正文是否完整。", "进入小说深度分析，先分析剧情、人物关系、时间线和世界观。", "确认识别结果后，再进入视觉素材、剧本和分镜制作。"] },
  { title: "人物、场景和道具", icon: "🎭", href: "/visual-studio", steps: ["打开「三维素材生成 / 视觉工作室」。", "选择项目与素材类别，检查 AI 生成提示词。", "选择已配置的图片服务商或使用网页版 AI 交接。", "生成结果导入后先预览、确认，再保存为项目素材。"] },
  { title: "剧本与分镜", icon: "🎞️", href: "/storyboard", steps: ["打开「剧本与分镜」，选择小说和章节。", "检查分集计划、镜头顺序、场景、人物和道具关联。", "按需要合并、拆分或调整镜头；锁定已经确认的素材。", "生成前检查缺失素材和连续性提醒。"] },
  { title: "AI 网页工具协作", icon: "🌐", href: "/ai-tools", steps: ["选择文本、图片或视频任务类别与目标 AI 工具。", "载入小说章节或输入任务要求，生成结构化提示词。", "点击打开网页版工具后，按页面提示手动复制提示词并提交。", "将 AI 返回的文本、JSON 或文件导回软件，确认后归档。"] },
  { title: "视频剪辑与漫剧视频", icon: "✂️", href: "/video-editor", steps: ["进入 AI 视频剪辑，导入原视频或选择项目素材。", "输入剪辑要求，选择字幕、配音和音轨处理方式。", "检查任务交接包或已配置的视频服务商参数，再由你确认提交。", "预览输出，检查字幕、音轨、时长和黑屏/静音提示。"] },
  { title: "统一任务中心", icon: "🧭", href: "/tasks", steps: ["打开统一任务中心查看分析、图片、视频和配音任务。", "使用搜索框、模块和状态筛选定位任务。", "失败任务先阅读错误诊断，再按按钮允许的方式重试。", "状态不确定的付费任务必须先到服务商核实，不能直接重复提交。"] },
  { title: "素材库与剧情状态", icon: "🗂️", href: "/assets", steps: ["在素材库搜索、预览和下载已归档文件。", "在人物与素材状态追踪中查看章节变化和历史版本。", "替换或删除前确认素材是否被分镜或视频引用。", "对重要素材先备份，避免误删造成返工。"] },
  { title: "视频发布中心", icon: "🚀", href: "/publishing", steps: ["进入发布中心，选择成片和目标平台。", "检查画幅、分辨率、时长、封面、标题和描述。", "按页面显示的平台能力准备上传或手动发布。", "发布后记录平台链接和结果；平台规则以当前平台要求为准。"] },
  { title: "备份、恢复和诊断", icon: "💾", href: "/backup-center", steps: ["定期创建数据库备份或完整工作空间归档。", "将备份下载到安全位置；完整归档可能包含本地密钥文件。", "恢复前确认文件来源和目标项目，恢复期间不要关闭软件。", "遇到启动或服务异常时，打开系统运行诊断并查看检查结果。"] },
];

const tips = [
  ["AI 服务设置", "先在 AI 模型管理中配置服务商、模型和密钥；网络开关、付费 API 开关与服务商授权分别管理。软件不会自动购买、充值或替你同意付费。", "/ai-center"],
  ["网页版 AI 的边界", "软件会准备上下文、提示词和结果格式。第三方网页需要你手动粘贴、提交并导入结果；打开网页不代表网页能自动读取本地项目。", "/ai-collaboration"],
  ["安全重试", "遇到提交超时或状态不确定时，先去服务商后台核实是否已接单。尤其是视频和配音等付费任务，不要为避免重复扣费而盲目重试。", "/tasks"],
  ["数据安全", "重要项目完成阶段性制作后，创建完整工作空间归档，并将 ZIP 存放在另一块磁盘或可信备份位置。", "/backup-center"],
];

export default function HelpPage() {
  return (
    <main className="cosmic min-h-screen p-5 text-white md:p-8">
      <div className="mx-auto max-w-6xl space-y-8">
        <header className="rounded-3xl border border-purple-300/20 bg-slate-950/65 p-7">
          <p className="text-xs tracking-[0.3em] text-purple-300">AI COMIC STUDIO · USER GUIDE</p>
          <h1 className="mt-3 text-3xl font-black md:text-4xl">使用说明与快速入门</h1>
          <p className="mt-3 max-w-3xl text-sm leading-7 text-purple-100/75">按实际创作流程查找功能入口、用途和操作步骤。第一次使用建议从小说导入开始，再逐步完成剧情分析、素材确认、剧本分镜、视频制作和发布。</p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Link href="/setup-wizard" className="rounded-xl bg-cyan-700 px-5 py-3 text-sm font-semibold hover:bg-cyan-600">开始配置 AI 资源包</Link>
            <Link href="/novels" className="rounded-xl bg-purple-600 px-5 py-3 text-sm font-semibold hover:bg-purple-500">开始创建项目</Link>
            <Link href="/tasks" className="rounded-xl border border-cyan-300/30 px-5 py-3 text-sm text-cyan-100 hover:bg-cyan-950/40">查看任务中心</Link>
            <Link href="/backup-center" className="rounded-xl border border-white/15 px-5 py-3 text-sm hover:bg-white/5">备份项目数据</Link>
          </div>
        </header>

        <section>
          <div className="mb-4 flex items-end justify-between gap-3">
            <div><h2 className="text-2xl font-bold">功能入口与操作步骤</h2><p className="mt-1 text-sm text-purple-200/60">每张卡片都可以直接打开对应功能。</p></div>
            <span className="text-xs text-purple-300">10 个常用模块</span>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {sections.map((section, index) => (
              <article key={section.href} className="rounded-2xl border border-purple-300/15 bg-slate-950/60 p-5">
                <div className="flex items-center gap-3">
                  <span className="text-3xl">{section.icon}</span>
                  <div className="min-w-0 flex-1"><p className="text-xs text-purple-300">功能 {String(index + 1).padStart(2, "0")}</p><h3 className="text-lg font-bold">{section.title}</h3></div>
                  <Link href={section.href} className="rounded-lg border border-purple-300/25 px-3 py-2 text-xs text-purple-100 hover:bg-purple-900/40">打开功能 ↗</Link>
                </div>
                <ol className="mt-4 space-y-2 border-l border-purple-400/25 pl-5 text-sm leading-6 text-slate-300">
                  {section.steps.map((step, i) => <li key={i} className="list-decimal pl-1">{step}</li>)}
                </ol>
              </article>
            ))}
          </div>
        </section>

        <section className="rounded-2xl border border-cyan-300/15 bg-slate-950/60 p-5">
          <h2 className="text-xl font-bold">常见设置与安全提醒</h2>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {tips.map(([title, body, href]) => (
              <div key={title} className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
                <h3 className="font-semibold text-cyan-100">{title}</h3>
                <p className="mt-2 text-sm leading-6 text-slate-300">{body}</p>
                <Link href={href} className="mt-3 inline-block text-xs text-purple-200 underline underline-offset-4">打开相关页面</Link>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-2xl border border-amber-300/20 bg-amber-950/15 p-5">
          <h2 className="font-bold text-amber-100">遇到问题时按这个顺序处理</h2>
          <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm leading-6 text-slate-300">
            <li>先确认软件窗口正常、网络可用，并打开系统运行诊断。</li>
            <li>到统一任务中心查看具体任务状态和错误提示。</li>
            <li>检查 AI 服务商密钥、模型、网络开关和额度；不要把密钥发给他人。</li>
            <li>如果任务显示“状态需要核实”，先到服务商后台查记录，再决定是否重试。</li>
            <li>不要删除数据库或素材目录；先创建完整工作空间备份，再处理数据问题。</li>
          </ol>
        </section>
        <footer className="pb-10 text-center text-xs text-purple-300/50">AI漫剧制作大师 · 使用说明会随正式测试版本同步更新</footer>
      </div>
    </main>
  );
}
