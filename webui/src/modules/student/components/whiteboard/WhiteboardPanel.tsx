import { useCallback, useEffect, useRef, useState } from "react";

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
  const [initialScene] = useState<StoredWhiteboardScene | null>(() => userId ? readWhiteboardScene(userId) : null);
  const latestScene = useRef<StoredWhiteboardScene | null>(initialScene);
  const saveTimer = useRef<number | null>(null);

  useEffect(() => {
    if (initialScene) onSceneChange?.(initialScene);
  }, [initialScene, onSceneChange]);

  const persistLatestScene = useCallback(() => {
    if (userId && latestScene.current) writeWhiteboardScene(userId, latestScene.current);
  }, [userId]);

  useEffect(() => () => {
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    persistLatestScene();
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
      persistLatestScene();
    }, LOCAL_SAVE_DEBOUNCE_MS);
  }, [onSceneChange, persistLatestScene, userId]);

  return <ExcalidrawAdapter initialScene={initialScene} onChange={handleChange} />;
}
