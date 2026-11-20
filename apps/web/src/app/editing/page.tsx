"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";
type MusicFile = { id: number; name: string; path: string };
type Shot = { id: number; shot_number: number; duration_seconds: number; scene_name: string; visual_description: string; dialogue: string };
type Transition = { after_shot_id: number; transition: "cut" | "fade" | "dissolve"; duration_seconds: number };
type ShotTrim = { shot_id: number; start_seconds: number; end_seconds: number | null };
type AudioTrack = { file_path: string; start_seconds: number; volume: number; fade_in_seconds: number; fade_out_seconds: number; enabled: boolean };
type Timeline = {
  shot_order: number[]; transitions: Transition[]; shot_trims: ShotTrim[]; audio_tracks: AudioTrack[];
  intro_title: string; outro_title: string; music_fade_in_seconds: number; music_fade_out_seconds: number; music_volume: number;
};
const blank: Timeline = { shot_order: [], transitions: [], shot_trims: [], audio_tracks: [], intro_title: "", outro_title: "", music_fade_in_seconds: 1, music_fade_out_seconds: 2, music_volume: 0.18 };

export default function EpisodeEditingPage() {
  const [episodeId, setEpisodeId] = useState("");
  const [shots, setShots] = useState<Shot[]>([]);
  const [musicFiles, setMusicFiles] = useState<MusicFile[]>([]);
  const [musicPath, setMusicPath] = useState("");
  const [timeline, setTimeline] = useState<Timeline>(blank);
  const [version, setVersion] = useState(0);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [draggedShotId, setDraggedShotId] = useState<number | null>(null);
  const [selectedAudioId, setSelectedAudioId] = useState("");
  const orderedShots = useMemo(() => timeline.shot_order.map(id => shots.find(s => s.id === id)).filter(Boolean) as Shot[], [shots, timeline.shot_order]);

  const load = useCallback(async (id: string) => {
    if (!id || !/^\d+$/.test(id)) { setMessage("请在地址中提供有效的分集编号，例如 /editing?episode_id=1"); return; }
    setBusy(true);
    try {
      const response = await fetch(`${API}/api/media/video/episodes/${id}/timeline`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "读取剪辑时间轴失败");
      setEpisodeId(id); setShots(data.shots || []); setVersion(data.version || 0);
      setTimeline({ ...blank, ...(data.timeline || {}) });
      const musicResponse = await fetch(`${API}/api/media/files?category=audio`);
      if (musicResponse.ok) setMusicFiles(await musicResponse.json());
      setMessage(`已载入：${data.episode_title || "当前分集"} · ${(data.shots || []).length} 个镜头`);
    } catch (e) { setMessage(e instanceof Error ? e.message : "读取失败"); }
    finally { setBusy(false); }
  }, []);

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("episode_id") || "";
    if (id) void load(id);
  }, [load]);

  function move(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= orderedShots.length) return;
    const ids = [...timeline.shot_order];
    [ids[index], ids[target]] = [ids[target], ids[index]];
    setTimeline({ ...timeline, shot_order: ids });
  }
  function reorderShot(sourceId: number, targetId: number) {
    if (sourceId === targetId) return;
    const ids = [...timeline.shot_order];
    const from = ids.indexOf(sourceId);
    const to = ids.indexOf(targetId);
    if (from < 0 || to < 0) return;
    ids.splice(from, 1);
    ids.splice(to, 0, sourceId);
    setTimeline({ ...timeline, shot_order: ids });
  }
  function setShotTrim(shotId: number, field: "start_seconds" | "end_seconds", value: number | null) {
    const current = timeline.shot_trims.find(item => item.shot_id === shotId) || { shot_id: shotId, start_seconds: 0, end_seconds: null };
    const next = { ...current, [field]: value };
    setTimeline({ ...timeline, shot_trims: [...timeline.shot_trims.filter(item => item.shot_id !== shotId), next] });
  }
  function addAudioTrack() {
    const file = musicFiles.find(item => String(item.id) === selectedAudioId);
    if (!file) return;
    if (timeline.audio_tracks.length >= 8) { setMessage("最多支持 8 条独立音轨"); return; }
    setTimeline({ ...timeline, audio_tracks: [...timeline.audio_tracks, { file_path: file.path, start_seconds: 0, volume: 1, fade_in_seconds: 0, fade_out_seconds: 0, enabled: true }] });
    setSelectedAudioId("");
  }
  function updateAudioTrack(index: number, patch: Partial<AudioTrack>) {
    setTimeline({ ...timeline, audio_tracks: timeline.audio_tracks.map((track, i) => i === index ? { ...track, ...patch } : track) });
  }
  function setTransition(shotId: number, kind: Transition["transition"]) {
    const transitions = timeline.transitions.filter(t => t.after_shot_id !== shotId);
    if (kind !== "cut") transitions.push({ after_shot_id: shotId, transition: kind, duration_seconds: 0.5 });
    setTimeline({ ...timeline, transitions });
  }
  async function replaceClip(shotId: number, file?: File) {
    if (!file || !episodeId) return;
    const form = new FormData();
    form.append("file", file);
    setBusy(true); setMessage("正在导入本地替换镜头……");
    try {
      const response = await fetch(`${API}/api/media/video/episodes/${episodeId}/shots/${shotId}/replace`, { method: "POST", body: form });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "镜头替换失败");
      setMessage(data.message || `镜头 ${shotId} 已替换，历史素材仍保留。`);
    } catch (e) { setMessage(e instanceof Error ? e.message : "镜头替换失败"); }
    finally { setBusy(false); }
  }

  async function renderEpisode() {
    if (!episodeId) return;
    setBusy(true); setMessage("正在保存剪辑方案并使用本地 FFmpeg 合成成片……");
    try {
      const saveResponse = await fetch(`${API}/api/media/video/episodes/${episodeId}/timeline`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(timeline)
      });
      const saved = await saveResponse.json();
      if (!saveResponse.ok) throw new Error(saved.detail || "保存剪辑方案失败");
      setVersion(saved.version);
      const musicQuery = musicPath ? `?music_path=${encodeURIComponent(musicPath)}` : "";
      const response = await fetch(`${API}/api/media/video/episodes/${episodeId}/render${musicQuery}`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "成片合成失败");
      setMessage(`成片已生成：${data.output} · ${data.duration_seconds || data.quality_report?.duration_seconds || "时长已检查"} 秒`);
    } catch (e) { setMessage(e instanceof Error ? e.message : "成片合成失败"); }
    finally { setBusy(false); }
  }

  async function save() {
    setBusy(true); setMessage("正在保存本地剪辑方案……");
    try {
      const response = await fetch(`${API}/api/media/video/episodes/${episodeId}/timeline`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(timeline)
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "保存失败");
      setVersion(data.version); setMessage(`剪辑方案已保存（版本 ${data.version}）。原始镜头未被修改。`);
    } catch (e) { setMessage(e instanceof Error ? e.message : "保存失败"); }
    finally { setBusy(false); }
  }

  return <main className="min-h-screen p-6 text-white">
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <h1 className="text-3xl font-bold">分集剪辑工作台</h1>
        <p className="mt-2 text-white/60">调整镜头顺序、设置镜头衔接、片头片尾标题和背景音乐参数。方案保存在本地数据库。</p>
      </header>
      <section className="flex flex-wrap items-end gap-3 rounded-xl border border-purple-300/20 bg-slate-950/60 p-4">
        <label className="min-w-56 flex-1 text-sm">分集编号
          <input value={episodeId} onChange={e => setEpisodeId(e.target.value)} className="mt-2 w-full rounded-lg border border-white/20 bg-black/30 p-3" placeholder="例如 1" />
        </label>
        <button disabled={busy} onClick={() => void load(episodeId)} className="rounded-lg border border-white/20 px-4 py-3 disabled:opacity-50">载入分集</button>
        <button disabled={busy || !shots.length} onClick={() => void save()} className="rounded-lg bg-purple-600 px-5 py-3 font-semibold disabled:opacity-50">保存剪辑方案</button>
        <button disabled={busy || !shots.length} onClick={() => void renderEpisode()} className="rounded-lg border border-emerald-300/40 bg-emerald-700/40 px-5 py-3 font-semibold disabled:opacity-50">按时间轴合成成片</button>
      </section>
      {message && <p role="status" className="rounded-lg border border-purple-300/20 bg-purple-500/10 p-3 text-sm">{message}</p>}
      {shots.length > 0 && <>
        <section className="space-y-3">
          <div className="flex items-center justify-between"><h2 className="text-xl font-semibold">镜头时间轴</h2><span className="text-sm text-white/50">方案版本：{version}</span></div>
          {orderedShots.map((shot, index) => {
            const transition = timeline.transitions.find(t => t.after_shot_id === shot.id);
            const trim = timeline.shot_trims.find(item => item.shot_id === shot.id) || { shot_id: shot.id, start_seconds: 0, end_seconds: null };
            return <article key={shot.id} draggable onDragStart={() => setDraggedShotId(shot.id)} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); if (draggedShotId !== null) reorderShot(draggedShotId, shot.id); setDraggedShotId(null); }} onDragEnd={() => setDraggedShotId(null)} className={`rounded-xl border border-white/15 bg-slate-950/60 p-4 ${draggedShotId === shot.id ? "opacity-50" : ""}`}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div><span className="mr-2 cursor-grab text-white/50" aria-hidden="true">⠿</span><strong>镜头 {shot.shot_number}</strong><span className="ml-3 text-sm text-white/50">{shot.duration_seconds}s · {shot.scene_name || "未命名场景"}</span></div>
                <div className="flex gap-2">
                  <button aria-label="上移镜头" disabled={index === 0} onClick={() => move(index, -1)} className="rounded border border-white/20 px-3 py-1 disabled:opacity-30">上移</button>
                  <button aria-label="下移镜头" disabled={index === orderedShots.length - 1} onClick={() => move(index, 1)} className="rounded border border-white/20 px-3 py-1 disabled:opacity-30">下移</button>
                </div>
              </div>
              <p className="mt-2 text-sm text-white/70">{shot.visual_description}</p>
              <div className="mt-3 grid gap-3 rounded-lg border border-white/10 bg-black/20 p-3 sm:grid-cols-2">
                <label className="text-xs text-white/70">裁剪入点（秒）
                  <input type="number" min="0" max="600" step="0.1" value={trim.start_seconds} onChange={e => setShotTrim(shot.id, "start_seconds", Math.max(0, Number(e.target.value)))} className="mt-1 w-full rounded bg-slate-800 p-2 text-sm text-white" />
                </label>
                <label className="text-xs text-white/70">裁剪出点（秒，留空为素材结尾）
                  <input type="number" min="0.1" max="600" step="0.1" value={trim.end_seconds ?? ""} onChange={e => setShotTrim(shot.id, "end_seconds", e.target.value === "" ? null : Math.max(0.1, Number(e.target.value)))} className="mt-1 w-full rounded bg-slate-800 p-2 text-sm text-white" />
                </label>
              </div>
              <label className="mt-3 inline-flex cursor-pointer items-center rounded border border-white/20 px-3 py-2 text-xs text-white/80">
                导入本地视频替换此镜头
                <input type="file" accept=".mp4,.mov,.mkv,.webm,video/*" disabled={busy} className="ml-2 max-w-48" onChange={e => { const file = e.target.files?.[0]; void replaceClip(shot.id, file); e.currentTarget.value = ""; }} />
              </label>
              {shot.dialogue && <p className="mt-1 text-sm text-white/50">对白：{shot.dialogue}</p>}
              {index < orderedShots.length - 1 && <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-white/10 pt-3 text-sm">
                <label>与下一镜头衔接
                  <select value={transition?.transition || "cut"} onChange={e => setTransition(shot.id, e.target.value as Transition["transition"])} className="ml-2 rounded bg-slate-800 p-2">
                    <option value="cut">直接切镜</option><option value="fade">淡入淡出</option><option value="dissolve">叠化</option>
                  </select>
                </label>
                {transition && <label>转场时长
                  <input type="number" min="0.1" max="2" step="0.1" value={transition.duration_seconds} onChange={e => setTimeline({ ...timeline, transitions: timeline.transitions.map(t => t.after_shot_id === shot.id ? { ...t, duration_seconds: Math.max(0.1, Math.min(2, Number(e.target.value))) } : t) })} className="ml-2 w-20 rounded bg-slate-800 p-2" /> 秒
                </label>}
              </div>}
            </article>;
          })}
        </section>
        <section className="space-y-4 rounded-xl border border-white/15 bg-slate-950/60 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-xl font-semibold">独立音轨</h2><span className="text-xs text-white/50">最多 8 条 · 本地素材</span></div>
          <div className="flex flex-wrap gap-2">
            <select value={selectedAudioId} onChange={e => setSelectedAudioId(e.target.value)} className="min-w-60 flex-1 rounded bg-slate-800 p-3">
              <option value="">选择音频素材</option>
              {musicFiles.map(file => <option key={file.id} value={file.id}>{file.name}</option>)}
            </select>
            <button type="button" onClick={addAudioTrack} disabled={!selectedAudioId || timeline.audio_tracks.length >= 8} className="rounded-lg border border-purple-300/40 px-4 py-2 disabled:opacity-40">添加音轨</button>
          </div>
          {timeline.audio_tracks.map((track, index) => <div key={index} className="grid gap-3 rounded-lg border border-white/10 p-3 md:grid-cols-2">
            <div className="flex items-center justify-between gap-2 md:col-span-2"><strong className="text-sm">音轨 {index + 1} · {musicFiles.find(file => file.path === track.file_path)?.name || "本地音频"}</strong><div className="flex gap-3 text-xs"><label><input type="checkbox" checked={track.enabled} onChange={e => updateAudioTrack(index, { enabled: e.target.checked })} /> 启用</label><button type="button" onClick={() => setTimeline({ ...timeline, audio_tracks: timeline.audio_tracks.filter((_, i) => i !== index) })} className="text-red-300">移除</button></div></div>
            <label className="text-xs text-white/70">起始时间（秒）<input type="number" min="0" max="3600" step="0.1" value={track.start_seconds} onChange={e => updateAudioTrack(index, { start_seconds: Math.max(0, Number(e.target.value)) })} className="mt-1 w-full rounded bg-slate-800 p-2 text-sm" /></label>
            <label className="text-xs text-white/70">音量：{Math.round(track.volume * 100)}%<input type="range" min="0" max="2" step="0.01" value={track.volume} onChange={e => updateAudioTrack(index, { volume: Number(e.target.value) })} className="mt-2 w-full" /></label>
            <label className="text-xs text-white/70">淡入（秒）<input type="number" min="0" max="30" step="0.5" value={track.fade_in_seconds} onChange={e => updateAudioTrack(index, { fade_in_seconds: Math.max(0, Math.min(30, Number(e.target.value))) })} className="mt-1 w-full rounded bg-slate-800 p-2 text-sm" /></label>
            <label className="text-xs text-white/70">淡出（秒）<input type="number" min="0" max="30" step="0.5" value={track.fade_out_seconds} onChange={e => updateAudioTrack(index, { fade_out_seconds: Math.max(0, Math.min(30, Number(e.target.value))) })} className="mt-1 w-full rounded bg-slate-800 p-2 text-sm" /></label>
          </div>)}
          {timeline.audio_tracks.length === 0 && <p className="text-sm text-white/50">尚未添加独立音轨。你仍可使用下方的背景音乐设置。</p>}
        </section>
        <section className="grid gap-4 rounded-xl border border-white/15 bg-slate-950/60 p-4 md:grid-cols-2">
          <label className="text-sm">片头标题<input maxLength={120} value={timeline.intro_title} onChange={e => setTimeline({ ...timeline, intro_title: e.target.value })} className="mt-2 w-full rounded bg-black/30 p-3" placeholder="可留空" /></label>
          <label className="text-sm">片尾标题<input maxLength={120} value={timeline.outro_title} onChange={e => setTimeline({ ...timeline, outro_title: e.target.value })} className="mt-2 w-full rounded bg-black/30 p-3" placeholder="可留空" /></label>
          <label className="text-sm">背景音乐淡入（秒）<input type="number" min="0" max="30" step="0.5" value={timeline.music_fade_in_seconds} onChange={e => setTimeline({ ...timeline, music_fade_in_seconds: Math.max(0, Math.min(30, Number(e.target.value))) })} className="mt-2 w-full rounded bg-black/30 p-3" /></label>
          <label className="text-sm">背景音乐淡出（秒）<input type="number" min="0" max="30" step="0.5" value={timeline.music_fade_out_seconds} onChange={e => setTimeline({ ...timeline, music_fade_out_seconds: Math.max(0, Math.min(30, Number(e.target.value))) })} className="mt-2 w-full rounded bg-black/30 p-3" /></label>
          <label className="text-sm md:col-span-2">背景音乐文件
            <select value={musicPath} onChange={e => setMusicPath(e.target.value)} className="mt-2 w-full rounded bg-slate-800 p-3">
              <option value="">不添加背景音乐</option>
              {musicFiles.map(file => <option key={file.id} value={file.path}>{file.name}</option>)}
            </select>
          </label>
          <label className="text-sm md:col-span-2">背景音乐音量：{Math.round(timeline.music_volume * 100)}%
            <input type="range" min="0" max="1" step="0.01" value={timeline.music_volume} onChange={e => setTimeline({ ...timeline, music_volume: Number(e.target.value) })} className="mt-2 w-full" />
          </label>
        </section>
        <p className="text-xs text-emerald-200/80">本地渲染会应用拖拽后的镜头顺序、镜头裁剪、转场、标题、背景音乐及独立音轨。替换镜头和音轨均只使用本地文件，不调用 AI；重新合成需要手动点击。</p>
      </>}
    </div>
  </main>;
}
