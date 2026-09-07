import { Excalidraw } from "@excalidraw/excalidraw";
import type { ExcalidrawElement } from "@excalidraw/excalidraw/element/types";
import type { AppState, BinaryFiles, ExcalidrawInitialDataState } from "@excalidraw/excalidraw/types";
import "@excalidraw/excalidraw/index.css";

import type { StoredWhiteboardScene } from "./storage";
import "./whiteboard.css";

export interface ExcalidrawSceneChange {
  elements: readonly ExcalidrawElement[];
  appState: AppState;
  files: BinaryFiles;
}

export function ExcalidrawAdapter({ initialScene, onChange }: {
  initialScene: StoredWhiteboardScene | null;
  onChange: (scene: ExcalidrawSceneChange) => void;
}) {
  const initialData: ExcalidrawInitialDataState | undefined = initialScene ? {
    elements: initialScene.elements,
    appState: initialScene.appState,
    files: initialScene.files,
  } : undefined;

  return <section className="whiteboard-panel" aria-label="白板绘图">
    <Excalidraw
      initialData={initialData}
      langCode="zh-CN"
      onChange={(elements, appState, files) => onChange({ elements, appState, files })}
      aiEnabled={false}
      UIOptions={{ welcomeScreen: false }}
      validateEmbeddable={() => false}
    />
  </section>;
}
