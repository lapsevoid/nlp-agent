import { HelpCircle, X } from "lucide-react";
import { useEffect } from "react";

import { MainMenu } from "@excalidraw/excalidraw";

type ShortcutGroup = {
  title: string;
  items: Array<{ label: string; keys: string }>;
};

const SHORTCUT_GROUPS: ShortcutGroup[] = [
  {
    title: "工具",
    items: [
      { label: "抓手（平移工具）", keys: "H" },
      { label: "选择工具", keys: "V / 1" },
      { label: "矩形", keys: "R / 2" },
      { label: "菱形", keys: "D / 3" },
      { label: "椭圆", keys: "O / 4" },
      { label: "箭头", keys: "A / 5" },
      { label: "线条", keys: "L / 6" },
      { label: "自由画笔", keys: "P / 7" },
      { label: "文字", keys: "T / 8" },
      { label: "插入图像", keys: "9" },
      { label: "橡皮擦", keys: "E / 0" },
      { label: "画框工具", keys: "F" },
      { label: "激光笔", keys: "K" },
    ],
  },
  {
    title: "编辑器",
    items: [
      { label: "移动画布", keys: "Space + 拖动 / 滚轮" },
      { label: "缩放画布", keys: "+ / - 或滚轮" },
      { label: "撤销", keys: "Ctrl/Cmd + Z" },
      { label: "重做", keys: "Ctrl/Cmd + Shift + Z" },
      { label: "删除选中元素", keys: "Delete / Backspace" },
      { label: "全部选中", keys: "Ctrl/Cmd + A" },
      { label: "复制 / 粘贴", keys: "Ctrl/Cmd + C / V" },
      { label: "取消当前操作", keys: "Esc" },
    ],
  },
];

export function WhiteboardHelpMenuItem({ onOpen }: { onOpen: () => void }) {
  return <MainMenu.Item icon={<HelpCircle size={16} />} onClick={onOpen}>
    帮助
  </MainMenu.Item>;
}

export function WhiteboardHelpDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  useEffect(() => {
    if (!open) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);

  if (!open) return null;

  return <div className="whiteboard-help-overlay" role="presentation" onMouseDown={(event) => {
    if (event.target === event.currentTarget) onClose();
  }}>
    <section className="whiteboard-help-dialog" role="dialog" aria-modal="true" aria-labelledby="whiteboard-help-title">
      <header className="whiteboard-help-header">
        <h2 id="whiteboard-help-title">白板快捷键</h2>
        <button type="button" className="whiteboard-help-close" aria-label="关闭帮助" onClick={onClose}>
          <X size={18} aria-hidden="true" />
        </button>
      </header>
      <div className="whiteboard-help-content">
        {SHORTCUT_GROUPS.map((group) => <section key={group.title} className="whiteboard-help-group" aria-labelledby={`whiteboard-help-${group.title}`}>
          <h3 id={`whiteboard-help-${group.title}`}>{group.title}</h3>
          <div className="whiteboard-help-list">
            {group.items.map((item) => <div className="whiteboard-help-row" key={item.label}>
              <span>{item.label}</span>
              <kbd>{item.keys}</kbd>
            </div>)}
          </div>
        </section>)}
      </div>
    </section>
  </div>;
}
