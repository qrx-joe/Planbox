import { CalendarDays, Inbox, Search, Upload } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";

type SourceItem = {
  id: string;
  source_type: string;
  source_url?: string | null;
  raw_text?: string | null;
  title?: string | null;
  captured_at: string;
  parse_status: string;
};

export function App() {
  const [rawText, setRawText] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [items, setItems] = useState<SourceItem[]>([]);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    void loadItems();
  }, []);

  async function loadItems() {
    const response = await fetch("/api/source-items");
    if (response.ok) {
      setItems(await response.json());
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSaving(true);
    try {
      const payload = {
        source_type: sourceUrl ? "web" : "text",
        source_url: sourceUrl || null,
        raw_text: rawText || null
      };
      const response = await fetch("/api/source-items", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (!response.ok) {
        throw new Error("保存失败");
      }
      setRawText("");
      setSourceUrl("");
      await loadItems();
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <CalendarDays size={24} />
          <span>Planbox</span>
        </div>
        <nav className="nav">
          <button className="nav-item active" title="Inbox">
            <Inbox size={18} />
            <span>Inbox</span>
          </button>
          <button className="nav-item" title="Search">
            <Search size={18} />
            <span>Activities</span>
          </button>
        </nav>
      </aside>

      <section className="content">
        <header className="page-header">
          <div>
            <h1>活动收件箱</h1>
            <p>先把原文和链接收进来，下一步再做 AI 提取和人工确认。</p>
          </div>
        </header>

        <form className="composer" onSubmit={handleSubmit}>
          <label>
            链接
            <input
              value={sourceUrl}
              onChange={(event) => setSourceUrl(event.target.value)}
              placeholder="https://..."
            />
          </label>
          <label>
            正文
            <textarea
              value={rawText}
              onChange={(event) => setRawText(event.target.value)}
              placeholder="粘贴微信推文、小红书笔记或活动介绍..."
            />
          </label>
          <div className="composer-actions">
            <button type="button" className="secondary" title="Upload screenshot">
              <Upload size={17} />
              截图稍后接入
            </button>
            <button type="submit" disabled={isSaving || (!rawText && !sourceUrl)}>
              {isSaving ? "保存中" : "保存到 Inbox"}
            </button>
          </div>
        </form>

        <section className="list-header">
          <h2>最近收集</h2>
          <button type="button" className="secondary" onClick={loadItems}>
            刷新
          </button>
        </section>

        <section className="source-list">
          {items.length === 0 ? (
            <p className="empty">还没有收集内容。先扔一条真实活动进来，别继续让微信群当黑洞。</p>
          ) : (
            items.map((item) => (
              <article className="source-row" key={item.id}>
                <div>
                  <h3>{item.title || item.source_url || "未命名活动线索"}</h3>
                  <p>{item.raw_text || item.source_url}</p>
                </div>
                <span className="status">{item.parse_status}</span>
              </article>
            ))
          )}
        </section>
      </section>
    </main>
  );
}
