const MIIT_RECORD_URL = "https://beian.miit.gov.cn/";

export function IcpRecordBar() {
  return (
    <div className="site-icp-bar" role="contentinfo">
      <span>© 2026 乐山师范学院自然语言处理教学平台</span>
      <span aria-hidden="true">·</span>
      <a href={MIIT_RECORD_URL} target="_blank" rel="noreferrer">
        蜀ICP备2026055638号
      </a>
    </div>
  );
}
