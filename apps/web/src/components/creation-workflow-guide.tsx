import Link from "next/link";

const steps = [
  {
    number: "01",
    title: "创建项目并导入小说",
    description: "新建创作项目，导入 TXT，检查章节目录和正文是否完整。",
    href: "/novels",
    action: "导入小说",
  },
  {
    number: "02",
    title: "分析剧情与世界观",
    description: "分析章节、人物关系、时间线和世界观；先检查结果，再进入下一步。",
    href: "/ai-analysis",
    action: "开始分析",
  },
  {
    number: "03",
    title: "确认人物与视觉素材",
    description: "核对人物、场景和道具，选择视觉风格，生成并确认可复用素材。",
    href: "/visual-studio",
    action: "管理视觉素材",
  },
  {
    number: "04",
    title: "改编剧本并制作分镜",
    description: "检查分集规划、镜头顺序、对白和素材关联；按需要调整后再锁定。",
    href: "/storyboard",
    action: "制作分镜",
  },
  {
    number: "05",
    title: "生成与剪辑漫剧视频",
    description: "进入视频制作或剪辑流程，预览画面、字幕、配音和音轨。",
    href: "/media-studio",
    action: "制作视频",
  },
  {
    number: "06",
    title: "检查成片并准备发布",
    description: "检查时长、分辨率、音轨和质量提示，再准备封面与发布文案。",
    href: "/publishing",
    action: "检查与发布",
  },
];

export default function CreationWorkflowGuide() {
  return (
    <section className="mb-9 rounded-3xl border border-purple-300/20 bg-slate-950/65 p-5 md:p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs tracking-[0.25em] text-cyan-300">GUIDED WORKFLOW</p>
          <h2 className="mt-2 text-xl font-bold text-white md:text-2xl">从小说到成片：推荐创作路线</h2>
          <p className="mt-2 text-sm leading-6 text-purple-100/70">按顺序完成六个步骤。每个阶段先检查和确认结果，再进入下一阶段，减少返工。</p>
        </div>
        <Link href="/help" className="rounded-xl border border-cyan-300/30 px-4 py-2 text-xs text-cyan-100 transition hover:bg-cyan-950/40">查看完整使用说明 →</Link>
      </div>

      <ol className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {steps.map((step) => (
          <li key={step.number} className="flex min-w-0 flex-col rounded-2xl border border-purple-300/15 bg-white/[0.035] p-4">
            <div className="flex items-center gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-purple-500/20 text-sm font-bold text-purple-100">{step.number}</span>
              <h3 className="font-semibold text-white">{step.title}</h3>
            </div>
            <p className="mt-3 flex-1 text-xs leading-6 text-purple-100/70">{step.description}</p>
            <Link href={step.href} className="mt-4 inline-flex w-fit items-center rounded-lg border border-purple-300/25 px-3 py-2 text-xs text-purple-100 transition hover:border-purple-200/60 hover:bg-purple-900/40">{step.action} ↗</Link>
          </li>
        ))}
      </ol>

      <div className="mt-4 rounded-xl border border-amber-300/20 bg-amber-950/15 px-4 py-3 text-xs leading-6 text-amber-100/85">
        操作提醒：人物、场景、道具、剧本和分镜请在生成后先人工确认；第三方网页 AI 需要手动提交与导入；任何付费任务都必须由你确认后再执行。
      </div>
    </section>
  );
}
