"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type LocalVoice = { name: string; culture: string; gender: string; age: string };

export default function SubtitleStudioPage() {
  const [subtitlePath, setSubtitlePath] = useState("");
  const [videoPath, setVideoPath] = useState("");
  const [provider, setProvider] = useState("deepseek");
  const [model, setModel] = useState("");
  const [voiceModel, setVoiceModel] = useState("");
  const [voice, setVoice] = useState("alloy");
  const [language, setLanguage] = useState("en");
  const [font, setFont] = useState("Arial");
  const [fontSize, setFontSize] = useState(28);
  const [position, setPosition] = useState(2);
  const [confirmPaid, setConfirmPaid] = useState(false);
  const [files, setFiles] = useState<any[]>([]);
  const [videos, setVideos] = useState<any[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [localVoices, setLocalVoices] = useState<LocalVoice[]>([]);
  const [localVoice, setLocalVoice] = useState("");
  const [localRate, setLocalRate] = useState(0);
  const [loadingLocalVoices, setLoadingLocalVoices] = useState(false);
  const [localVoiceResult, setLocalVoiceResult] = useState<{ download_url: string; name: string; duration_seconds: number; cue_count: number } | null>(null);

  async function loadLocalVoices() {
    setLoadingLocalVoices(true);
    try {
      const response = await fetch(`${API}/api/subtitle-tools/local-voices`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "读取本机离线语音失败");
      const voices = Array.isArray(data.voices) ? data.voices : [];
      setLocalVoices(voices);
      setLocalVoice((current) => current || voices.find((item: LocalVoice) => item.culture.toLowerCase().startsWith("zh"))?.name || voices[0]?.name || "");
      setMessage(voices.length ? `检测到 ${voices.length} 个本机离线语音。` : "当前 Windows 未检测到可用的系统语音；可在 Windows 设置中安装中文语音包后重新检测。");
    } catch (error) {
      setLocalVoices([]);
      setMessage(error instanceof Error ? error.message : "读取本机离线语音失败");
    } finally { setLoadingLocalVoices(false); }
  }

  async function generateLocalVoice() {
    if (!subtitlePath || !localVoice) {
      setMessage("请先选择 SRT 字幕和本机离线语音。");
      return;
    }
    if (!window.confirm(`确认使用本机语音“${localVoice}”逐句生成配音？只使用 Windows 已安装语音和本机 CPU，不联网、不调用云端 API，也不会产生 API 费用。`)) return;
    setBusy(true);
    setLocalVoiceResult(null);
    setMessage("正在使用 Windows 本机离线语音生成配音，请保持软件运行……");
    try {
      const response = await fetch(`${API}/api/subtitle-tools/voice-local`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subtitle_path: subtitlePath, voice: localVoice, rate: localRate, confirm_local_generation: true }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "本机离线配音失败");
      setLocalVoiceResult(data);
      setMessage(data.message || "本机离线配音完成。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "本机离线配音失败");
    } finally { setBusy(false); }
  }

  async function loadFiles() {
    const [subs, vids] = await Promise.all([
      fetch(`${API}/api/media/files?category=other`),
      fetch(`${API}/api/media/files?category=video`)
    ]);
    if (subs.ok) setFiles((await subs.json()).filter((x: any) => /\.srt$/i.test(x.name)));
    if (vids.ok) setVideos(await vids.json());
  }

  useEffect(() => { void loadFiles(); void loadLocalVoices(); }, []);

  async function upload(file: File, category: "other" | "video") {
    const form = new FormData();
    form.append("file", file);
    form.append("category", category);
    const response = await fetch(`${API}/api/media/files/upload`, { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "上传失败");
    return data.path;
  }

  async function run(path: string, endpoint: string, body: any, done: (data: any) => void) {
    setBusy(true);
    setMessage("正在处理，请稍候……");
    try {
      const response = await fetch(`${API}${endpoint}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, subtitle_path: path })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "处理失败");
      done(data);
      setMessage(data.message || "处理完成。");
      await loadFiles();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "处理失败");
    } finally { setBusy(false); }
  }

  const panel = "rounded-2xl border border-purple-300/20 bg-slate-950/65 p-6";
  return (
    <main className="cosmic min-h-screen p-5 text-white md:p-8">
      <div className="mx-auto max-w-6xl space-y-7">
        <header>
          <h1 className="text-3xl font-bold">AI 字幕与多语言配音工作室</h1>
          <p className="mt-2 text-white/60">字幕翻译保留原时间轴；逐句配音按时间轴合成；字幕样式可直接烧录到视频。</p>
          <div className="mt-3 flex flex-wrap gap-3 text-sm">
            <a className="rounded-lg border border-purple-300/30 px-3 py-2" href="/video-editor">返回 AI 视频剪辑中心</a>
            <button onClick={() => void loadFiles()} className="rounded-lg border border-purple-300/30 px-3 py-2">刷新素材库</button>
          </div>
        <p className="mt-3 text-xs text-white/55">严格零费用模式：云端字幕翻译与 TTS API 已拦截。字幕翻译可通过网页 AI 工具手动准备；本页已接入 Windows 本机离线配音与 FFmpeg 字幕烧录。</p>
      </header>
        {message && <div className="rounded-xl border border-purple-400/30 bg-purple-500/10 p-4">{message}</div>}
        <section className={panel}>
          <h2 className="mb-4 text-xl font-semibold">素材与 AI 设置</h2>
          <div className="grid gap-4 md:grid-cols-2">
            <label className="text-sm">字幕文件（SRT）
              <select value={subtitlePath} onChange={e => setSubtitlePath(e.target.value)} className="mt-2 w-full rounded-lg border border-white/15 bg-black/30 p-3">
                <option value="">选择字幕文件</option>
                {files.map(f => <option key={f.id} value={f.path}>{f.name}</option>)}
              </select>
            </label>
            <label className="text-sm">视频文件
              <select value={videoPath} onChange={e => setVideoPath(e.target.value)} className="mt-2 w-full rounded-lg border border-white/15 bg-black/30 p-3">
                <option value="">选择需要烧录字幕的视频</option>
                {videos.map(f => <option key={f.id} value={f.path}>{f.name}</option>)}
              </select>
            </label>
            <label className="text-sm">文本 AI 服务商 ID
              <input value={provider} onChange={e => setProvider(e.target.value)} className="mt-2 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            </label>
            <label className="text-sm">文本 AI 模型
              <input value={model} onChange={e => setModel(e.target.value)} placeholder="例如 deepseek-chat" className="mt-2 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            </label>
            <label className="text-sm">语音 AI 模型
              <input value={voiceModel} onChange={e => setVoiceModel(e.target.value)} placeholder="填写已配置的语音模型" className="mt-2 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            </label>
            <label className="text-sm">声音 ID
              <input value={voice} onChange={e => setVoice(e.target.value)} className="mt-2 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            </label>
          </div>
          <label className="mt-4 flex items-center gap-2 text-sm"><input type="checkbox" checked={confirmPaid} onChange={e => setConfirmPaid(e.target.checked)} />我确认本次 AI 调用可能产生费用</label>
          <div className="mt-4 flex flex-wrap gap-3">
            <label className="cursor-pointer rounded-lg border border-dashed border-purple-300/40 px-4 py-3">上传 SRT 字幕
              <input type="file" accept=".srt" className="hidden" onChange={async e => { const f=e.target.files?.[0]; if(f){try{setSubtitlePath(await upload(f,"other"));setMessage("字幕已上传。");await loadFiles();}catch(err){setMessage(err instanceof Error?err.message:"上传失败");}} }} />
            </label>
            <label className="cursor-pointer rounded-lg border border-dashed border-purple-300/40 px-4 py-3">上传视频
              <input type="file" accept="video/*" className="hidden" onChange={async e => { const f=e.target.files?.[0]; if(f){try{setVideoPath(await upload(f,"video"));setMessage("视频已上传。");await loadFiles();}catch(err){setMessage(err instanceof Error?err.message:"上传失败");}} }} />
            </label>
          </div>
          <p className="mt-3 text-xs text-amber-200/70">当前翻译、逐句配音和样式烧录支持 SRT；ASS/SSA/VTT 请先转换为 SRT。云端翻译/API 配音已禁用；本机离线配音只使用 Windows 已安装语音。</p>
        </section>
        <section className={panel}>
          <h2 className="mb-4 text-xl font-semibold">AI 字幕翻译</h2>
          <label className="block text-sm">目标语言</label>
          <select value={language} onChange={e => setLanguage(e.target.value)} className="mt-2 w-full max-w-md rounded-lg border border-white/15 bg-black/30 p-3">
            <option value="zh">翻译为简体中文</option><option value="en">Translate to English</option><option value="zh-en">中英双语（每条两行）</option>
          </select>
          <a href="/ai-tools" className="mt-4 inline-flex rounded-xl border border-purple-300/30 bg-purple-950/40 px-5 py-3 font-semibold text-purple-100">前往网页 AI 工具准备字幕翻译</a>
          <p className="mt-2 text-xs text-white/45">原字幕不会被覆盖；系统会校验翻译条数和编号，保持原开始/结束时间及顺序。</p>
        </section>
        <section className={panel}>
          <h2 className="mb-4 text-xl font-semibold">多语言逐句配音</h2>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="text-sm">Windows 本机离线语音
              <select value={localVoice} onChange={e => setLocalVoice(e.target.value)} disabled={loadingLocalVoices || localVoices.length === 0} className="mt-2 w-full rounded-lg border border-white/15 bg-black/30 p-3">
                <option value="">{loadingLocalVoices ? "正在检测系统语音…" : "请选择已安装的语音"}</option>
                {localVoices.map(item => <option key={item.name} value={item.name}>{item.name}（{item.culture} · {item.gender}）</option>)}
              </select>
            </label>
            <label className="text-sm">语速：{localRate}
              <input type="range" min={-5} max={5} step={1} value={localRate} onChange={e => setLocalRate(Number(e.target.value))} className="mt-4 w-full" />
            </label>
          </div>
          <div className="mt-3 flex flex-wrap gap-3">
            <button type="button" disabled={busy || loadingLocalVoices} onClick={() => void loadLocalVoices()} className="rounded-xl border border-indigo-300/30 px-4 py-3 text-sm">重新检测本机语音</button>
            <button type="button" disabled={busy || !subtitlePath || !localVoice} onClick={() => void generateLocalVoice()} className="rounded-xl bg-indigo-600 px-5 py-3 font-semibold disabled:opacity-40">{busy ? "正在本机配音…" : "生成本机离线配音"}</button>
          </div>
          {localVoiceResult && <div className="mt-3 rounded-xl border border-emerald-300/20 bg-emerald-950/20 p-3 text-sm"><p className="font-semibold text-emerald-200">离线配音已生成：{localVoiceResult.name}</p><p className="mt-1 text-xs text-white/55">字幕 {localVoiceResult.cue_count} 条 · 时长 {localVoiceResult.duration_seconds.toFixed(1)} 秒 · 本机处理、未联网</p><a className="mt-2 inline-flex underline" href={API + localVoiceResult.download_url} target="_blank" rel="noreferrer">播放 / 下载配音文件</a></div>}
          <p className="mt-2 text-xs text-white/45">使用 Windows 已安装的 System.Speech 语音，无需下载模型。可用语音取决于 Windows 已安装的语言包；没有中文语音时，请先在 Windows 设置中添加中文语音后重新检测。系统按字幕时间轴合成音频，过长台词不会被直接截断。</p>
        </section>
        <section className={panel}>
          <h2 className="mb-4 text-xl font-semibold">字幕样式与视频烧录</h2>
          <div className="grid gap-4 md:grid-cols-3">
            <label className="text-sm">字体
              <select value={font} onChange={e=>setFont(e.target.value)} className="mt-2 w-full rounded-lg border border-white/15 bg-black/30 p-3"><option>Arial</option><option>Microsoft YaHei</option><option>SimHei</option><option>Noto Sans CJK SC</option></select>
            </label>
            <label className="text-sm">字号：{fontSize}
              <input type="range" min={12} max={72} value={fontSize} onChange={e=>setFontSize(Number(e.target.value))} className="mt-4 w-full" />
            </label>
            <label className="text-sm">字幕位置
              <select value={position} onChange={e=>setPosition(Number(e.target.value))} className="mt-2 w-full rounded-lg border border-white/15 bg-black/30 p-3"><option value={2}>底部居中</option><option value={8}>顶部居中</option><option value={5}>画面中央</option></select>
            </label>
          </div>
          <button disabled={busy || !subtitlePath || !videoPath} onClick={async()=>{setBusy(true);setMessage("正在使用 FFmpeg 烧录字幕样式……");try{const r=await fetch(`${API}/api/subtitle-tools/render`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({video_path:videoPath,subtitle_path:subtitlePath,font_name:font,font_size:fontSize,alignment:position})});const d=await r.json();if(!r.ok)throw new Error(d.detail||"导出失败");setMessage(`字幕样式成片已导出：${d.path}`);}catch(e){setMessage(e instanceof Error?e.message:"导出失败");}finally{setBusy(false);}}} className="mt-4 rounded-xl bg-cyan-700 px-5 py-3 font-semibold disabled:opacity-40">导出带样式字幕的视频</button>
        </section>
      </div>
    </main>
  );
}
