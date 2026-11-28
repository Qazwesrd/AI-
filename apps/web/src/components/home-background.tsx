"use client";

import { useEffect, useRef, useState } from "react";

const STORAGE_KEY = "ai-comic-home-background-v1";

export default function HomeBackground() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [hasCustomBackground, setHasCustomBackground] = useState(false);

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved?.startsWith("data:image/")) {
        document.documentElement.style.setProperty("--home-bg-image", `url("${saved}")`);
        setHasCustomBackground(true);
      }
    } catch {
      // Private browsing or storage restrictions: keep the default background.
    }
  }, []);

  function chooseImage(file?: File) {
    if (!file) return;
    if (!file.type.startsWith("image/")) return;
    if (file.size > 8 * 1024 * 1024) {
      window.alert("背景图片请控制在 8MB 以内。");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result !== "string") return;
      document.documentElement.style.setProperty("--home-bg-image", `url("${reader.result}")`);
      try {
        window.localStorage.setItem(STORAGE_KEY, reader.result);
        setHasCustomBackground(true);
      } catch {
        window.alert("图片已临时应用，但浏览器存储空间不足，刷新后可能需要重新选择。");
      }
    };
    reader.readAsDataURL(file);
  }

  function clearImage() {
    document.documentElement.style.removeProperty("--home-bg-image");
    try { window.localStorage.removeItem(STORAGE_KEY); } catch {}
    setHasCustomBackground(false);
  }

  return (
    <div className="home-background-controls">
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        className="sr-only"
        aria-label="选择首页背景图片"
        onChange={(event) => chooseImage(event.target.files?.[0])}
      />
      <button type="button" onClick={() => inputRef.current?.click()}>
        {hasCustomBackground ? "更换背景图片" : "设置背景图片"}
      </button>
      {hasCustomBackground && (
        <button type="button" onClick={clearImage} aria-label="恢复默认背景">恢复默认</button>
      )}
    </div>
  );
}
