import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const excalidraw = vi.hoisted(() => ({
  render: vi.fn(),
  loadLibraryFromBlob: vi.fn().mockResolvedValue([]),
}));

vi.mock("@excalidraw/excalidraw", () => ({
  loadLibraryFromBlob: excalidraw.loadLibraryFromBlob,
  Excalidraw: (props: { initialData?: unknown; onChange?: (elements: unknown, appState: unknown, files: unknown) => void; children?: React.ReactNode }) => {
    excalidraw.render(props);
    return <><button type="button" onClick={() => props.onChange?.([{ id: "line-1", type: "line" }] as never, { theme: "light", viewBackgroundColor: "#fff" } as never, {})}>模拟绘图</button>{props.children}</>;
  },
  MainMenu: Object.assign(({ children }: { children?: React.ReactNode }) => <nav data-testid="whiteboard-main-menu">{children}</nav>, {
    DefaultItems: {
      LoadScene: () => null,
      SaveToActiveFile: () => null,
      Export: () => null,
      SaveAsImage: () => null,
      SearchMenu: () => null,
      Help: () => <span data-testid="whiteboard-help-menu-item" />,
      ClearCanvas: () => null,
      ToggleTheme: () => null,
      ChangeCanvasBackground: () => null,
      Socials: () => <span data-testid="whiteboard-excalidraw-links" />,
    },
    Separator: () => null,
  }),
}));

import { WhiteboardPanel } from "./WhiteboardPanel";
import { WHITEBOARD_LIBRARY_ASSETS } from "./libraryAssets";
import { storageKeyForUser } from "./storage";

describe("WhiteboardPanel", () => {
  beforeEach(() => {
    localStorage.clear();
    excalidraw.render.mockClear();
    excalidraw.loadLibraryFromBlob.mockClear();
    excalidraw.loadLibraryFromBlob.mockResolvedValue([]);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("loads the current user's local scene into the embedded engine", () => {
    localStorage.setItem(storageKeyForUser("student-1"), JSON.stringify({
      schemaVersion: 1,
      elements: [{ id: "saved-1", type: "rectangle" }],
      appState: { theme: "dark" },
      files: {},
    }));

    render(<WhiteboardPanel userId="student-1" />);

    expect(excalidraw.render).toHaveBeenCalledWith(expect.objectContaining({
      initialData: expect.objectContaining({ elements: [{ id: "saved-1", type: "rectangle" }] }),
      langCode: "zh-CN",
      aiEnabled: false,
    }));
  });

  it("keeps the native help entry without rendering Excalidraw links", () => {
    render(<WhiteboardPanel userId="student-1" />);

    expect(screen.getByTestId("whiteboard-main-menu")).toBeInTheDocument();
    expect(screen.getByTestId("whiteboard-help-menu-item")).toBeInTheDocument();
    expect(screen.queryByTestId("whiteboard-excalidraw-links")).not.toBeInTheDocument();
  });

  it("loads the bundled teaching libraries through the Excalidraw API", async () => {
    vi.useRealTimers();
    const updateLibrary = vi.fn().mockResolvedValue([]);
    const blob = new Blob([JSON.stringify({ type: "excalidrawlib", version: 2, libraryItems: [] })], { type: "application/json" });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(blob) });
    vi.stubGlobal("fetch", fetchMock);

    render(<WhiteboardPanel userId="student-1" />);
    const props = excalidraw.render.mock.calls.at(-1)?.[0] as { excalidrawAPI?: (api: { updateLibrary: typeof updateLibrary }) => void };
    props.excalidrawAPI?.({ updateLibrary });

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await waitFor(() => expect(updateLibrary).toHaveBeenCalled());
    expect(fetchMock).toHaveBeenCalledTimes(WHITEBOARD_LIBRARY_ASSETS.length);
    expect(excalidraw.loadLibraryFromBlob).toHaveBeenCalledTimes(WHITEBOARD_LIBRARY_ASSETS.length);
    expect(updateLibrary).toHaveBeenCalledTimes(WHITEBOARD_LIBRARY_ASSETS.length);
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(expect.arrayContaining([
      expect.stringContaining("deep-learning.excalidrawlib"),
      expect.stringContaining("data-processing.excalidrawlib"),
      expect.stringContaining("mathematical-symbols.excalidrawlib"),
      expect.stringContaining("flow-chart-symbols.excalidrawlib"),
      expect.stringContaining("montessori-basic-grammar-symbols.excalidrawlib"),
      expect.stringContaining("bubbles.excalidrawlib"),
    ]));
    expect(updateLibrary).toHaveBeenNthCalledWith(1, expect.objectContaining({ merge: false, defaultStatus: "published" }));
    expect(updateLibrary.mock.calls.slice(1).every(([options]) => options.merge === true && options.defaultStatus === "published")).toBe(true);
  });

  it("writes scene changes to local storage for that user", () => {
    render(<WhiteboardPanel userId="student-1" />);

    fireEvent.click(screen.getByRole("button", { name: "模拟绘图" }));
    vi.advanceTimersByTime(250);

    const stored = JSON.parse(localStorage.getItem(storageKeyForUser("student-1")) ?? "null") as { elements: Array<{ id: string }> };
    expect(stored.elements).toEqual([{ id: "line-1", type: "line" }]);
    expect(localStorage.getItem(storageKeyForUser("student-2"))).toBeNull();
  });

  it("forwards structured scene changes to page-level actions", () => {
    const onSceneChange = vi.fn();

    render(<WhiteboardPanel userId="student-1" onSceneChange={onSceneChange} />);
    fireEvent.click(screen.getByRole("button", { name: "模拟绘图" }));

    expect(onSceneChange).toHaveBeenCalledWith(expect.objectContaining({
      schemaVersion: 1,
      elements: [{ id: "line-1", type: "line" }],
      files: {},
    }));
  });

  it("forwards a restored scene before the next edit", () => {
    const onSceneChange = vi.fn();
    const savedScene = {
      schemaVersion: 1,
      elements: [{ id: "saved-1", type: "rectangle" }],
      appState: { theme: "dark" },
      files: {},
    };
    localStorage.setItem(storageKeyForUser("student-1"), JSON.stringify(savedScene));

    render(<WhiteboardPanel userId="student-1" onSceneChange={onSceneChange} />);

    expect(onSceneChange).toHaveBeenCalledWith(savedScene);
  });
});
