import { useCallback, useRef } from "react";

import { Excalidraw, MainMenu } from "@excalidraw/excalidraw";
import type { ExcalidrawElement } from "@excalidraw/excalidraw/element/types";
import type {
  AppState,
  BinaryFiles,
  ExcalidrawImperativeAPI,
  ExcalidrawInitialDataState,
} from "@excalidraw/excalidraw/types";
import "@excalidraw/excalidraw/index.css";

import { WHITEBOARD_LIBRARY_ASSETS, whiteboardLibraryUrl } from "./libraryAssets";
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
  const libraryLoadStarted = useRef(false);
  const initialData: ExcalidrawInitialDataState | undefined = initialScene ? {
    elements: initialScene.elements,
    appState: initialScene.appState,
    files: initialScene.files,
  } : undefined;
  const handleExcalidrawAPI = useCallback((api: ExcalidrawImperativeAPI) => {
    if (libraryLoadStarted.current) return;
    libraryLoadStarted.current = true;

    void Promise.all(WHITEBOARD_LIBRARY_ASSETS.map(async (asset) => {
      try {
        const response = await fetch(whiteboardLibraryUrl(asset.fileName));
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        await api.updateLibrary({
          libraryItems: await response.blob(),
          merge: true,
          defaultStatus: "published",
        });
      } catch (error) {
        console.warn(`[whiteboard] failed to load ${asset.name} library`, error);
      }
    }));
  }, []);

  return <section className="whiteboard-panel" aria-label="白板绘图">
    <Excalidraw
      initialData={initialData}
      langCode="zh-CN"
      onChange={(elements, appState, files) => onChange({ elements, appState, files })}
      excalidrawAPI={handleExcalidrawAPI}
      aiEnabled={false}
      validateEmbeddable={() => false}
    >
      <MainMenu>
        <MainMenu.DefaultItems.LoadScene />
        <MainMenu.DefaultItems.SaveToActiveFile />
        <MainMenu.DefaultItems.Export />
        <MainMenu.DefaultItems.SaveAsImage />
        <MainMenu.DefaultItems.SearchMenu />
        <MainMenu.DefaultItems.Help />
        <MainMenu.DefaultItems.ClearCanvas />
        <MainMenu.Separator />
        <MainMenu.DefaultItems.ToggleTheme />
        <MainMenu.DefaultItems.ChangeCanvasBackground />
      </MainMenu>
    </Excalidraw>
  </section>;
}
