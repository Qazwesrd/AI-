"use client";

import { useState } from "react";

type Step = { number: string; title: string; description: string; href: string; action: string };

const beginnerSteps: Step[] = [
  { number: "01", title: "导入小说", description: "新建项目，选择小说文件，软件会整理章节目录。", href: "/novels", action: "去导入小说" },
  { number: "02", title: "选择章节并梳理剧情", description: "打开小说分析，选择要制作的章节，检查人物、场景和故事信息。", href: "/ai-analysis", action: "去选择章节" },
  { number: "03", title: "确认画风与素材", description: "选择国漫、仿真人、3D 漫或 2D 漫风格，检查人物、场景和道具素材。", href: "/visual-studio", action: "去管理素材" },
  { number: "04", title: "制作剧本与分镜", description: "检查镜头顺序、对白、人物和场景关联，确认后再进入视频制作。", href: "/storyboard", action: "去制作分镜" },
  { number: "05", title: "合成、质检并导出", description: "制作视频，检查字幕、配音、音画同步和画面质量，再导出成片。", href: "/episode-studio", action: "去检查成片" },
];

const professionalSteps: Step[] = [
  ...beginnerSteps.slice(0, 3),
  { number: "04", title: "分集规划与剧本", description: "调整分集结构、剧情节奏、对白和场景调度。", href: "/storyboard", action: "管理剧本" },
  { number: "05", title: "镜头设计与素材绑定", description: "逐镜头确认景别、运镜、角色状态、道具和连续性。", href: "/storyboard", action: "编辑分镜" },
  { number: "06", title: "视频制作与剪辑", description: "安排镜头视频、配音、字幕、音效、配乐和转场。", href: "/media-studio", action: "进入视频制作" },
  { number: "07", title: "质量检查与导出", description: "运行单集或全项目质检，修复问题后导出成片。", href: "/episode-studio", action: "检查成片" },
];

export default function BeginnerQuickStart() {
  const [mode, setMode] = useState<"beginner" | "professional">("beginner");
  const steps = mode === "beginner" ? beginnerSteps : professionalSteps;

  return (
    <section className="mb-9 rounded-2xl border border-cyan-300/20 bg-slate-950/70 p-5 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs tracking-[0.22em] text-cyan-300">QUICK START · 零基础也能上手</p>
          <h2 className="mt-2 text-xl font-bold text-white md:text-2xl">从小说到漫剧，跟着步骤做</h2>
          <p className="mt-2 text-sm leading-6 text-purple-100/70">先选操作模式。每一步都有明确入口，不需要一次学会全部功能。</p>
        </div>
        <div className="flex rounded-xl border border-white/10 bg-black/20 p-1" aria-label="操作模式">
          <button type="button" aria-pressed={mode === "beginner"} onClick={() => setMode("beginner")} className={`rounded-lg px-3 py-2 text-sm transition ${mode === "beginner" ? "bg-cyan-700 text-white" : "text-slate-300 hover:bg-white/5"}`}>新手模式</button>
          <button type="button" aria-pressed={mode === "professional"} onClick={() => setMode("professional")} className={`rounded-lg px-3 py-2 text-sm transition ${mode === "professional" ? "bg-purple-700 text-white" : "text-slate-300 hover:bg-white/5"}`}>专业模式</button>
        </div>
      </div>

      <ol className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {steps.map((step) => (
          <li key={step.number} className="flex min-w-0 flex-col rounded-xl border border-purple-300/15 bg-white/[0.035] p-4">
            <div className="flex items-center gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-purple-500/20 text-sm font-bold text-purple-100">{step.number}</span>
              <h3 className="font-semibold text-white">{step.title}</h3>
            </div>
            <p className="mt-3 flex-1 text-xs leading-6 text-purple-100/70">{step.description}</p>
            <a href={step.href} className="mt-4 inline-flex w-fit items-center rounded-lg border border-cyan-300/25 px-3 py-2 text-xs text-cyan-100 transition hover:bg-cyan-950/40">{step.action} →</a>
          </li>
        ))}
      </ol>

      <div className="mt-4 rounded-xl border border-amber-300/25 bg-amber-950/20 p-4 text-xs leading-6 text-amber-100/90">
        <strong>零费用使用提醒：</strong>小说导入、文本编辑、素材整理，以及本地 FFmpeg 视频合成和质检可在电脑上完成，不按 AI API 次数收费。第三方 AI 网页、云端模型和在线素材服务可能收费或消耗额度；严格零支出时，请只使用本地功能并保持云端调用关闭。不要提交任何价格、额度或计费方式不明确的任务。
      </div>
      <div className="mt-3 flex flex-wrap gap-3 text-xs">
        <a href="/help" className="text-cyan-200 underline underline-offset-4">查看新手说明</a>
        <a href="/system-diagnostics" className="text-purple-200 underline underline-offset-4">检查电脑运行环境</a>
        <a href="/backup-center" className="text-purple-200 underline underline-offset-4">先备份项目</a>
      </div>
    </section>
  );
}
