import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ExcalidrawAdapter, type ExcalidrawSceneChange } from "./ExcalidrawAdapter";
import {
  readWhiteboardScene,
  serializeWhiteboardScene,
  writeWhiteboardScene,
  type StoredWhiteboardScene,
} from "./storage";

const LOCAL_SAVE_DEBOUNCE_MS = 250;

export interface WhiteboardPanelProps {
  userId: string | null;
  /** Exposes the structured scene to page-level business actions. */
  onSceneChange?: (scene: StoredWhiteboardScene) => void;
}

/**
 * The whiteboard deliberately owns no business state. It only adapts the
 * embedded Excalidraw scene to per-user browser storage.
 */
export function WhiteboardPanel({ userId, onSceneChange }: WhiteboardPanelProps) {
  if (!userId) {
    return <div className="whiteboard-shell whiteboard-auth-required" role="status">请登录后使用白板。</div>;
  }

  return <AuthenticatedWhiteboardPanel userId={userId} onSceneChange={onSceneChange} />;
}

function AuthenticatedWhiteboardPanel({ userId, onSceneChange }: WhiteboardPanelProps & { userId: string }) {
  const initialScene = useMemo<StoredWhiteboardScene | null>(() => userId ? readWhiteboardScene(userId) : null, [userId]);
  const latestScene = useRef<StoredWhiteboardScene | null>(initialScene);
  const saveTimer = useRef<number | null>(null);
  const [saveErrorUserId, setSaveErrorUserId] = useState<string | null>(null);
  const [libraryLoadError, setLibraryLoadError] = useState(false);

  useEffect(() => {
    latestScene.current = initialScene;
  }, [initialScene]);

  useEffect(() => {
    if (initialScene) onSceneChange?.(initialScene);
  }, [initialScene, onSceneChange]);

  const persistLatestScene = useCallback((notify: boolean) => {
    if (!userId || !latestScene.current) return;
    const persisted = writeWhiteboardScene(userId, latestScene.current);
    if (notify) setSaveErrorUserId(persisted ? null : userId);
  }, [userId]);

  useEffect(() => {
    const flushLatestScene = () => {
      if (saveTimer.current !== null) {
        window.clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }
      persistLatestScene(true);
    };

    window.addEventListener("pagehide", flushLatestScene);
    return () => {
      window.removeEventListener("pagehide", flushLatestScene);
      if (saveTimer.current !== null) {
        window.clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }
      persistLatestScene(false);
    };
  }, [persistLatestScene]);

  const handleChange = useCallback((
    scene: ExcalidrawSceneChange,
  ) => {
    const serializedScene = serializeWhiteboardScene(scene.elements, scene.appState, scene.files);
    latestScene.current = serializedScene;
    onSceneChange?.(serializedScene);
    if (!userId) return;
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      saveTimer.current = null;
      persistLatestScene(true);
    }, LOCAL_SAVE_DEBOUNCE_MS);
  }, [onSceneChange, persistLatestScene, userId]);

  return <div className="whiteboard-shell">
    <ExcalidrawAdapter
      key={userId}
      initialScene={initialScene}
      onChange={handleChange}
      onLibraryLoadError={() => setLibraryLoadError(true)}
    />
    {libraryLoadError && <div className="whiteboard-library-warning" role="status">部分教学素材加载失败，请刷新白板后重试。</div>}
    {userId !== null && saveErrorUserId === userId && <div className="whiteboard-save-warning" role="alert">本地保存失败，请导出白板文件备份。</div>}
  </div>;
}
