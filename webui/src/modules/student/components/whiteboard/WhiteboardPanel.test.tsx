import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const excalidraw = vi.hoisted(() => ({
  render: vi.fn(),
}));

vi.mock("@excalidraw/excalidraw", () => ({
  Excalidraw: (props: { initialData?: unknown; onChange?: (elements: unknown, appState: unknown, files: unknown) => void }) => {
    excalidraw.render(props);
    return <button type="button" onClick={() => props.onChange?.([{ id: "line-1", type: "line" }] as never, { theme: "light", viewBackgroundColor: "#fff" } as never, {})}>模拟绘图</button>;
  },
}));

import { WhiteboardPanel } from "./WhiteboardPanel";
import { storageKeyForUser } from "./storage";

describe("WhiteboardPanel", () => {
  beforeEach(() => {
    localStorage.clear();
    excalidraw.render.mockClear();
    vi.useFakeTimers();
  });

  afterEach(() => vi.useRealTimers());

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
});
