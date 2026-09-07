import { useCallback, useEffect, useRef, useState } from "react";

import { CaptureUpdateAction, Excalidraw, loadLibraryFromBlob, MainMenu } from "@excalidraw/excalidraw";
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
import { WhiteboardHelpDialog, WhiteboardHelpMenuItem } from "./WhiteboardHelp";
import "./whiteboard.css";

export interface ExcalidrawSceneChange {
  elements: readonly ExcalidrawElement[];
  appState: AppState;
  files: BinaryFiles;
}

type LoadedWhiteboardLibrary = {
  asset: typeof WHITEBOARD_LIBRARY_ASSETS[number];
  libraryItems: Awaited<ReturnType<typeof loadLibraryFromBlob>>;
};

type BundledLibraryLoadResult = {
  libraries: LoadedWhiteboardLibrary[];
  failedAssets: typeof WHITEBOARD_LIBRARY_ASSETS[number][];
};

let bundledLibrariesPromise: Promise<BundledLibraryLoadResult> | null = null;

function loadBundledLibraries() {
  if (!bundledLibrariesPromise) {
    bundledLibrariesPromise = (async () => {
      const failedAssets: typeof WHITEBOARD_LIBRARY_ASSETS[number][] = [];
      const libraries = (await Promise.all(WHITEBOARD_LIBRARY_ASSETS.map(async (asset) => {
        try {
          const response = await fetch(whiteboardLibraryUrl(asset.fileName));
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          return {
            asset,
            libraryItems: await loadLibraryFromBlob(await response.blob(), "published"),
          };
        } catch (error) {
          failedAssets.push(asset);
          console.warn(`[whiteboard] failed to load ${asset.name} library`, error);
          return null;
        }
      }))).filter((library): library is LoadedWhiteboardLibrary => library !== null);

      // Keep successful loads cached, but allow a later board to retry when
      // this attempt was incomplete (for example during a transient outage).
      if (failedAssets.length > 0) bundledLibrariesPromise = null;
      return { libraries, failedAssets };
    })();
  }
  return bundledLibrariesPromise;
}

/** Test-only cache reset; production callers keep successful loads cached. */
export function resetBundledLibrariesCache() {
  bundledLibrariesPromise = null;
}

export function ExcalidrawAdapter({ initialScene, onChange, onLibraryLoadError }: {
  initialScene: StoredWhiteboardScene | null;
  onChange: (scene: ExcalidrawSceneChange) => void;
  onLibraryLoadError?: (assetNames: readonly string[]) => void;
}) {
  const libraryLoadStarted = useRef(false);
  const excalidrawApi = useRef<ExcalidrawImperativeAPI | null>(null);
  const mounted = useRef(true);
  const [helpOpen, setHelpOpen] = useState(false);
  const initialData: ExcalidrawInitialDataState | undefined = initialScene ? {
    elements: initialScene.elements,
    appState: initialScene.appState,
    files: initialScene.files,
  } : undefined;
  useEffect(() => () => {
    mounted.current = false;
    excalidrawApi.current = null;
  }, []);

  const handleExcalidrawAPI = useCallback((api: ExcalidrawImperativeAPI) => {
    excalidrawApi.current = api;
    if (libraryLoadStarted.current) return;
    libraryLoadStarted.current = true;

    const installBundledLibraries = async () => {
      if (!mounted.current) return;

      // Clear the engine's library before awaiting our local assets so a
      // user's personal/public library cannot flash into this read-only view.
      let libraryUpdateFailed = false;
      try {
        await api.updateLibrary({
          libraryItems: [],
          merge: false,
          defaultStatus: "published",
        });
      } catch (error) {
        libraryUpdateFailed = true;
        console.warn("[whiteboard] failed to clear the library", error);
      }

      const { libraries: loadedLibraries, failedAssets } = await loadBundledLibraries();
      if (!mounted.current) return;

      const installationFailures = [...failedAssets];
      for (const { asset, libraryItems } of loadedLibraries) {
        if (!mounted.current) return;
        try {
          await api.updateLibrary({
            libraryItems,
            merge: true,
            defaultStatus: "published",
          });
        } catch (error) {
          installationFailures.push(asset);
          console.warn(`[whiteboard] failed to install ${asset.name} library`, error);
        }
      }
      if (libraryUpdateFailed || installationFailures.length > 0) {
        onLibraryLoadError?.(installationFailures.map((asset) => asset.name));
      }
    };

    void installBundledLibraries();
  }, [onLibraryLoadError]);

  const handleSceneChange = useCallback((elements: readonly ExcalidrawElement[], appState: AppState, files: BinaryFiles) => {
    const safeElements = elements.filter((element) => element.type !== "embeddable");
    if (safeElements.length !== elements.length) {
      excalidrawApi.current?.updateScene({
        elements: safeElements,
        captureUpdate: CaptureUpdateAction.NEVER,
      });
    }
    onChange({ elements: safeElements, appState, files });
  }, [onChange]);

  return <section className="whiteboard-panel" aria-label="白板绘图">
    <Excalidraw
      initialData={initialData}
      langCode="zh-CN"
      onChange={handleSceneChange}
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
        <WhiteboardHelpMenuItem onOpen={() => setHelpOpen(true)} />
        <MainMenu.DefaultItems.ClearCanvas />
        <MainMenu.Separator />
        <MainMenu.DefaultItems.ToggleTheme />
        <MainMenu.DefaultItems.ChangeCanvasBackground />
      </MainMenu>
    </Excalidraw>
    <WhiteboardHelpDialog open={helpOpen} onClose={() => setHelpOpen(false)} />
  </section>;
}
