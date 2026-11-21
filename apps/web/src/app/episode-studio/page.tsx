"use client";

import { useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type ShotItem = {
  shot_id: number;
  shot_number: number;
  scene_name: string;
  visual_description: string;
  planned_duration_seconds: number;
  status: "ready" | "missing" | "processing" | "broken";
  latest_task_id: number | null;
  latest_task_status: string | null;
  selected_task_id: number | null;
  video: null | { duration_seconds: number; width?: number; height?: number; preview_url?: string | null };
  warnings: { task_id: number; reason: string }[];
  error?: string | null;
};

type EpisodeTimeline = {
  shot_order: number[];
  transitions: { after_shot_id: number; transition: "cut" | "fade" | "dissolve"; duration_seconds: number }[];
  intro_title: string;
  outro_title: string;
  music_fade_in_seconds: number;
  music_fade_out_seconds: number;
  music_volume: number;
  music_path?: string | null;
  music_name?: string | null;
  voice_path?: string | null;
  voice_name?: string | null;
  character_voice_profiles: Record<string, string>;
  speaker_by_shot_id: Record<string, number>;
  dialogue_segments_by_shot_id: Record<string, { speaker_character_id: number | null; text: string }[]>;
  subtitle_burn_in: boolean;
  subtitle_font_name: string;
  subtitle_font_size: number;
  subtitle_primary_color: string;
  subtitle_outline_color: string;
  subtitle_outline_width: number;
  subtitle_background: boolean;
  subtitle_alignment: 2 | 5 | 8;
  subtitle_margin_v: number;
  voice_volume: number;
  original_volume: number;
  sound_effect_tracks: { path: string; name: string; start_seconds: number; volume: number }[];
  subtitle_cues?: { start: number; end: number; text: string }[] | null;
};

type EpisodeCharacter = { id: number; name: string };
type EpisodeShot = { id: number; shot_number: number; dialogue?: string; character_ids: number[] };

type Readiness = {
  episode_id: number;
  episode_number?: number;
  episode_title?: string;
  ready: boolean;
  total_shots: number;
  ready_count: number;
  missing_count: number;
  processing_count: number;
  broken_count: number;
  items: ShotItem[];
  message: string;
};

const statusText: Record<ShotItem["status"], string> = {
  ready: "视频可用",
  missing: "缺少视频",
  processing: "仍在生成",
  broken: "文件损坏或丢失",
};

export default function EpisodeReadinessPage() {
  const [episodeId, setEpisodeId] = useState("");
  const [report, setReport] = useState<Readiness | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [replacement, setReplacement] = useState<Record<number, File | null>>({});
  const [renderResult, setRenderResult] = useState<{ output?: string; subtitle_path?: string; duration_target?: number; quality_report?: { duration_seconds?: number; width?: number; height?: number } } | null>(null);
  const [timeline, setTimeline] = useState<EpisodeTimeline | null>(null);
  const [generatedSubtitleCues, setGeneratedSubtitleCues] = useState<{ start: number; end: number; text: string }[]>([]);
  const [timelineVersion, setTimelineVersion] = useState(0);
  const [timelineBusy, setTimelineBusy] = useState(false);
  const [episodeCharacters, setEpisodeCharacters] = useState<EpisodeCharacter[]>([]);
  const [episodeShots, setEpisodeShots] = useState<EpisodeShot[]>([]);
  const [speechProvider, setSpeechProvider] = useState("");
  const [speechModel, setSpeechModel] = useState("");
  const [defaultSpeechVoice, setDefaultSpeechVoice] = useState("alloy");

  async function loadTimeline(id: number, readiness?: Readiness) {
    try {
      const response = await fetch(API + "/api/media/video/episodes/" + id + "/timeline");
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "读取剪辑时间轴失败");
      const saved = data.timeline as EpisodeTimeline;
      saved.character_voice_profiles = saved.character_voice_profiles || {};
      saved.speaker_by_shot_id = saved.speaker_by_shot_id || {};
      saved.dialogue_segments_by_shot_id = saved.dialogue_segments_by_shot_id || {};
      saved.subtitle_burn_in = saved.subtitle_burn_in ?? true;
      saved.subtitle_font_name = saved.subtitle_font_name || "Microsoft YaHei";
      saved.subtitle_font_size = saved.subtitle_font_size || 36;
      saved.subtitle_primary_color = saved.subtitle_primary_color || "#FFFFFF";
      saved.subtitle_outline_color = saved.subtitle_outline_color || "#000000";
      saved.subtitle_outline_width = saved.subtitle_outline_width ?? 2;
      saved.subtitle_background = saved.subtitle_background ?? false;
      saved.subtitle_alignment = saved.subtitle_alignment || 2;
      saved.subtitle_margin_v = saved.subtitle_margin_v ?? 60;
      setEpisodeCharacters(Array.isArray(data.characters) ? data.characters : []);
      setEpisodeShots(Array.isArray(data.shots) ? data.shots : []);
      setGeneratedSubtitleCues(Array.isArray(data.generated_subtitle_cues) ? data.generated_subtitle_cues : []);
      if (saved.subtitle_cues === undefined || saved.subtitle_cues === null) {
        const shotById = new Map<number, { id: number; duration_seconds?: number; dialogue?: string }>(
          (data.shots || []).map((shot: { id: number; duration_seconds?: number; dialogue?: string }) => [shot.id, shot] as const)
        );
        const transitionByShot = new Map<number, { transition: string; duration_seconds: number }>(
          (saved.transitions || []).map(item => [item.after_shot_id, item] as const)
        );
        let elapsed = 0;
        const cues: { start: number; end: number; text: string }[] = [];
        for (const shotId of saved.shot_order || []) {
          const shot = shotById.get(shotId);
          const actualDuration = readiness?.items.find(item => item.shot_id === shotId)?.video?.duration_seconds;
          const duration = Math.max(1, Number(actualDuration || shot?.duration_seconds) || 5);
          const dialogue = (shot?.dialogue || "").trim();
          if (dialogue) cues.push({ start: Number(elapsed.toFixed(2)), end: Number((elapsed + duration).toFixed(2)), text: dialogue });
          const transition = transitionByShot.get(shotId);
          const overlap = transition && transition.transition !== "cut" ? Number(transition.duration_seconds) || 0 : 0;
          elapsed += duration - overlap;
        }
        saved.subtitle_cues = cues;
      }
      setTimeline(saved);
      setTimelineVersion(data.version || 0);
    } catch (error) {
      setTimeline(null);
      setMessage(error instanceof Error ? error.message : "读取剪辑时间轴失败");
    }
  }

  function moveShot(shotId: number, direction: -1 | 1) {
    if (!timeline) return;
    const order = [...timeline.shot_order];
    const index = order.indexOf(shotId);
    const next = index + direction;
    if (index < 0 || next < 0 || next >= order.length) return;
    [order[index], order[next]] = [order[next], order[index]];
    setTimeline({ ...timeline, shot_order: order });
  }

  function setTransition(shotId: number, transition: "cut" | "fade" | "dissolve") {
    if (!timeline) return;
    const index = timeline.shot_order.indexOf(shotId);
    if (index < 0 || index >= timeline.shot_order.length - 1) return;
    const existing = timeline.transitions.filter(item => item.after_shot_id !== shotId);
    if (transition !== "cut") existing.push({ after_shot_id: shotId, transition, duration_seconds: 0.5 });
    setTimeline({ ...timeline, transitions: existing });
  }

  async function saveTimeline() {
    if (!timeline || !report) return;
    setTimelineBusy(true);
    try {
      const response = await fetch(API + "/api/media/video/episodes/" + report.episode_id + "/timeline", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(timeline),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "保存剪辑时间轴失败");
      setTimelineVersion(data.version || timelineVersion + 1);
      setMessage(data.message || "剪辑时间轴已保存。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存剪辑时间轴失败");
    } finally {
      setTimelineBusy(false);
    }
  }

  async function uploadAudioTrack(file: File | null, kind: "voice" | "effect") {
    if (!file || !timeline) return;
    if (!file.type.startsWith("audio/") && !/\.(mp3|wav|m4a|aac|ogg|flac|opus)$/i.test(file.name)) {
      setMessage("请选择有效的音频文件。");
      return;
    }
    setTimelineBusy(true);
    setMessage(kind === "voice" ? "正在保存对白配音轨……" : "正在保存音效素材……");
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch(API + "/api/media/files/upload?category=audio", { method: "POST", body: form });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "音频上传失败");
      if (kind === "voice") {
        setTimeline({ ...timeline, voice_path: data.path, voice_name: data.name });
        setMessage("对白配音已保存。合成时将优先使用此配音轨；没有选择时会沿用已生成的分集配音。");
      } else {
        setTimeline({
          ...timeline,
          sound_effect_tracks: [...(timeline.sound_effect_tracks || []), {
            path: data.path, name: data.name, start_seconds: 0, volume: 1,
          }],
        });
        setMessage("音效已添加到独立音效轨道。可调整起始时间和音量。");
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "音频上传失败");
    } finally {
      setTimelineBusy(false);
    }
  }

  async function generateEpisodeDubbing() {
    if (!report || !timeline) return;
    if (!speechProvider.trim() || !speechModel.trim()) {
      setMessage("请先填写已配置的语音服务商 ID 和模型名称。");
      return;
    }
    const segmentedMap = timeline.dialogue_segments_by_shot_id || {};
    const estimatedRequests = episodeShots.reduce((count, shot) => {
      if (!(shot.dialogue || "").trim()) return count;
      const segments = segmentedMap[String(shot.id)] || [];
      return count + (segments.length || 1);
    }, 0);
    const paidConsent = window.confirm(
      "即将调用所选语音服务商，为本集生成对白配音。预计语音请求约 " + estimatedRequests +
      " 次；拆分对白会按每句分别请求语音服务。实际计费以服务商规则为准，可能消耗额度或产生费用。只有在你已核实价格、额度并明确同意时才继续。是否授权本次配音请求？"
    );
    if (!paidConsent) return;
    setTimelineBusy(true);
    setMessage("正在按角色音色及逐句说话人配置生成分镜对白配音……");
    try {
      const response = await fetch(API + "/api/media/speech/episodes/" + report.episode_id + "/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: speechProvider.trim(),
          model: speechModel.trim(),
          voice: defaultSpeechVoice.trim() || "alloy",
          voice_by_character_id: timeline.character_voice_profiles || {},
          speaker_by_shot_id: timeline.speaker_by_shot_id || {},
          dialogue_segments_by_shot_id: timeline.dialogue_segments_by_shot_id || {},
          confirm_paid: true,
          speed: 1,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "对白配音生成失败");
      setGeneratedSubtitleCues(Array.isArray(data.subtitle_cues) ? data.subtitle_cues : []);
      const voicePath = data.voice_track as string | undefined;
      if (voicePath) {
        const voiceName = voicePath.split(/[\\/]/).pop() || "分集对白配音";
        setTimeline(current => current ? { ...current, voice_path: voicePath, voice_name: voiceName } : current);
      }
      const failureCount = Array.isArray(data.failures) ? data.failures.length : 0;
      const warningCount = Array.isArray(data.warnings) ? data.warnings.length : 0;
      const warningText = failureCount || warningCount
        ? "；失败 " + failureCount + " 项，提醒 " + warningCount + " 条，请查看任务结果"
        : "";
      setMessage((data.message || "对白配音生成完成。") + warningText);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "对白配音生成失败");
    } finally {
      setTimelineBusy(false);
    }
  }

  async function uploadBackgroundMusic(file: File | null) {
    if (!file || !timeline) return;
    if (!file.type.startsWith("audio/") && !/\.(mp3|wav|m4a|aac|ogg|flac|opus)$/i.test(file.name)) {
      setMessage("请选择 MP3、WAV、M4A、AAC、OGG、FLAC 或 OPUS 音频文件。");
      return;
    }
    setTimelineBusy(true);
    setMessage("正在将背景音乐保存到本地音频素材库……");
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch(API + "/api/media/files/upload?category=audio", { method: "POST", body: form });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "背景音乐上传失败");
      setTimeline({ ...timeline, music_path: data.path, music_name: data.name });
      setMessage("背景音乐已保存到本地素材库。保存时间轴后，下一次合成会应用该音乐。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "背景音乐上传失败");
    } finally {
      setTimelineBusy(false);
    }
  }

  function updateSubtitleCue(index: number, patch: Partial<{ start: number; end: number; text: string }>) {
    if (!timeline) return;
    const cues = [...(timeline.subtitle_cues || [])];
    cues[index] = { ...cues[index], ...patch };
    setTimeline({ ...timeline, subtitle_cues: cues });
  }

  async function checkReadiness(clearPreviousRender = true) {
    const id = Number(episodeId);
    if (!Number.isInteger(id) || id <= 0) {
      setMessage("请输入有效的分集 ID（正整数）。");
      return;
    }
    setBusy(true);
    setMessage("正在逐镜头检查视频文件、视频流和时长……");
    if (clearPreviousRender) setRenderResult(null);
    try {
      const response = await fetch(`${API}/api/media/video/episodes/${id}/render-readiness`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "检查失败");
      setReport(data);
      setMessage(data.message);
      await loadTimeline(id, data as Readiness);
    } catch (error) {
      setReport(null);
      setMessage(error instanceof Error ? error.message : "检查失败");
    } finally {
      setBusy(false);
    }
  }

  async function replaceShot(shotId: number) {
    const file = replacement[shotId];
    if (!file || !report) {
      setMessage("请先选择要替换的本地视频文件。");
      return;
    }
    if (!window.confirm(`确认将镜头 #${shotId} 替换为“${file.name}”？旧素材会保留，不会调用 AI 服务或产生 API 费用。`)) return;
    setBusy(true);
    setMessage(`正在验证并保存镜头 #${shotId} 的替换视频……`);
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch(`${API}/api/media/video/episodes/${report.episode_id}/shots/${shotId}/replace`, {
        method: "POST",
        body: form,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "替换失败");
      setMessage(data.message || "镜头替换完成。");
      setReplacement(current => ({ ...current, [shotId]: null }));
      await checkReadiness();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "替换失败");
    } finally {
      setBusy(false);
    }
  }

  async function renderEpisode() {
    if (!report?.ready) {
      setMessage("仍有缺失、生成中或损坏的镜头，不能开始合成。");
      return;
    }
    if (!window.confirm(`确认合成第 ${report.episode_number ?? ""} 集？将使用当前检查通过的镜头视频，并在本地执行 FFmpeg 合成。`)) return;
    setBusy(true);
    setMessage("正在合成分集视频，请保持软件运行……");
    try {
      const response = await fetch(`${API}/api/media/video/episodes/${report.episode_id}/render`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "合成失败");
      setRenderResult(data);
      setMessage("分集成片已生成，并完成基础音视频质量检查。");
      await checkReadiness(false);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "合成失败");
    } finally {
      setBusy(false);
    }
  }

  const panel = "rounded-2xl border border-purple-300/20 bg-slate-950/70 p-5";
  const counts = report ? [
    ["可用", report.ready_count, "text-emerald-300"],
    ["缺失", report.missing_count, "text-amber-300"],
    ["生成中", report.processing_count, "text-sky-300"],
    ["异常", report.broken_count, "text-rose-300"],
  ] as const : [];

  return (
    <main className="cosmic min-h-screen p-5 text-white md:p-8">
      <div className="mx-auto max-w-6xl space-y-6">
        <header>
          <h1 className="text-3xl font-bold">分集成片检查与镜头替换</h1>
          <p className="mt-2 text-sm text-white/60">先检查每个镜头的视频文件和媒体流，再合成 MP4。替换素材只保存在本地，不会触发 AI 生成。</p>
          <a href="/storyboard" className="mt-3 inline-flex rounded-lg border border-white/20 px-4 py-2 text-sm hover:bg-white/10">返回分镜工作台</a>
        </header>

        {message && <div className="rounded-xl border border-purple-300/30 bg-purple-500/10 p-4">{message}</div>}

        <section className={panel}>
          <h2 className="mb-3 text-lg font-semibold">选择要检查的分集</h2>
          <div className="flex flex-wrap gap-3">
            <input value={episodeId} onChange={event => setEpisodeId(event.target.value)} inputMode="numeric" placeholder="输入分集 ID，例如 1" className="min-w-0 flex-1 rounded-lg border border-white/15 bg-black/30 px-3 py-3" />
            <button disabled={busy} onClick={() => void checkReadiness()} className="rounded-lg bg-indigo-600 px-5 py-3 font-semibold disabled:opacity-50">检查成片条件</button>
          </div>
        </section>

        {report && (
          <>
            <section className={panel}>
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <h2 className="text-xl font-semibold">{report.episode_title || `分集 #${report.episode_id}`}</h2>
                  <p className="mt-1 text-sm text-white/55">共 {report.total_shots} 个镜头 · 逐个验证视频文件与可解码视频流</p>
                </div>
                <button disabled={busy || !report.ready} onClick={() => void renderEpisode()} className="rounded-xl bg-emerald-600 px-5 py-3 font-semibold disabled:cursor-not-allowed disabled:opacity-40">合成分集 MP4</button>
              </div>
              <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
                {counts.map(([label, value, color]) => <div key={label} className="rounded-xl bg-black/25 p-4"><div className={`text-2xl font-bold ${color}`}>{value}</div><div className="mt-1 text-sm text-white/55">{label}</div></div>)}
              </div>
              {!report.ready && <p className="mt-4 text-sm text-amber-200">请先处理下方未通过检查的镜头。已有历史有效视频会保留，并优先选择可正常读取的版本。</p>}
            </section>

            {timeline && (
              <section className={panel}>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h2 className="text-xl font-semibold">分集剪辑时间轴</h2>
                    <p className="mt-1 text-xs text-white/50">本地保存 · 版本 {timelineVersion} · 不调用 AI 服务</p>
                  </div>
                  <button disabled={timelineBusy || busy} onClick={() => void saveTimeline()} className="rounded-lg bg-cyan-700 px-4 py-2 text-sm font-semibold disabled:opacity-50">{timelineBusy ? "保存中…" : "保存时间轴"}</button>
                </div>
                <div className="mt-4 grid gap-3 md:grid-cols-2">
                  <label className="text-sm text-white/65">片头文字
                    <input value={timeline.intro_title} maxLength={120} onChange={event => setTimeline({ ...timeline, intro_title: event.target.value })} className="mt-1 w-full rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-white" placeholder="例如：第一集 · 初遇" />
                  </label>
                  <label className="text-sm text-white/65">片尾文字
                    <input value={timeline.outro_title} maxLength={120} onChange={event => setTimeline({ ...timeline, outro_title: event.target.value })} className="mt-1 w-full rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-white" placeholder="例如：未完待续" />
                  </label>
                </div>
                <div className="mt-4 grid gap-3 md:grid-cols-3">
                  <label className="text-sm text-white/65">背景音乐音量：{timeline.music_volume.toFixed(2)}
                    <input type="range" min="0" max="1" step="0.01" value={timeline.music_volume} onChange={event => setTimeline({ ...timeline, music_volume: Number(event.target.value) })} className="mt-2 w-full" />
                  </label>
                  <label className="text-sm text-white/65">音乐淡入（秒）
                    <input type="number" min="0" max="30" step="0.5" value={timeline.music_fade_in_seconds} onChange={event => setTimeline({ ...timeline, music_fade_in_seconds: Number(event.target.value) })} className="mt-1 w-full rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-white" />
                  </label>
                  <label className="text-sm text-white/65">音乐淡出（秒）
                    <input type="number" min="0" max="30" step="0.5" value={timeline.music_fade_out_seconds} onChange={event => setTimeline({ ...timeline, music_fade_out_seconds: Number(event.target.value) })} className="mt-1 w-full rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-white" />
                  </label>
                </div>
                <div className="mt-5 rounded-xl border border-white/10 bg-black/20 p-4">
                  <h3 className="font-semibold">对白配音轨道</h3>
                  <p className="mt-1 text-xs text-white/45">可上传已有的整集配音音频；未指定时，合成会自动使用已生成的分集对白配音。这里仅导入本地音频，不会请求 AI 生成或产生 API 费用。</p>
                  {timeline.voice_name && <p className="mt-2 break-all text-sm text-emerald-200">当前配音：{timeline.voice_name}</p>}
                  <div className="mt-3 grid gap-3 md:grid-cols-2">
                    <label className="text-xs text-white/60">语音服务商 ID
                      <input value={speechProvider} onChange={event => setSpeechProvider(event.target.value)} placeholder="填写已配置的服务商 ID" className="mt-1 w-full rounded border border-white/15 bg-slate-900 px-3 py-2 text-white" />
                    </label>
                    <label className="text-xs text-white/60">语音模型名称
                      <input value={speechModel} onChange={event => setSpeechModel(event.target.value)} placeholder="填写已配置的模型名称" className="mt-1 w-full rounded border border-white/15 bg-slate-900 px-3 py-2 text-white" />
                    </label>
                  </div>
                  <label className="mt-3 block text-xs text-white/60">默认音色（角色未单独设置时使用）
                    <input value={defaultSpeechVoice} onChange={event => setDefaultSpeechVoice(event.target.value)} maxLength={100} className="mt-1 w-full rounded border border-white/15 bg-slate-900 px-3 py-2 text-white" />
                  </label>
                  {episodeCharacters.length > 0 && (
                    <div className="mt-4 rounded-lg border border-white/10 p-3">
                      <h4 className="text-sm font-semibold">角色专属音色</h4>
                      <p className="mt-1 text-xs text-white/45">按角色素材 ID 配置语音服务商支持的 voice 名称。单角色镜头自动采用对应音色；多角色镜头请在下方明确选择实际说话人，避免把对白分配给错误角色。</p>
                      <div className="mt-3 grid gap-3 md:grid-cols-2">
                        {episodeCharacters.map(character => (
                          <label key={character.id} className="text-xs text-white/65">{character.name}
                            <input
                              value={timeline.character_voice_profiles?.[String(character.id)] || ""}
                              onChange={event => setTimeline({
                                ...timeline,
                                character_voice_profiles: {
                                  ...(timeline.character_voice_profiles || {}),
                                  [String(character.id)]: event.target.value,
                                },
                              })}
                              maxLength={100}
                              placeholder="留空则使用默认音色"
                              className="mt-1 w-full rounded border border-white/15 bg-slate-900 px-3 py-2 text-white"
                            />
                          </label>
                        ))}
                      </div>
                    </div>
                  )}
                  {episodeShots.some(shot => (shot.character_ids || []).length > 1) && (
                    <div className="mt-4 rounded-lg border border-amber-300/20 p-3">
                      <h4 className="text-sm font-semibold">多角色镜头说话人指定</h4>
                      <p className="mt-1 text-xs text-white/45">每个镜头选择实际说话角色；未选择时将使用默认音色。此设置不会自动推断台词中的说话人。</p>
                      <div className="mt-3 space-y-3">
                        {episodeShots.filter(shot => (shot.character_ids || []).length > 1 && (shot.dialogue || "").trim()).map(shot => (
                          <label key={shot.id} className="block text-xs text-white/65">
                            <span className="font-semibold">镜头 {shot.shot_number}</span>
                            <span className="ml-2 text-white/45">{shot.dialogue}</span>
                            <select
                              value={timeline.speaker_by_shot_id?.[String(shot.id)] ?? ""}
                              onChange={event => setTimeline({
                                ...timeline,
                                speaker_by_shot_id: {
                                  ...(timeline.speaker_by_shot_id || {}),
                                  ...(event.target.value ? { [String(shot.id)]: Number(event.target.value) } : (() => { const next = { ...(timeline.speaker_by_shot_id || {}) }; delete next[String(shot.id)]; return next; })()),
                                },
                              })}
                              className="mt-2 w-full rounded border border-white/15 bg-slate-900 px-3 py-2 text-white"
                            >
                              <option value="">未指定（使用默认音色）</option>
                              {episodeCharacters.filter(character => (shot.character_ids || []).includes(character.id)).map(character => (
                                <option key={character.id} value={character.id}>{character.name}</option>
                              ))}
                            </select>
                          </label>
                        ))}
                      </div>
                    </div>
                  )}
                  <div className="mt-4 rounded-lg border border-white/10 p-3">
                    <h4 className="text-sm font-semibold">多角色对白逐句拆分</h4>
                    <p className="mt-1 text-xs text-white/45">将同一镜头的对白拆成多句，并为每句指定说话角色。每句单独生成配音，再按顺序拼接；未拆分的镜头继续沿用原有单句配音流程。每句会产生独立语音请求。</p>
                    <div className="mt-3 space-y-3">
                      {episodeShots.filter(shot => (shot.character_ids || []).length > 1).map(shot => {
                        const segments = timeline.dialogue_segments_by_shot_id?.[String(shot.id)] || [];
                        const allowedCharacters = episodeCharacters.filter(character => shot.character_ids.includes(character.id));
                        return (
                          <div key={shot.id} className="rounded-lg border border-white/10 p-3">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <div className="text-sm font-medium">镜头 {shot.shot_number} · 多角色</div>
                              <button
                                type="button"
                                onClick={() => setTimeline({
                                  ...timeline,
                                  dialogue_segments_by_shot_id: {
                                    ...(timeline.dialogue_segments_by_shot_id || {}),
                                    [String(shot.id)]: segments.length
                                      ? [...segments, { speaker_character_id: allowedCharacters[0]?.id ?? null, text: "" }]
                                      : [{ speaker_character_id: null, text: shot.dialogue || "" }],
                                  },
                                })}
                                className="rounded border border-cyan-300/30 px-2 py-1 text-xs text-cyan-100"
                              >{segments.length ? "添加一句对白" : "拆分原对白"}</button>
                            </div>
                            {!segments.length && <p className="mt-2 text-xs text-white/40">当前未拆分，生成时会沿用原对白并使用默认音色。点击“拆分原对白”开始逐句设置。</p>}
                            {segments.map((segment, index) => (
                              <div key={index} className="mt-2 grid gap-2 md:grid-cols-[180px_1fr_auto]">
                                <label className="text-xs text-white/60">说话角色
                                  <select
                                    value={segment.speaker_character_id ?? ""}
                                    onChange={event => setTimeline({
                                      ...timeline,
                                      dialogue_segments_by_shot_id: {
                                        ...(timeline.dialogue_segments_by_shot_id || {}),
                                        [String(shot.id)]: segments.map((item, i) => i === index ? { ...item, speaker_character_id: event.target.value ? Number(event.target.value) : null } : item),
                                      },
                                    })}
                                    className="mt-1 w-full rounded border border-white/15 bg-slate-900 px-2 py-2 text-white"
                                  >
                                    <option value="">选择角色</option>
                                    {allowedCharacters.map(character => <option key={character.id} value={character.id}>{character.name}</option>)}
                                  </select>
                                </label>
                                <label className="text-xs text-white/60">对白内容
                                  <textarea
                                    value={segment.text}
                                    maxLength={2000}
                                    rows={2}
                                    onChange={event => setTimeline({
                                      ...timeline,
                                      dialogue_segments_by_shot_id: {
                                        ...(timeline.dialogue_segments_by_shot_id || {}),
                                        [String(shot.id)]: segments.map((item, i) => i === index ? { ...item, text: event.target.value } : item),
                                      },
                                    })}
                                    className="mt-1 w-full rounded border border-white/15 bg-slate-900 px-2 py-2 text-white"
                                  />
                                </label>
                                <button
                                  type="button"
                                  onClick={() => setTimeline({
                                    ...timeline,
                                    dialogue_segments_by_shot_id: {
                                      ...(timeline.dialogue_segments_by_shot_id || {}),
                                      [String(shot.id)]: segments.filter((_, i) => i !== index),
                                    },
                                  })}
                                  className="self-end rounded border border-rose-300/30 px-3 py-2 text-xs text-rose-200"
                                >删除</button>
                              </div>
                            ))}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                  <button type="button" disabled={timelineBusy || busy || !speechProvider.trim() || !speechModel.trim()} onClick={() => void generateEpisodeDubbing()} className="mt-3 rounded-lg bg-violet-700 px-4 py-2 text-sm font-semibold disabled:opacity-40">生成本集对白配音（需逐次确认费用）</button>
                  <input type="file" accept="audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac,.opus" disabled={timelineBusy || busy} onChange={event => void uploadAudioTrack(event.target.files?.[0] || null, "voice")} className="mt-3 block w-full text-xs" />
                  {timeline.voice_path && <button type="button" onClick={() => setTimeline({ ...timeline, voice_path: null, voice_name: null })} className="mt-2 rounded border border-white/15 px-3 py-1 text-xs">移除自定义配音（恢复使用已生成配音）</button>}
                  <div className="mt-4 grid gap-3 md:grid-cols-2">
                    <label className="text-sm text-white/65">对白配音音量：{(timeline.voice_volume ?? 1).toFixed(2)}
                      <input type="range" min="0" max="2" step="0.01" value={timeline.voice_volume ?? 1} onChange={event => setTimeline({ ...timeline, voice_volume: Number(event.target.value) })} className="mt-2 w-full" />
                    </label>
                    <label className="text-sm text-white/65">镜头原声音量：{(timeline.original_volume ?? 0.35).toFixed(2)}
                      <input type="range" min="0" max="2" step="0.01" value={timeline.original_volume ?? 0.35} onChange={event => setTimeline({ ...timeline, original_volume: Number(event.target.value) })} className="mt-2 w-full" />
                    </label>
                  </div>
                </div>
                <div className="mt-5 rounded-xl border border-white/10 bg-black/20 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <h3 className="font-semibold">独立音效轨道</h3>
                      <p className="mt-1 text-xs text-white/45">可添加多个音效文件，并分别设置在成片中的起始秒数和音量。</p>
                    </div>
                    <label className="cursor-pointer rounded-lg border border-amber-300/30 px-3 py-2 text-xs text-amber-100">
                      添加音效
                      <input type="file" accept="audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac,.opus" disabled={timelineBusy || busy} onChange={event => void uploadAudioTrack(event.target.files?.[0] || null, "effect")} className="hidden" />
                    </label>
                  </div>
                  <div className="mt-3 space-y-3">
                    {(timeline.sound_effect_tracks || []).map((effect, index) => (
                      <div key={effect.path + "-" + index} className="grid gap-2 rounded-lg border border-white/10 p-3 md:grid-cols-[1fr_120px_1fr_auto]">
                        <div className="min-w-0 break-all text-sm">{effect.name || ("音效 " + (index + 1))}</div>
                        <label className="text-xs text-white/55">起始时间（秒）
                          <input type="number" min="0" max="900" step="0.05" value={effect.start_seconds} onChange={event => setTimeline({ ...timeline, sound_effect_tracks: timeline.sound_effect_tracks.map((item, i) => i === index ? { ...item, start_seconds: Number(event.target.value) } : item) })} className="mt-1 w-full rounded border border-white/15 bg-slate-900 px-2 py-2 text-white" />
                        </label>
                        <label className="text-xs text-white/55">音量：{effect.volume.toFixed(2)}
                          <input type="range" min="0" max="2" step="0.01" value={effect.volume} onChange={event => setTimeline({ ...timeline, sound_effect_tracks: timeline.sound_effect_tracks.map((item, i) => i === index ? { ...item, volume: Number(event.target.value) } : item) })} className="mt-2 w-full" />
                        </label>
                        <button type="button" onClick={() => setTimeline({ ...timeline, sound_effect_tracks: timeline.sound_effect_tracks.filter((_, i) => i !== index) })} className="self-end rounded border border-rose-300/30 px-3 py-2 text-xs text-rose-200">删除</button>
                      </div>
                    ))}
                    {!(timeline.sound_effect_tracks || []).length && <p className="text-xs text-white/45">尚未添加音效文件。</p>}
                  </div>
                </div>
                <div className="mt-5 rounded-xl border border-white/10 bg-black/20 p-4">
                  <h3 className="font-semibold">背景音乐轨道</h3>
                  <p className="mt-1 text-xs text-white/45">选择本地音频文件；上传只保存到素材库，不会调用 AI 或产生 API 费用。</p>
                  {timeline.music_name && <p className="mt-2 break-all text-sm text-emerald-200">当前音乐：{timeline.music_name}</p>}
                  <input type="file" accept="audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac,.opus" disabled={timelineBusy || busy} onChange={event => void uploadBackgroundMusic(event.target.files?.[0] || null)} className="mt-3 block w-full text-xs" />
                  {timeline.music_path && <button type="button" onClick={() => setTimeline({ ...timeline, music_path: null, music_name: null })} className="mt-2 rounded border border-white/15 px-3 py-1 text-xs">移除背景音乐</button>}
                </div>
                <div className="mt-5 rounded-xl border border-white/10 bg-black/20 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <h3 className="font-semibold">对白字幕时间轴</h3>
                      <p className="mt-1 text-xs text-white/45">可调整每条字幕的开始/结束时间和文本；保存后由本地 FFmpeg 生成 SRT。</p>
                      {generatedSubtitleCues.length > 0 && (
                        <p className="mt-1 text-xs text-emerald-200">已根据实际逐句配音时长计算 {generatedSubtitleCues.length} 条字幕建议；尚未覆盖当前字幕。</p>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        disabled={!generatedSubtitleCues.length}
                        onClick={() => {
                          const confirmed = window.confirm(
                            "将用最新配音的实际音频时长替换当前字幕时间轴吗？当前手动字幕会被替换；取消则保留原字幕不变。"
                          );
                          if (confirmed) {
                            setTimeline({ ...timeline, subtitle_cues: generatedSubtitleCues.map(cue => ({ ...cue })) });
                            setMessage("已应用按实际配音时长计算的字幕建议；你仍可逐条手动微调。");
                          }
                        }}
                        className="rounded-lg border border-emerald-300/30 px-3 py-2 text-xs text-emerald-100 disabled:opacity-40"
                      >应用配音时间</button>
                      <button type="button" onClick={() => setTimeline({ ...timeline, subtitle_cues: [...(timeline.subtitle_cues || []), { start: 0, end: 2, text: "" }] })} className="rounded-lg border border-cyan-300/30 px-3 py-2 text-xs text-cyan-100">添加字幕</button>
                    </div>
                  </div>
                  <div className="mt-4 rounded-lg border border-white/10 p-3">
                    <label className="flex items-center gap-2 text-sm">
                      <input type="checkbox" checked={timeline.subtitle_burn_in} onChange={event => setTimeline({ ...timeline, subtitle_burn_in: event.target.checked })} />
                      将字幕直接烧录进最终 MP4（关闭则只生成独立 SRT 文件）
                    </label>
                    <div className="mt-3 grid gap-3 md:grid-cols-2">
                      <label className="text-xs text-white/60">字体名称
                        <input value={timeline.subtitle_font_name} maxLength={80} onChange={event => setTimeline({ ...timeline, subtitle_font_name: event.target.value })} className="mt-1 w-full rounded border border-white/15 bg-slate-900 px-3 py-2 text-white" />
                      </label>
                      <label className="text-xs text-white/60">字号：{timeline.subtitle_font_size}
                        <input type="range" min="16" max="72" value={timeline.subtitle_font_size} onChange={event => setTimeline({ ...timeline, subtitle_font_size: Number(event.target.value) })} className="mt-2 w-full" />
                      </label>
                      <label className="text-xs text-white/60">字幕文字颜色
                        <input type="color" value={timeline.subtitle_primary_color} onChange={event => setTimeline({ ...timeline, subtitle_primary_color: event.target.value })} className="mt-1 h-10 w-full rounded border border-white/15 bg-slate-900 p-1" />
                      </label>
                      <label className="text-xs text-white/60">描边颜色
                        <input type="color" value={timeline.subtitle_outline_color} onChange={event => setTimeline({ ...timeline, subtitle_outline_color: event.target.value })} className="mt-1 h-10 w-full rounded border border-white/15 bg-slate-900 p-1" />
                      </label>
                      <label className="text-xs text-white/60">描边宽度：{timeline.subtitle_outline_width.toFixed(1)}
                        <input type="range" min="0" max="8" step="0.5" value={timeline.subtitle_outline_width} onChange={event => setTimeline({ ...timeline, subtitle_outline_width: Number(event.target.value) })} className="mt-2 w-full" />
                      </label>
                      <label className="text-xs text-white/60">垂直边距：{timeline.subtitle_margin_v}
                        <input type="range" min="0" max="200" value={timeline.subtitle_margin_v} onChange={event => setTimeline({ ...timeline, subtitle_margin_v: Number(event.target.value) })} className="mt-2 w-full" />
                      </label>
                      <label className="text-xs text-white/60">字幕位置
                        <select value={timeline.subtitle_alignment} onChange={event => setTimeline({ ...timeline, subtitle_alignment: Number(event.target.value) as 2 | 5 | 8 })} className="mt-1 w-full rounded border border-white/15 bg-slate-900 px-3 py-2 text-white">
                          <option value={2}>底部居中</option>
                          <option value={5}>画面居中</option>
                          <option value={8}>顶部居中</option>
                        </select>
                      </label>
                      <label className="flex items-center gap-2 self-end text-xs text-white/60">
                        <input type="checkbox" checked={timeline.subtitle_background} onChange={event => setTimeline({ ...timeline, subtitle_background: event.target.checked })} />
                        启用半透明字幕底板
                      </label>
                    </div>
                    <p className="mt-2 text-xs text-white/40">建议使用系统已安装的字体；最终 MP4 会重新编码视频以烧录字幕，音频保持原轨。</p>
                  </div>
                  <div className="mt-3 space-y-3">
                    {(timeline.subtitle_cues || []).map((cue, index) => (
                      <div key={index} className="grid gap-2 rounded-lg border border-white/10 p-3 md:grid-cols-[110px_110px_1fr_auto]">
                        <label className="text-xs text-white/55">开始（秒）
                          <input type="number" min="0" step="0.05" value={cue.start} onChange={event => updateSubtitleCue(index, { start: Number(event.target.value) })} className="mt-1 w-full rounded border border-white/15 bg-slate-900 px-2 py-2 text-white" />
                        </label>
                        <label className="text-xs text-white/55">结束（秒）
                          <input type="number" min="0.01" step="0.05" value={cue.end} onChange={event => updateSubtitleCue(index, { end: Number(event.target.value) })} className="mt-1 w-full rounded border border-white/15 bg-slate-900 px-2 py-2 text-white" />
                        </label>
                        <label className="text-xs text-white/55">字幕内容
                          <textarea value={cue.text} maxLength={2000} rows={2} onChange={event => updateSubtitleCue(index, { text: event.target.value })} className="mt-1 w-full rounded border border-white/15 bg-slate-900 px-2 py-2 text-white" />
                        </label>
                        <button type="button" onClick={() => setTimeline({ ...timeline, subtitle_cues: (timeline.subtitle_cues || []).filter((_, cueIndex) => cueIndex !== index) })} className="self-end rounded border border-rose-300/30 px-3 py-2 text-xs text-rose-200">删除</button>
                      </div>
                    ))}
                    {!(timeline.subtitle_cues || []).length && <p className="text-xs text-white/45">当前没有字幕条目。可以添加字幕，或保留空白以生成无对白字幕的成片。</p>}
                  </div>
                </div>
                <h3 className="mt-5 font-semibold">镜头顺序与转场</h3>
                <div className="mt-3 space-y-2">
                  {timeline.shot_order.map((shotId, index) => {
                    const shot = report.items.find(item => item.shot_id === shotId);
                    const transition = timeline.transitions.find(item => item.after_shot_id === shotId);
                    return (
                      <div key={shotId} className="flex flex-wrap items-center gap-2 rounded-xl border border-white/10 bg-black/20 p-3">
                        <div className="min-w-0 flex-1">
                          <div className="font-medium">顺序 {index + 1} · 镜头 {shot?.shot_number ?? shotId}</div>
                          <div className="truncate text-xs text-white/50">{shot?.scene_name || "未命名场景"}</div>
                        </div>
                        <button disabled={index === 0} onClick={() => moveShot(shotId, -1)} className="rounded border border-white/15 px-2 py-1 text-sm disabled:opacity-30" aria-label="上移镜头">↑</button>
                        <button disabled={index === timeline.shot_order.length - 1} onClick={() => moveShot(shotId, 1)} className="rounded border border-white/15 px-2 py-1 text-sm disabled:opacity-30" aria-label="下移镜头">↓</button>
                        {index < timeline.shot_order.length - 1 && (
                          <select value={transition?.transition || "cut"} onChange={event => setTransition(shotId, event.target.value as "cut" | "fade" | "dissolve")} className="rounded border border-white/15 bg-slate-900 px-2 py-2 text-sm">
                            <option value="cut">直接切镜</option>
                            <option value="fade">淡入淡出</option>
                            <option value="dissolve">叠化</option>
                          </select>
                        )}
                      </div>
                    );
                  })}
                </div>
                <p className="mt-3 text-xs text-white/45">保存后，下一次合成才会应用。音乐参数仅作用于已配置的背景音乐轨道。</p>
              </section>
            )}

            <section className="space-y-3">
              {report.items.map(item => (
                <article key={item.shot_id} className={panel}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h3 className="font-semibold">镜头 {item.shot_number} <span className="ml-2 text-xs text-white/45">ID {item.shot_id}</span></h3>
                      <p className="mt-1 text-sm text-white/55">{item.scene_name || "未命名场景"} · 计划 {item.planned_duration_seconds}s</p>
                    </div>
                    <span className={`rounded-full px-3 py-1 text-xs ${item.status === "ready" ? "bg-emerald-500/15 text-emerald-300" : item.status === "processing" ? "bg-sky-500/15 text-sky-300" : item.status === "broken" ? "bg-rose-500/15 text-rose-300" : "bg-amber-500/15 text-amber-300"}`}>{statusText[item.status]}</span>
                  </div>
                  {item.visual_description && <p className="mt-3 text-sm text-white/70">{item.visual_description}</p>}
                  {item.video && <div className="mt-3 flex flex-wrap items-center gap-3 text-sm text-emerald-200"><span>{item.video.duration_seconds}s</span><span>{item.video.width || "?"} × {item.video.height || "?"}</span>{item.video.preview_url && <a className="underline" href={`${API}${item.video.preview_url}`} target="_blank" rel="noreferrer">打开当前视频</a>}</div>}
                  {item.warnings.map(warning => <p key={warning.task_id} className="mt-2 text-xs text-rose-300">历史任务 #{warning.task_id}：{warning.reason}</p>)}
                  {item.error && <p className="mt-2 text-xs text-rose-300">{item.error}</p>}
                  {item.status !== "ready" && (
                    <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-white/10 pt-4">
                      <label className="min-w-0 flex-1 text-sm text-white/65">选择本地替换视频
                        <input type="file" accept=".mp4,.mov,.mkv,.webm,video/*" className="mt-2 block w-full text-xs" onChange={event => setReplacement(current => ({ ...current, [item.shot_id]: event.target.files?.[0] || null }))} />
                      </label>
                      <button disabled={busy || !replacement[item.shot_id]} onClick={() => void replaceShot(item.shot_id)} className="rounded-lg border border-cyan-300/40 bg-cyan-900/40 px-4 py-2 text-sm text-cyan-100 disabled:opacity-40">确认替换此镜头</button>
                    </div>
                  )}
                </article>
              ))}
            </section>
          </>
        )}

        {renderResult && (
          <section className={panel}>
            <h2 className="text-lg font-semibold text-emerald-300">成片生成成功</h2>
            <p className="mt-2 break-all text-sm">{renderResult.output}</p>
            {renderResult.subtitle_path && <p className="mt-2 break-all text-xs text-white/55">字幕：{renderResult.subtitle_path}</p>}
            {renderResult.quality_report && <p className="mt-3 text-sm text-white/65">时长 {renderResult.quality_report.duration_seconds ?? "—"} 秒 · 分辨率 {renderResult.quality_report.width ?? "—"} × {renderResult.quality_report.height ?? "—"}</p>}
          </section>
        )}
      </div>
    </main>
  );
}
