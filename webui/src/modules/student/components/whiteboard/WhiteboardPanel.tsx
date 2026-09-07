import { useCallback, useEffect, useRef, useState } from "react";

import { ExcalidrawAdapter, type ExcalidrawSceneChange } from "./ExcalidrawAdapter";
import {
  readWhiteboardScene,
  serializeWhiteboardScene,
  writeWhiteboardScene,
  type StoredWhiteboardScene,
} from "./storage";

const LOCAL_SAVE_DEBOUNCE_MS = 250;

/**
 * The whiteboard deliberately owns no business state. It only adapts the
 * embedded Excalidraw scene to per-user browser storage.
 */
export function WhiteboardPanel({ userId }: { userId: string | null }) {
  const [initialScene] = useState<StoredWhiteboardScene | null>(() => userId ? readWhiteboardScene(userId) : null);
  const latestScene = useRef<StoredWhiteboardScene | null>(initialScene);
  const saveTimer = useRef<number | null>(null);

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
    latestScene.current = serializeWhiteboardScene(scene.elements, scene.appState, scene.files);
    if (!userId) return;
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      saveTimer.current = null;
      persistLatestScene();
    }, LOCAL_SAVE_DEBOUNCE_MS);
  }, [persistLatestScene, userId]);

  return <ExcalidrawAdapter initialScene={initialScene} onChange={handleChange} />;
}
