import {
  AlertTriangle,
  Bell,
  CalendarDays,
  Check,
  ClipboardList,
  Clock,
  Download,
  Inbox,
  Search,
  Sparkles,
  Star,
  Upload,
  X,
} from "lucide-react";
import { FormEvent, ReactNode, useEffect, useState } from "react";

type View = "inbox" | "review" | "activities" | "brief";

type SourceItem = {
  id: string;
  source_type: string;
  source_url?: string | null;
  raw_text?: string | null;
  title?: string | null;
  captured_at: string;
  parse_status: string;
};

type ExtractedEventDraft = {
  title: string;
  event_type?: string | null;
  registration_url?: string | null;
  registration_deadline_at?: string | null;
  event_start_at?: string | null;
  location?: string | null;
  is_online?: boolean | null;
  missing_fields: string[];
  confidence: number;
};

type ReviewQueueItem = {
  extraction: {
    id: string;
    source_item_id: string;
    extracted: ExtractedEventDraft;
    missing_fields: string[];
    confidence?: number | null;
  };
  source_url?: string | null;
  source_text?: string | null;
  source_type: string;
};

type EventItem = {
  id: string;
  title: string;
  status: string;
  event_type?: string | null;
  registration_deadline_at?: string | null;
  event_start_at?: string | null;
  event_end_at?: string | null;
  location?: string | null;
  registration_url?: string | null;
  confidence?: number | null;
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

type EventFilters = {
  keyword: string;
  status: string;
  deadlineWithinDays: number;
};

const STATUS_OPTIONS = [
  { value: "", label: "全部状态" },
  { value: "interested", label: "感兴趣" },
  { value: "applied", label: "已报名" },
  { value: "rejected", label: "已放弃" },
  { value: "ongoing", label: "进行中" },
  { value: "done", label: "已结束" },
  { value: "expired", label: "已过期" },
] as const;

type EvidenceItem = {
  id: string;
  field_name: string;
  field_value?: string | null;
  evidence_text?: string | null;
  confidence?: number | null;
  source_url?: string | null;
};

type DuplicateCandidate = {
  event: EventItem;
  match_reasons: string[];
  match_score: number;
};

export function App() {
  const [activeView, setActiveView] = useState<View>("inbox");
  const [rawText, setRawText] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [items, setItems] = useState<SourceItem[]>([]);
  const [reviewItems, setReviewItems] = useState<ReviewQueueItem[]>([]);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [filters, setFilters] = useState<EventFilters>({
    keyword: "",
    status: "",
    deadlineWithinDays: 0,
  });
  const [evidences, setEvidences] = useState<EvidenceItem[]>([]);
  const [duplicates, setDuplicates] = useState<DuplicateCandidate[]>([]);
  const [duplicateBaseId, setDuplicateBaseId] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    void refreshAll();
  }, []);

  useEffect(() => {
    void loadEvents();
  }, [filters]);

  async function refreshAll() {
    await Promise.all([loadItems(), loadReviewQueue(), loadEvents()]);
  }

  async function loadItems() {
    const response = await fetch(`${API_BASE_URL}/api/source-items`);
    if (response.ok) {
      setItems(await response.json());
    }
  }

  async function loadReviewQueue() {
    const response = await fetch(`${API_BASE_URL}/api/review-queue`);
    if (response.ok) {
      setReviewItems(await response.json());
    }
  }

  async function loadEvents() {
    const params = new URLSearchParams();
    if (filters.keyword) params.set("keyword", filters.keyword);
    if (filters.status) params.set("status", filters.status);
    if (filters.deadlineWithinDays) {
      const until = new Date(Date.now() + filters.deadlineWithinDays * 86400000).toISOString();
      params.set("deadline_before", until);
      params.set("deadline_after", new Date().toISOString());
    }
    params.set("paginated", "false");
    const response = await fetch(`${API_BASE_URL}/api/events?${params.toString()}`);
    if (response.ok) {
      setEvents(await response.json());
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSaving(true);
    setMessage(null);
    try {
      // 微信文章链接走专门的抓取接口
      const isWechatUrl = sourceUrl.includes("mp.weixin.qq.com");
      const endpoint = isWechatUrl ? `${API_BASE_URL}/api/source-items/fetch?source_url=${encodeURIComponent(sourceUrl)}` : `${API_BASE_URL}/api/source-items`;
      const method = isWechatUrl ? "POST" : "POST";
      const body = isWechatUrl ? null : JSON.stringify({
        source_type: sourceUrl ? "web" : "text",
        source_url: sourceUrl || null,
        raw_text: rawText || null,
      });

      const response = await fetch(endpoint, {
        method,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body,
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || "保存失败");
      }

      if (isWechatUrl) {
        setMessage("已抓取文章并保存到 Inbox");
      } else {
        setMessage("已保存到 Inbox");
      }
      setRawText("");
      setSourceUrl("");
      await loadItems();
    } finally {
      setIsSaving(false);
    }
  }

  async function extractSource(sourceItemId: string) {
    setBusyId(sourceItemId);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/source-items/${sourceItemId}/extract`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error("抽取失败");
      }
      setMessage("已生成待确认活动草稿");
      setActiveView("review");
      await refreshAll();
    } finally {
      setBusyId(null);
    }
  }

  async function confirmReview(extractionId: string) {
    setBusyId(extractionId);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/review-queue/${extractionId}/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "interested" }),
      });
      if (!response.ok) {
        throw new Error("确认失败");
      }
      setMessage("活动已入库，并标记为感兴趣");
      await refreshAll();
    } finally {
      setBusyId(null);
    }
  }

  async function rejectReview(extractionId: string) {
    setBusyId(extractionId);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/review-queue/${extractionId}/reject`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error("放弃失败");
      }
      setMessage("已放弃该草稿");
      await refreshAll();
    } finally {
      setBusyId(null);
    }
  }

  async function updateEventStatus(eventId: string, nextStatus: string) {
    setBusyId(eventId);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/events/${eventId}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: nextStatus }),
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || "状态更新失败");
      }
      setMessage("状态已更新");
      await loadEvents();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "状态更新失败");
    } finally {
      setBusyId(null);
    }
  }

  async function loadEvidences(eventId: string) {
    const response = await fetch(`${API_BASE_URL}/api/evidences/${eventId}`);
    if (response.ok) {
      const data = await response.json();
      setEvidences(data.items ?? []);
    } else {
      setEvidences([]);
    }
  }

  async function checkDuplicates(eventId: string) {
    const response = await fetch(`${API_BASE_URL}/api/events/duplicates/${eventId}`);
    if (response.ok) {
      const data = await response.json();
      const candidates = (data.candidates ?? []) as DuplicateCandidate[];
      setDuplicates(candidates);
      setDuplicateBaseId(eventId);
      setMessage(
        candidates.length > 0 ? `发现 ${candidates.length} 个可能重复的活动` : "没有发现重复活动"
      );
    } else {
      setDuplicates([]);
      setDuplicateBaseId(null);
    }
  }

  async function mergeEvents(targetId: string, sourceId: string) {
    setBusyId(sourceId);
    try {
      const response = await fetch(`${API_BASE_URL}/api/events/merge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_id: targetId, source_id: sourceId }),
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || "合并失败");
      }
      const data = await response.json();
      setMessage(`已合并，补全了 ${data.filled_fields?.length ?? 0} 个字段`);
      setDuplicates([]);
      setDuplicateBaseId(null);
      await loadEvents();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "合并失败");
    } finally {
      setBusyId(null);
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
          <NavButton active={activeView === "inbox"} onClick={() => setActiveView("inbox")}>
            <Inbox size={18} />
            <span>Inbox</span>
          </NavButton>
          <NavButton active={activeView === "review"} onClick={() => setActiveView("review")}>
            <ClipboardList size={18} />
            <span>Review</span>
          </NavButton>
          <NavButton
            active={activeView === "activities"}
            onClick={() => setActiveView("activities")}
          >
            <Search size={18} />
            <span>Activities</span>
          </NavButton>
          <NavButton active={activeView === "brief"} onClick={() => setActiveView("brief")}>
            <Bell size={18} />
            <span>Brief</span>
          </NavButton>
        </nav>
      </aside>

      <section className="content">
        <header className="page-header">
          <div>
            <h1>{pageTitle(activeView)}</h1>
            <p>{pageSubtitle(activeView)}</p>
          </div>
          <button type="button" className="secondary" onClick={refreshAll}>
            刷新
          </button>
        </header>

        {message ? <p className="notice">{message}</p> : null}

        {activeView === "inbox" ? (
          <>
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
            </section>

            <section className="source-list">
              {items.length === 0 ? (
                <p className="empty">还没有收集内容。先扔一条真实活动进来。</p>
              ) : (
                items.map((item) => (
                  <article className="source-row" key={item.id}>
                    <div>
                      <h3>{item.title || item.source_url || "未命名活动线索"}</h3>
                      <p>{item.raw_text || item.source_url}</p>
                    </div>
                    <div className="row-actions">
                      <span className="status">{item.parse_status}</span>
                      <button
                        type="button"
                        className="secondary"
                        disabled={busyId === item.id}
                        onClick={() => extractSource(item.id)}
                      >
                        <Sparkles size={16} />
                        {busyId === item.id ? "抽取中" : "AI 抽取"}
                      </button>
                    </div>
                  </article>
                ))
              )}
            </section>
          </>
        ) : null}

        {activeView === "review" ? (
          <section className="review-list">
            {reviewItems.length === 0 ? (
              <p className="empty">没有待确认草稿。很好，至少这里暂时不是第二个垃圾桶。</p>
            ) : (
              reviewItems.map((item) => (
                <article className="review-card" key={item.extraction.id}>
                  <div className="review-source">
                    <p className="eyebrow">{item.source_type}</p>
                    <p>{item.source_text || item.source_url}</p>
                  </div>
                  <div className="review-fields">
                    <h2>{item.extraction.extracted.title}</h2>
                    <dl>
                      <Field label="类型" value={item.extraction.extracted.event_type} />
                      <Field
                        label="报名截止"
                        value={item.extraction.extracted.registration_deadline_at}
                      />
                      <Field label="举办时间" value={item.extraction.extracted.event_start_at} />
                      <Field label="地点" value={item.extraction.extracted.location} />
                      <Field label="置信度" value={formatScore(item.extraction.confidence)} />
                    </dl>
                    {item.extraction.missing_fields.length > 0 ? (
                      <p className="missing">
                        缺失字段：{item.extraction.missing_fields.join("、")}
                      </p>
                    ) : null}
                    <div className="composer-actions">
                      <button
                        type="button"
                        disabled={busyId === item.extraction.id}
                        onClick={() => confirmReview(item.extraction.id)}
                      >
                        <Check size={17} />
                        确认入库
                      </button>
                      <button
                        type="button"
                        className="secondary"
                        disabled={busyId === item.extraction.id}
                        onClick={() => rejectReview(item.extraction.id)}
                      >
                        <X size={17} />
                        放弃
                      </button>
                    </div>
                  </div>
                </article>
              ))
            )}
          </section>
        ) : null}

        {activeView === "activities" ? (
          <>
            <section className="list-header">
              <h2>已确认活动</h2>
              <a className="button-link" href={`${API_BASE_URL}/api/calendar/export.ics`}>
                <Download size={17} />
                导出待行动日历
              </a>
            </section>

            <section className="filter-bar">
              <input
                type="search"
                placeholder="搜索活动名称、主办方、地点"
                value={filters.keyword}
                onChange={(e) => setFilters({ ...filters, keyword: e.target.value })}
              />
              <select
                value={filters.status}
                onChange={(e) => setFilters({ ...filters, status: e.target.value })}
              >
                {STATUS_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <select
                value={filters.deadlineWithinDays}
                onChange={(e) =>
                  setFilters({ ...filters, deadlineWithinDays: Number(e.target.value) })
                }
              >
                <option value={0}>不限截止时间</option>
                <option value={3}>3 天内截止</option>
                <option value={7}>7 天内截止</option>
                <option value={30}>30 天内截止</option>
              </select>
            </section>

            <section className="source-list">
              {events.length === 0 ? (
                <p className="empty">还没有正式活动。先从 Review 里确认一个。</p>
              ) : (
                events.map((event) => (
                  <article className="source-row" key={event.id}>
                    <div>
                      <h3>{event.title}</h3>
                      <p>
                        {[event.registration_deadline_at, event.event_start_at, event.location]
                          .filter(Boolean)
                          .join(" · ") || "时间地点待补充"}
                      </p>
                      <div className="inline-actions">
                        <select
                          className="status-select"
                          value={event.status}
                          disabled={busyId === event.id}
                          onChange={(e) => updateEventStatus(event.id, e.target.value)}
                        >
                          {STATUS_OPTIONS.filter((o) => o.value).map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          className="link-button"
                          onClick={() => loadEvidences(event.id)}
                        >
                          查看证据
                        </button>
                        <button
                          type="button"
                          className="link-button"
                          onClick={() => checkDuplicates(event.id)}
                        >
                          检查重复
                        </button>
                      </div>
                      {evidences.length > 0 ? (
                        <ul className="evidence-list">
                          {evidences.map((item) => (
                            <li key={item.id}>
                              <strong>{item.field_name}</strong>
                              {item.evidence_text ? `：${item.evidence_text}` : ""}
                              {item.confidence != null ? (
                                <span className="confidence"> {formatScore(item.confidence)}</span>
                              ) : null}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                    <div className="row-actions">
                      <span className="status">{event.status}</span>
                      <a
                        className="button-link secondary"
                        href={`${API_BASE_URL}/api/calendar/events/${event.id}.ics`}
                      >
                        <Download size={16} />
                        ICS
                      </a>
                    </div>
                  </article>
                ))
              )}
            </section>

            {duplicates.length > 0 && duplicateBaseId ? (
              <section className="duplicate-panel">
                <h3>可能重复的活动</h3>
                {duplicates.map((candidate) => (
                  <article className="duplicate-row" key={candidate.event.id}>
                    <div>
                      <strong>{candidate.event.title}</strong>
                      <p>{candidate.match_reasons.join(" · ")}</p>
                    </div>
                    <button
                      type="button"
                      className="secondary"
                      disabled={busyId === candidate.event.id}
                      onClick={() => mergeEvents(duplicateBaseId, candidate.event.id)}
                    >
                      合并进来
                    </button>
                  </article>
                ))}
              </section>
            ) : null}
          </>
        ) : null}

        {activeView === "brief" ? <BriefView events={events} reviewItems={reviewItems} /> : null}
      </section>
    </main>
  );
}

function NavButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button className={`nav-item ${active ? "active" : ""}`} type="button" onClick={onClick}>
      {children}
    </button>
  );
}

function Field({ label, value }: { label: string; value?: string | null }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value || "待确认"}</dd>
    </>
  );
}

function pageTitle(view: View) {
  if (view === "review") return "待确认队列";
  if (view === "activities") return "活动机会池";
  if (view === "brief") return "每日简报";
  return "活动收件箱";
}

function pageSubtitle(view: View) {
  if (view === "review") return "AI 先给草稿，你确认以后才入库。";
  if (view === "activities") return "这里只放已经确认过、可以继续推进的活动。";
  if (view === "brief") return "减少每天打开工具后的判断成本。";
  return "先把原文和链接收进来，下一步再做 AI 提取。";
}

function formatScore(score?: number | null) {
  if (score === null || score === undefined) return null;
  return `${Math.round(score * 100)}%`;
}

function BriefView({
  events,
  reviewItems,
}: {
  events: EventItem[];
  reviewItems: ReviewQueueItem[];
}) {
  const now = new Date();
  const threeDaysLater = new Date(now.getTime() + 3 * 24 * 60 * 60 * 1000);

  const urgentEvents = events.filter((e) => {
    if (!e.registration_deadline_at) return false;
    const deadline = new Date(e.registration_deadline_at);
    return deadline <= threeDaysLater && deadline >= now;
  });

  const missingInfoEvents = events.filter((e) =>
    [e.registration_deadline_at, e.event_start_at, e.location].some((v) => !v)
  );

  const expiredEvents = events.filter((e) => {
    if (!e.registration_deadline_at) return false;
    return new Date(e.registration_deadline_at) < now;
  });

  return (
    <section className="brief-view">
      {urgentEvents.length > 0 && (
        <BriefSection icon={<AlertTriangle size={18} />} title="3天内截止" variant="urgent">
          {urgentEvents.map((e) => (
            <div key={e.id} className="brief-item">
              <span className="brief-title">{e.title}</span>
              <span className="brief-meta">截止 {formatRelativeDate(e.registration_deadline_at!)}</span>
            </div>
          ))}
        </BriefSection>
      )}

      {reviewItems.length > 0 && (
        <BriefSection icon={<ClipboardList size={18} />} title="待确认草稿" variant="action">
          {reviewItems.map((item) => (
            <div key={item.extraction.id} className="brief-item">
              <span className="brief-title">{item.extraction.extracted.title}</span>
              <span className="brief-meta">置信度 {formatScore(item.extraction.confidence)}</span>
            </div>
          ))}
        </BriefSection>
      )}

      {missingInfoEvents.length > 0 && (
        <BriefSection icon={<Star size={18} />} title="信息缺失" variant="info">
          {missingInfoEvents.map((e) => (
            <div key={e.id} className="brief-item">
              <span className="brief-title">{e.title}</span>
              <span className="brief-meta">待补充关键信息</span>
            </div>
          ))}
        </BriefSection>
      )}

      {expiredEvents.length > 0 && (
        <BriefSection icon={<Clock size={18} />} title="已过期" variant="expired">
          {expiredEvents.map((e) => (
            <div key={e.id} className="brief-item">
              <span className="brief-title">{e.title}</span>
              <span className="brief-meta">已截止</span>
            </div>
          ))}
        </BriefSection>
      )}

      {urgentEvents.length === 0 &&
        reviewItems.length === 0 &&
        missingInfoEvents.length === 0 &&
        expiredEvents.length === 0 && (
          <p className="empty">今日无事，一切尽在掌握。</p>
        )}
    </section>
  );
}

function BriefSection({
  icon,
  title,
  variant,
  children,
}: {
  icon: ReactNode;
  title: string;
  variant: "urgent" | "action" | "info" | "expired";
  children: ReactNode;
}) {
  return (
    <article className={`brief-section ${variant}`}>
      <h3>
        {icon}
        {title}
      </h3>
      <div className="brief-list">{children}</div>
    </article>
  );
}

function formatRelativeDate(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = date.getTime() - now.getTime();
  const diffDays = Math.ceil(diffMs / (24 * 60 * 60 * 1000));

  if (diffDays === 0) return "今天";
  if (diffDays === 1) return "明天";
  if (diffDays < 0) return "已过期";
  return `${diffDays}天后`;
}
