"use client";

import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type Platform = {
  id: string;
  name: string;
  region: string;
  creator_url: string;
  automatic_publish_enabled: boolean;
};
type Account = {
  id: number;
  platform: string;
  account_name: string;
  enabled: boolean;
  authorized: boolean;
};
type Job = {
  id: number;
  platform: string;
  account_id?: number | null;
  title: string;
  status: string;
  video_path: string;
  publish_url?: string;
  error?: string;
  retry_count: number;
  scheduled_at?: string | null;
};
type Stats = {
  total: number;
  pending: number;
  scheduled: number;
  ready: number;
  published: number;
  failed: number;
  accounts: number;
};
const EMPTY_STATS: Stats = {
  total: 0, pending: 0, scheduled: 0, ready: 0,
  published: 0, failed: 0, accounts: 0
};
const STATUS: Record<string, string> = {
  pending: "等待处理", scheduled: "定时等待", ready: "素材已准备",
  published: "已登记发布", failed: "发布失败"
};

export default function PublishingPage() {
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [stats, setStats] = useState<Stats>(EMPTY_STATS);
  const [selected, setSelected] = useState<string[]>([]);
  const [accountMap, setAccountMap] = useState<Record<string, string>>({});
  const [videoPath, setVideoPath] = useState("");
  const [coverPath, setCoverPath] = useState("");
  const [videoUrl, setVideoUrl] = useState("");
  const [coverUrl, setCoverUrl] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [scheduledAt, setScheduledAt] = useState("");
  const [filter, setFilter] = useState("all");
  const [message, setMessage] = useState("");
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [loading, setLoading] = useState(false);
  const [qualityScan, setQualityScan] = useState<any>(null);
  const [scanningQuality, setScanningQuality] = useState(false);
  const videoInput = useRef<HTMLInputElement>(null);
  const coverInput = useRef<HTMLInputElement>(null);

  async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${API}${path}`, init);
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || `请求失败：${response.status}`);
    return result as T;
  }

  async function loadData() {
    try {
      const [p, a, j, s] = await Promise.all([
        api<{items: Platform[]}>("/api/publishing/platforms"),
        api<{items: Account[]}>("/api/publishing/accounts"),
        api<{items: Job[]}>("/api/publishing/jobs"),
        api<Stats>("/api/publishing/stats")
      ]);
      setPlatforms(p.items || []);
      setAccounts(a.items || []);
      setJobs(j.items || []);
      setStats({ ...EMPTY_STATS, ...s });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "读取发布中心数据失败");
    }
  }

  useEffect(() => {
    void loadData();
    const timer = window.setInterval(() => void loadData(), 20000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => () => {
    if (videoUrl.startsWith("blob:")) URL.revokeObjectURL(videoUrl);
  }, [videoUrl]);
  useEffect(() => () => {
    if (coverUrl.startsWith("blob:")) URL.revokeObjectURL(coverUrl);
  }, [coverUrl]);

  function togglePlatform(id: string) {
    setSelected(current => current.includes(id)
      ? current.filter(item => item !== id) : [...current, id]);
  }

  function uploadFile(file: File, kind: "video" | "cover"): Promise<string> {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append("file", file);
      form.append("kind", kind);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API}/api/publishing/uploads`);
      xhr.upload.onprogress = event => {
        if (event.lengthComputable) setProgress(Math.round(event.loaded / event.total * 100));
      };
      xhr.onload = () => {
        let result: any;
        try { result = JSON.parse(xhr.responseText); }
        catch { reject(new Error("服务器返回的数据无法识别")); return; }
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(new Error(result.detail || "文件上传失败")); return;
        }
        resolve(result.path);
      };
      xhr.onerror = () => reject(new Error("上传连接中断"));
      xhr.onabort = () => reject(new Error("上传已取消"));
      xhr.send(form);
    });
  }

  async function selectAndUpload(event: ChangeEvent<HTMLInputElement>, kind: "video" | "cover") {
    const file = event.target.files?.[0];
    if (!file) return;
    const isVideo = kind === "video";
    const max = isVideo ? 2 * 1024 * 1024 * 1024 : 20 * 1024 * 1024;
    if (file.size > max) {
      setMessage(isVideo ? "视频不能超过 2GB" : "封面不能超过 20MB");
      event.target.value = "";
      return;
    }
    setUploading(true);
    setProgress(0);
    setMessage("");
    const preview = URL.createObjectURL(file);
    if (isVideo) { setVideoPath(""); setVideoUrl(preview); }
    else { setCoverPath(""); setCoverUrl(preview); }
    try {
      const path = await uploadFile(file, kind);
      if (isVideo) setVideoPath(path); else setCoverPath(path);
      setProgress(100);
      setMessage(`${isVideo ? "视频" : "封面"}上传成功：${file.name}`);
    } catch (error) {
      if (isVideo) setVideoPath(""); else setCoverPath("");
      setMessage(error instanceof Error ? error.message : "上传失败");
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }

  async function scanQuality() {
    if (!videoPath) return setMessage("请先上传视频，再进行质量扫描");
    setScanningQuality(true);
    setQualityScan(null);
    setMessage("正在检测视频黑屏和静音片段，长视频可能需要一些时间...");
    try {
      const report = await api<any>(`/api/media/quality-scan?path=${encodeURIComponent(videoPath)}`);
      setQualityScan(report);
      setMessage(report.warnings?.length
        ? `扫描完成：发现 ${report.warnings.length} 项需要人工复核的提示`
        : "扫描完成：没有发现达到检测阈值的持续黑屏或静音片段");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "视频质量扫描失败");
    } finally {
      setScanningQuality(false);
    }
  }

  async function createBatch() {
    if (uploading) return setMessage("文件仍在上传，请等待完成");
    if (!videoPath) return setMessage("请先上传视频");
    if (!title.trim()) return setMessage("请填写视频标题");
    if (!selected.length) return setMessage("请至少选择一个平台");
    if (scheduledAt && new Date(scheduledAt).getTime() <= Date.now()) {
      return setMessage("计划发布时间必须晚于当前时间");
    }
    setLoading(true);
    try {
      const body = selected.map(platform => ({
        platform, video_path: videoPath, cover_path: coverPath || null,
        title: title.trim(), description: description.trim(),
        tags: tags.split(/[,，、\n]/).map(x => x.trim()).filter(Boolean),
        account_id: accountMap[platform] ? Number(accountMap[platform]) : null,
        scheduled_at: scheduledAt ? new Date(scheduledAt).toISOString() : null
      }));
      const result = await api<{created_count: number}>("/api/publishing/jobs/batch", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      setMessage(`成功创建 ${result.created_count} 个任务。任务已保存到本地队列。`);
      setSelected([]);
      await loadData();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建任务失败");
    } finally { setLoading(false); }
  }

  async function prepareJob(id: number) {
    try {
      const result = await api<{creator_url: string}>(`/api/publishing/jobs/${id}/prepare`, { method: "POST" });
      window.open(result.creator_url, "_blank", "noopener,noreferrer");
      setMessage("已打开平台创作者页面。请登录并完成实际发布，再登记发布链接。");
      await loadData();
    } catch (error) { setMessage(error instanceof Error ? error.message : "准备失败"); }
  }
  async function retryJob(id: number) {
    try {
      await api(`/api/publishing/jobs/${id}/retry`, { method: "POST" });
      setMessage("任务已重新加入待处理队列");
      await loadData();
    } catch (error) { setMessage(error instanceof Error ? error.message : "重试失败"); }
  }
  async function completeJob(id: number) {
    const url = window.prompt("请粘贴已经成功发布的视频链接：");
    if (!url) return;
    try {
      await api(`/api/publishing/jobs/${id}/complete?publish_url=${encodeURIComponent(url)}`, { method: "POST" });
      setMessage("发布记录已登记");
      await loadData();
    } catch (error) { setMessage(error instanceof Error ? error.message : "登记失败"); }
  }

  const domestic = platforms.filter(item => item.region !== "海外平台");
  const overseas = platforms.filter(item => item.region === "海外平台");
  const visibleJobs = useMemo(() => filter === "all"
    ? jobs : jobs.filter(job => job.status === filter), [jobs, filter]);

  function renderPlatforms(items: Platform[]) {
    return <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
      {items.map(platform => {
        const checked = selected.includes(platform.id);
        const usableAccounts = accounts.filter(a => a.platform === platform.id && a.enabled);
        return <div key={platform.id} className={`rounded-xl border p-4 ${checked
          ? "border-purple-400 bg-purple-500/20" : "border-white/10 bg-white/5"}`}>
          <button type="button" onClick={() => togglePlatform(platform.id)} className="w-full text-left">
            <div className="font-semibold">{platform.name}</div>
            <div className="mt-2 text-xs text-white/45">{checked ? "✓ 已选择" : "点击选择"}</div>
            <div className="mt-1 text-xs text-yellow-200/60">人工辅助发布</div>
          </button>
          {checked && <label className="block mt-3 text-xs text-white/50">发布账号
            <select value={accountMap[platform.id] || ""}
              onChange={e => setAccountMap(old => ({ ...old, [platform.id]: e.target.value }))}
              className="mt-1 w-full rounded-lg border border-white/15 bg-[#171326] p-2">
              <option value="">不指定账号</option>
              {usableAccounts.map(a => <option key={a.id} value={a.id}>{a.account_name}</option>)}
            </select>
          </label>}
        </div>;
      })}
    </div>;
  }

  return <main className="min-h-screen p-6 md:p-10 text-white">
    <div className="mx-auto max-w-7xl space-y-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-sm text-purple-300 mb-2">多媒体与发布中心</p>
          <h1 className="text-3xl md:text-4xl font-bold">多平台视频发布中心</h1>
          <p className="mt-3 text-white/60">国内外 22 个平台 · 视频素材 · 发布文案 · 任务队列</p>
        </div>
        <a href="/publishing/accounts" className="rounded-xl border border-purple-400/40 px-5 py-3 hover:bg-purple-500/10">账号管理</a>
      </header>

      {message && <div role="status" className="rounded-xl border border-purple-400/30 bg-purple-500/10 p-4 text-sm break-all">{message}</div>}

      <section className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          ["全部任务", stats.total], ["等待处理", stats.pending],
          ["已登记发布", stats.published], ["失败任务", stats.failed]
        ].map(([label, value]) => <div key={String(label)} className="rounded-xl border border-white/10 bg-black/30 p-5">
          <div className="text-sm text-white/50">{label}</div>
          <div className="mt-2 text-3xl font-bold text-purple-200">{value}</div>
        </div>)}
      </section>

      <section className="rounded-2xl border border-white/10 bg-black/30 p-6 space-y-5">
        <div><h2 className="text-xl font-semibold">1. 选择发布平台</h2>
          <p className="mt-2 text-xs text-white/45">自动发布需逐个平台接入官方 API 和授权；当前未接入自动发布的平台均采用人工辅助。</p></div>
        <h3 className="text-sm font-semibold text-purple-200">国内平台</h3>
        {renderPlatforms(domestic)}
        <h3 className="text-sm font-semibold text-purple-200 pt-3">海外平台</h3>
        {renderPlatforms(overseas)}
        <p className="text-sm text-purple-200">已选择 {selected.length} 个平台</p>
      </section>

      <section className="rounded-2xl border border-white/10 bg-black/30 p-6 space-y-5">
        <h2 className="text-xl font-semibold">2. 上传视频和封面</h2>
        <div className="space-y-3">
          <div className="font-medium">视频文件</div>
          <button type="button" onClick={() => videoInput.current?.click()} disabled={uploading}
            className="rounded-xl bg-purple-600 px-6 py-3 hover:bg-purple-500 disabled:opacity-50">选择并上传视频</button>
          <input ref={videoInput} type="file" accept="video/mp4,video/quicktime,video/x-matroska,video/webm,video/x-msvideo"
            className="hidden" onChange={e => void selectAndUpload(e, "video")} />
          {videoPath && <p className="text-xs text-green-300 break-all">视频上传完成</p>}
          {videoUrl && <video src={videoUrl} controls className="w-full max-h-[440px] rounded-xl bg-black" />}
          {videoPath && <div className="space-y-3">
            <button type="button" onClick={() => void scanQuality()} disabled={scanningQuality || uploading}
              className="rounded-xl border border-cyan-300/40 px-5 py-2 text-sm text-cyan-100 hover:bg-cyan-400/10 disabled:opacity-50">
              {scanningQuality ? "正在扫描视频..." : "扫描黑屏与静音"}
            </button>
            {qualityScan && <div className="rounded-xl border border-white/10 bg-white/5 p-4 text-sm space-y-2">
              <div className="font-medium">质量扫描结果</div>
              <div className="text-white/70">持续黑屏：{qualityScan.black_segments?.length || 0} 段；持续静音：{qualityScan.silence_segments?.length || 0} 段</div>
              {qualityScan.audio_video_sync && <div className="rounded-lg border border-white/10 bg-black/20 p-3 space-y-1">
                <div className="font-medium">音视频轨同步检查（FFprobe 元数据）</div>
                <div className={qualityScan.audio_video_sync.status === "warning" ? "text-yellow-200" : "text-white/70"}>
                  {qualityScan.audio_video_sync.status === "warning" ? "发现轨道时间差，建议复核" :
                    qualityScan.audio_video_sync.status === "ok" ? "轨道时长与起始位置在允许误差内" : "无法从文件元数据判断轨道同步"}
                </div>
                <div className="text-xs text-white/55">
                  视频轨：{qualityScan.audio_video_sync.video_duration_seconds ?? "未知"} 秒；
                  音频轨：{qualityScan.audio_video_sync.audio_duration_seconds ?? "未知"} 秒；
                  时长差：{qualityScan.audio_video_sync.duration_difference_seconds ?? "未知"} 秒；
                  起始差：{qualityScan.audio_video_sync.start_offset_seconds ?? "未知"} 秒
                </div>
                <p className="text-xs text-white/40">{qualityScan.audio_video_sync.note}</p>
              </div>}
              {(qualityScan.black_segments || []).map((item: any, index: number) => <div key={`black-${index}`} className="text-yellow-200/80">
                黑屏 {item.start_seconds}s – {item.end_seconds}s（{item.duration_seconds}s）
              </div>)}
              {(qualityScan.silence_segments || []).map((item: any, index: number) => <div key={`silence-${index}`} className="text-yellow-200/80">
                静音 {item.start_seconds}s – {item.end_seconds}s（{item.duration_seconds}s）
              </div>)}
              <p className="text-xs text-white/40">{qualityScan.note}</p>
            </div>}
          </div>}
        </div>
        <div className="border-t border-white/10 pt-5 space-y-3">
          <div className="font-medium">视频封面</div>
          <button type="button" onClick={() => coverInput.current?.click()} disabled={uploading}
            className="rounded-xl border border-purple-400/40 px-6 py-3 hover:bg-purple-500/10 disabled:opacity-50">选择并上传封面</button>
          <input ref={coverInput} type="file" accept="image/png,image/jpeg,image/webp"
            className="hidden" onChange={e => void selectAndUpload(e, "cover")} />
          {coverPath && <p className="text-xs text-green-300">封面上传完成</p>}
          {coverUrl && <img src={coverUrl} alt="封面预览" className="max-h-64 rounded-xl object-contain" />}
        </div>
        {uploading && <div className="space-y-2">
          <div className="flex justify-between text-sm"><span>正在上传</span><span>{progress}%</span></div>
          <div className="h-2 overflow-hidden rounded-full bg-white/10"><div className="h-full bg-purple-500" style={{ width: `${progress}%` }} /></div>
        </div>}
        <p className="text-xs text-white/40">视频最大 2GB；封面最大 20MB。素材保存在本地应用数据目录。</p>
      </section>

      <section className="rounded-2xl border border-white/10 bg-black/30 p-6 space-y-5">
        <h2 className="text-xl font-semibold">3. 发布文案与计划</h2>
        <label className="block space-y-2"><span className="text-sm text-white/70">计划发布时间</span>
          <input type="datetime-local" value={scheduledAt} onChange={e => setScheduledAt(e.target.value)}
            className="w-full rounded-xl border border-white/15 bg-black/40 p-3" />
          <span className="text-xs text-yellow-200/70">目前只保存计划时间并加入队列，不会在指定时间自动上传或发布。</span>
        </label>
        <label className="block space-y-2"><span className="text-sm text-white/70">视频标题</span>
          <input value={title} onChange={e => setTitle(e.target.value)} maxLength={200} placeholder="请输入视频标题"
            className="w-full rounded-xl border border-white/15 bg-black/40 p-3" /></label>
        <label className="block space-y-2"><span className="text-sm text-white/70">视频简介</span>
          <textarea value={description} onChange={e => setDescription(e.target.value)} rows={4} placeholder="请输入视频简介"
            className="w-full rounded-xl border border-white/15 bg-black/40 p-3" /></label>
        <label className="block space-y-2"><span className="text-sm text-white/70">标签（逗号分隔）</span>
          <input value={tags} onChange={e => setTags(e.target.value)} placeholder="AI漫剧, 动漫, 短剧"
            className="w-full rounded-xl border border-white/15 bg-black/40 p-3" /></label>
        <button type="button" disabled={loading || uploading} onClick={() => void createBatch()}
          className="rounded-xl bg-purple-600 px-8 py-3 font-semibold hover:bg-purple-500 disabled:opacity-50">
          {loading ? "正在创建..." : "批量创建发布任务"}
        </button>
      </section>

      <section className="rounded-2xl border border-white/10 bg-black/30 p-6 space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-xl font-semibold">4. 发布任务队列</h2>
          <div className="flex flex-wrap gap-2">
            <select value={filter} onChange={e => setFilter(e.target.value)} className="rounded-lg border border-white/20 bg-[#171326] px-3 py-2 text-sm">
              <option value="all">全部任务</option>
              {Object.entries(STATUS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
            <button type="button" onClick={() => void loadData()} className="rounded-lg border border-white/20 px-4 py-2 text-sm hover:bg-white/10">刷新队列</button>
          </div>
        </div>
        {!visibleJobs.length ? <div className="py-12 text-center text-white/40">暂无符合条件的发布任务</div> :
          <div className="space-y-3">{visibleJobs.map(job => <div key={job.id}
            className="rounded-xl border border-white/10 bg-white/5 p-4 flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div className="min-w-0">
              <div className="font-medium">{platforms.find(p => p.id === job.platform)?.name || job.platform} · {job.title || "未命名视频"}</div>
              <div className="mt-2 text-sm text-purple-300">{STATUS[job.status] || job.status}</div>
              {job.scheduled_at && <div className="mt-1 text-xs text-yellow-200/70">计划时间：{new Date(job.scheduled_at).toLocaleString()}</div>}
              {job.error && <div className="mt-2 text-sm text-red-300">{job.error}</div>}
              <div className="mt-1 text-xs text-white/40">重试次数：{job.retry_count || 0}</div>
            </div>
            <div className="flex flex-wrap gap-2 shrink-0">
              {["pending", "ready"].includes(job.status) && <button type="button" onClick={() => void prepareJob(job.id)}
                className="rounded-lg bg-purple-600 px-4 py-2 text-sm hover:bg-purple-500">前往平台发布</button>}
              {job.status === "ready" && <button type="button" onClick={() => void completeJob(job.id)}
                className="rounded-lg border border-green-400/40 px-4 py-2 text-sm text-green-200">登记已发布</button>}
              {job.status === "failed" && <button type="button" onClick={() => void retryJob(job.id)}
                className="rounded-lg border border-red-400/40 px-4 py-2 text-sm text-red-200">重试任务</button>}
              {job.publish_url && <a href={job.publish_url} target="_blank" rel="noreferrer" className="text-sm text-purple-300 underline self-center">查看发布视频</a>}
            </div>
          </div>)}</div>}
      </section>
      <p className="text-xs text-white/40 leading-6">平台账号记录不等于官方授权。自动发布必须逐个平台接入官方 OAuth、上传 API 和发布权限；当前版本不会虚报发布成功。定时任务目前只保存计划，不会后台自动执行。</p>
    </div>
  </main>;
}
