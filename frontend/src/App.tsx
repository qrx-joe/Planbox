import {
  Bell,
  ClipboardList,
  Download,
  Inbox,
  Radar,
  RefreshCw,
  Search,
  Sparkles,
  Upload,
  X,
} from "lucide-react";
import { FormEvent, ReactNode, useEffect, useState } from "react";
import { deadlineStatus, formatRelativeDate, timeReviewMessage, type TimeIssue } from "./time";
import { isFetchableLink } from "./link-rules";
import { ActivityDetails } from "./ActivityDetails";
import { ReviewCard, type ReviewQueueItem } from "./ReviewCard";
import { fieldLabel, requestDetail } from "./activity-details";

type View = "inbox" | "review" | "activities" | "brief";

type SourceItem = {
  id: string;
  source_type: string;
  source_url?: string | null;
  raw_text?: string | null;
  title?: string | null;
  captured_at: string;
  parse_status: string;
  ingest_state?: string | null;
  source_meta?: Record<string, unknown> | null;
};

type ShareIntakeResult = {
  source_item_id: string;
  state: "needs_content" | "ready";
  title?: string | null;
  source_url?: string | null;
  is_wechat_article: boolean;
  fetch_attempted: boolean;
  fetch_succeeded: boolean;
  fetch_error?: string | null;
  next_action: string;
  extraction_started: boolean;
};

/** 分享唤起参数，可从小程序分享或 URL query 读入，用于预填 */
type SharePayload = {
  scene?: string | null;
  query?: string | null;
  title?: string | null;
  summary?: string | null;
  article_url?: string | null;
  webview_url?: string | null;
  referer?: string | null;
};

/** 抓取失败后待用户补全的条目 */
type PendingContent = {
  id: string;
  nextAction: string;
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
  time_issues?: TimeIssue[];
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

/**
 * 从 URL query 读取分享唤起参数。
 *
 * 小程序分享到 Web 或从外部带参打开时，参数会挂在地址栏上。
 * 微信不会告知用户正在读哪篇文章，所以这里能拿到的只是
 * 分享者预置的 title / q / article_url，作为预填使用。
 */
function readSharePayloadFromUrl(): SharePayload | null {
  const params = new URLSearchParams(window.location.search);
  const keys = ["scene", "q", "title", "summary", "article_url", "referer"];
  const found = keys.filter((key) => params.get(key));
  if (found.length === 0) return null;

  return {
    scene: params.get("scene"),
    query: params.get("q"),
    title: params.get("title"),
    summary: params.get("summary"),
    article_url: params.get("article_url"),
    referer: params.get("referer"),
  };
}

type EventFilters = {
  keyword: string;
  status: string;
  deadlineWithinDays: number;
};

const STATUS_OPTIONS = [
  { value: "", label: "全部" },
  { value: "interested", label: "感兴趣" },
  { value: "applied", label: "已报名" },
  { value: "rejected", label: "已放弃" },
  { value: "ongoing", label: "进行中" },
  { value: "done", label: "已结束" },
  { value: "expired", label: "已过期" },
] as const;

/** event.status → 展示文案；未知值原样显示，不编造状态 */
const STATUS_LABELS: Record<string, string> = Object.fromEntries(
  STATUS_OPTIONS.map((option) => [option.value, option.label])
);

/** source_item.parse_status → 展示文案；未识别的枚举值保留原文 */
const PARSE_STATUS_LABELS: Record<string, string> = {
  pending: "待抽取",
  unparsed: "未抽取",
  parsed: "已解析",
  failed: "抽取失败",
  no_event: "未发现活动",
};

/** 收集条目的来源标识：图标底色与缩写 */
const SOURCE_MARKS: Record<string, { cls: string; label: string; name: string }> = {
  wechat: { cls: "wx", label: "微", name: "微信公众号" },
  xhs: { cls: "note", label: "书", name: "小红书" },
  web: { cls: "web", label: "↗", name: "网页链接" },
  text: { cls: "note", label: "文", name: "粘贴正文" },
};

const MONTH_LABELS = [
  "", "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
  "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
];

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

/** 直接取 ISO 串的日期部分，不做时区换算——仅日期与带时刻统一按原文展示日 */
function dateBlockParts(value: string | null | undefined): { day: string; month: string } | null {
  const match = value?.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return null;
  const month = MONTH_LABELS[Number(match[2])];
  return month ? { day: match[3], month } : null;
}

function sourceTypeLabel(sourceType: string): string {
  return SOURCE_MARKS[sourceType]?.name ?? "其他来源";
}

function formatCaptured(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

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
  const [duplicates, setDuplicates] = useState<DuplicateCandidate[]>([]);
  const [duplicateBaseId, setDuplicateBaseId] = useState<string | null>(null);
  const [sharePayload, setSharePayload] = useState<SharePayload | null>(null);
  const [pendingContent, setPendingContent] = useState<PendingContent | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  // Brief 用独立全量数据：不继承机会库页的筛选结果，避免筛选期间漏看关键节点
  const [briefEvents, setBriefEvents] = useState<EventItem[] | null>(null);
  const [briefError, setBriefError] = useState(false);
  const [briefReload, setBriefReload] = useState(0);

  useEffect(() => {
    void refreshAll();
  }, []);

  useEffect(() => {
    void loadEvents();
  }, [filters]);

  useEffect(() => {
    if (activeView !== "brief") return;
    let cancelled = false;
    setBriefError(false);
    void (async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/api/events?paginated=false`);
        if (!response.ok) throw new Error("brief load failed");
        const data: EventItem[] = await response.json();
        if (!cancelled) setBriefEvents(data);
      } catch {
        if (!cancelled) setBriefError(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeView, briefReload]);

  // 分享唤起：读URL 参数做预填，不自动提交，等用户确认
  useEffect(() => {
    const payload = readSharePayloadFromUrl();
    if (!payload) return;
    setSharePayload(payload);
    setActiveView("inbox");
    // 分享标题先填进链接框，正文仍需用户粘贴
    if (payload.article_url && !sourceUrl) {
      setSourceUrl(payload.article_url);
    } else if (!payload.article_url && payload.title) {
      setRawText(payload.title);
    }
    if (payload.summary && !rawText) {
      setRawText(payload.summary);
    }
  }, []);

  /** 走分享入口提交。抓不到正文时引导用户粘贴，不报错。 */
  async function submitShareIntake() {
    if (!sharePayload) return;
    setIsSaving(true);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/share/intake`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sharePayload),
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || "分享入口提交失败");
      }
      const data: ShareIntakeResult = await response.json();

      if (data.state === "needs_content") {
        setPendingContent({ id: data.source_item_id, nextAction: data.next_action });
        setMessage(data.next_action);
      } else {
        setMessage("已收到正文，已生成待确认草稿");
        setSharePayload(null);
      }
      await loadItems();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "分享入口提交失败");
    } finally {
      setIsSaving(false);
    }
  }

  /** 用户粘贴正文后补全分享入口建立的条目 */
  async function completeContent() {
    if (!pendingContent) return;
    const text = rawText.trim();
    if (text.length < 50) {
      setMessage("正文太短，至少 50 字。请在文章页全选复制完整正文。");
      return;
    }
    setIsSaving(true);
    setMessage(null);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/share/${pendingContent.id}/complete`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ raw_text: text, auto_extract: true }),
        }
      );
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || "补全失败");
      }
      const data = await response.json();
      if (data?.parse_status === "no_event") {
        setMessage("正文已补全；未发现活动：这条内容里没有可提取的活动信息");
      } else {
        setMessage("正文已补全，已生成待确认草稿");
      }
      setPendingContent(null);
      setRawText("");
      setSharePayload(null);
      setActiveView("review");
      await refreshAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "补全失败");
    } finally {
      setIsSaving(false);
    }
  }

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
      return true;
    }
    return false;
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
      // 微信文章与小红书笔记走专门的抓取接口。判定规则与后端一致，
      // 不能用子串包含——那样会把 evil.com/?x=mp.weixin.qq.com 误判成微信链接。
      const shouldFetch = isFetchableLink(sourceUrl);
      const endpoint = shouldFetch
        ? `${API_BASE_URL}/api/source-items/fetch`
        : `${API_BASE_URL}/api/source-items`;
      // 抓取接口的链接走请求体：URL 里带完整外链会被 CDN 层 WAF 拦成403
      const body = JSON.stringify(
        shouldFetch
          ? { source_url: sourceUrl }
          : {
              source_type: sourceUrl ? "web" : "text",
              source_url: sourceUrl || null,
              raw_text: rawText || null,
            }
      );

      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || "保存失败");
      }

      if (shouldFetch) {
        // 抓取失败是常态（微信风控、小红书正文不在服务端），引导用户粘贴正文而非报错
        const data = await response.json();
        if (data.status === "needs_content") {
          setPendingContent({
            id: data.source_item.id,
            nextAction: data.next_action,
          });
          setMessage(data.next_action);
        } else {
          setMessage("已抓取文章并生成待确认草稿");
        }
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
      const data = await response.json();
      if (data?.status === "no_event") {
        // 原文不含活动：如实告知，不建草稿也不切换视图
        setMessage(data.message || "未发现活动：这条内容里没有可提取的活动信息");
        await refreshAll();
        return;
      }
      const extra = Number(data?.additional_extractions ?? 0);
      setMessage(
        extra > 0
          ? `发现 ${extra + 1} 个活动候选，已全部生成待核对草稿`
          : "已生成待确认活动草稿"
      );
      setActiveView("review");
      await refreshAll();
    } finally {
      setBusyId(null);
    }
  }

  async function confirmReview(extractionId: string, revision: number) {
    setBusyId(extractionId);
    setMessage(null);
    try {
      await requestDetail<EventItem>(`${API_BASE_URL}/api/review-queue/${extractionId}/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // 确认事实 ≠ 决定参加：入库时只标记待核对，参与状态由用户在机会库单独选择
        body: JSON.stringify({ status: "needs_review", expected_revision: revision }),
      });
      setMessage("已确认入库；是否参加请在机会库标记（感兴趣/已报名/放弃）");
      await refreshAll();
    } catch (error) {
      setMessage(error instanceof TypeError ? "确认结果未知，请先检查活动库再重试"
        : error instanceof Error ? error.message : "确认失败，请重新核对");
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
        const error = await response.json().catch(() => ({}));
        throw new Error(typeof error.detail === "string" ? error.detail : "放弃失败");
      }
      setMessage("已放弃该草稿");
      await refreshAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "放弃失败，请重新读取");
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

  /** 顶栏"快速收集"：切回收集箱并聚焦链接输入框，不新增弹窗 */
  function focusComposer() {
    setActiveView("inbox");
    window.setTimeout(() => {
      document.getElementById("composer-input")?.focus();
    }, 30);
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Radar size={19} />
          </div>
          <div>
            <div className="brand-name">Planbox</div>
            <div className="brand-caption">OPPORTUNITY INTELLIGENCE</div>
          </div>
        </div>
        <div className="nav-caption">WORKSPACE</div>
        <nav className="nav" aria-label="主导航">
          <NavButton label="Inbox" active={activeView === "inbox"} onClick={() => setActiveView("inbox")} count={items.length}>
            <Inbox size={17} />
            <span>Inbox</span>
          </NavButton>
          <NavButton label="Review" active={activeView === "review"} onClick={() => setActiveView("review")} count={reviewItems.length}>
            <ClipboardList size={17} />
            <span>Review</span>
          </NavButton>
          <NavButton label="Activities" active={activeView === "activities"} onClick={() => setActiveView("activities")} count={events.length}>
            <Search size={17} />
            <span>Activities</span>
          </NavButton>
          <NavButton label="Brief" active={activeView === "brief"} onClick={() => setActiveView("brief")}>
            <Bell size={17} />
            <span>Brief</span>
          </NavButton>
        </nav>
        <div className="sidebar-spacer" />
        <div className="profile">
          <div className="avatar">P</div>
          <div>
            <div className="profile-name">个人工作空间</div>
            <div className="profile-role">Planbox</div>
          </div>
        </div>
      </aside>

      <section className="content">
        <div className="topline">
          <div className="crumb">
            工作空间　/　<b>{crumbName(activeView)}</b>
          </div>
          <div className="top-actions">
            <button type="button" className="icon-btn" aria-label="刷新" onClick={refreshAll}>
              <RefreshCw size={16} />
            </button>
            <button type="button" onClick={focusComposer}>
              <span>＋</span> 快速收集
            </button>
          </div>
        </div>

        <header className="page-heading">
          <div>
            <p className="eyebrow">{pageEyebrow(activeView)}</p>
            <h1>{pageTitle(activeView)}</h1>
            <p>{pageSubtitle(activeView)}</p>
          </div>
        </header>

        {message ? <p className="notice">{message}</p> : null}

        {activeView === "inbox" ? (
          <>
            {sharePayload ? (
              <section className="share-banner">
                <div>
                  <strong>从微信分享进入</strong>
                  <p>{sharePayload.title || sharePayload.article_url || "已接收分享参数"}</p>
                </div>
                <button
                  type="button"
                  disabled={isSaving}
                  onClick={() => void submitShareIntake()}
                >
                  {isSaving ? "处理中" : "确认并抓取"}
                </button>
              </section>
            ) : null}

            {pendingContent ? (
              <section className="pending-panel">
                <h3>需要补全正文</h3>
                <p>{pendingContent.nextAction}</p>
                <label>
                  在微信文章页长按全选复制正文，然后粘贴到这里
                  <textarea
                    value={rawText}
                    onChange={(event) => setRawText(event.target.value)}
                    placeholder="粘贴完整正文，AI 才能提取报名截止、举办时间等关键信息"
                  />
                </label>
                <div className="composer-actions">
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => setPendingContent(null)}
                  >
                    取消
                  </button>
                  <button
                    type="button"
                    disabled={isSaving || rawText.trim().length < 50}
                    onClick={() => void completeContent()}
                  >
                    {isSaving ? "补全中" : "补全并生成草稿"}
                  </button>
                </div>
              </section>
            ) : null}

            <div className="hero-grid">
              <section className="radar-card">
                <div className="radar-copy">
                  <div className="radar-kicker">
                    <i className="live-dot" /> YOUR OPPORTUNITY RADAR
                  </div>
                  <h2>
                    你的下一次机会，
                    <br />
                    可能就在这里。
                  </h2>
                  <p>把活动、比赛与行业信息收进来。先看清事实，再决定值不值得投入。</p>
                  <div className="radar-meta">
                    <div>
                      <strong>{pad2(items.length)}</strong>
                      <span>条待整理线索</span>
                    </div>
                    <div>
                      <strong>{pad2(reviewItems.length)}</strong>
                      <span>条待人工核对</span>
                    </div>
                  </div>
                  <button type="button" className="radar-cta" onClick={() => setActiveView("activities")}>
                    探索机会库 <span>↗</span>
                  </button>
                </div>
                <div className="radar-visual" aria-hidden="true">
                  <div className="radar-sweep" />
                  <div className="orbit four" />
                  <div className="orbit three" />
                  <div className="orbit two" />
                  <div className="orbit one" />
                  <div className="radar-core">
                    <Radar size={20} />
                  </div>
                  <i className="radar-point p1" />
                  <i className="radar-point p2" />
                  <i className="radar-point p3" />
                </div>
              </section>

              <section className="queue-card">
                <div className="panel-head">
                  <div>
                    <h3 className="panel-title">待你确认</h3>
                    <p className="panel-sub">AI 已整理，关键事实需要你把关</p>
                  </div>
                  <span className="status pending">{pad2(reviewItems.length)} 待核对</span>
                </div>
                <div className="queue-list">
                  {reviewItems.length === 0 ? (
                    <p className="queue-empty">当前没有待核对草稿。新收集的内容抽取后会出现在这里。</p>
                  ) : (
                    reviewItems.slice(0, 3).map((item, index) => (
                      <button
                        type="button"
                        className="queue-row"
                        key={item.extraction.id}
                        onClick={() => setActiveView("review")}
                      >
                        <span className={`queue-icon ${["blue", "orange", "purple"][index % 3]}`}>
                          <ClipboardList size={15} />
                        </span>
                        <span className="queue-main">
                          <b>{item.extraction.extracted.title || "未命名草稿"}</b>
                          <span>
                            {item.extraction.missing_fields.length > 0
                              ? `待补：${fieldLabel(item.extraction.missing_fields[0])}`
                              : "关键字段待人工核对"}
                          </span>
                        </span>
                      </button>
                    ))
                  )}
                </div>
                <div className="queue-footer">
                  <span>确认不是走过场，是质量控制。</span>
                  <button type="button" className="text-link" onClick={() => setActiveView("review")}>
                    进入 Review →
                  </button>
                </div>
              </section>
            </div>

            <div className="stat-strip">
              <div className="stat">
                <div className="stat-icon blue">
                  <Inbox size={15} />
                </div>
                <div>
                  <div className="stat-label">累计收集</div>
                  <div className="stat-value">{items.length}</div>
                </div>
                <span className="stat-foot">链接 / 正文 / 分享</span>
              </div>
              <div className="stat">
                <div className="stat-icon orange">
                  <ClipboardList size={15} />
                </div>
                <div>
                  <div className="stat-label">待核对草稿</div>
                  <div className="stat-value">{pad2(reviewItems.length)}</div>
                </div>
                <span className="stat-foot">需要人工确认</span>
              </div>
              <div className="stat">
                <div className="stat-icon green">
                  <Sparkles size={15} />
                </div>
                <div>
                  <div className="stat-label">已确认机会</div>
                  <div className="stat-value">{events.length}</div>
                </div>
                <span className="stat-foot">当前筛选内</span>
              </div>
            </div>

            <div className="lower-grid">
              <section className="panel">
                <div className="panel-head">
                  <div>
                    <h3 className="panel-title">最近收集</h3>
                    <p className="panel-sub">从链接、正文和分享入口汇总</p>
                  </div>
                </div>
                <div className="source-list feed-list">
                  {items.length === 0 ? (
                    <p className="empty">还没有收集内容。先扔一条真实活动进来。</p>
                  ) : (
                    items.map((item) => {
                      const mark = SOURCE_MARKS[item.source_type] ?? {
                        cls: "web",
                        label: "↗",
                        name: "其他来源",
                      };
                      return (
                        <article className="source-row feed-row" key={item.id}>
                          <div className="source-mark-cell">
                            <span className={`source-mark ${mark.cls}`}>{mark.label}</span>
                          </div>
                          <div className="feed-copy">
                            <b>{item.title || item.source_url || "未命名活动线索"}</b>
                            <span>
                              {sourceTypeLabel(item.source_type)} · {formatCaptured(item.captured_at)}
                            </span>
                          </div>
                          <div className="row-actions">
                            {item.ingest_state === "needs_content" ? (
                              <button
                                type="button"
                                className="status pending"
                                onClick={() =>
                                  setPendingContent({
                                    id: item.id,
                                    nextAction:
                                      "这条还没有正文。请在微信文章页全选复制正文，粘贴到上方补全。",
                                  })
                                }
                              >
                                待补正文
                              </button>
                            ) : (
                              <span className={`status ${item.parse_status}`}>
                                {PARSE_STATUS_LABELS[item.parse_status] ?? item.parse_status}
                              </span>
                            )}
                            <button
                              type="button"
                              className="secondary"
                              disabled={busyId === item.id || item.ingest_state === "needs_content"}
                              title={
                                item.ingest_state === "needs_content"
                                  ? "需要先补全正文"
                                  : "AI 抽取"
                              }
                              onClick={() => extractSource(item.id)}
                            >
                              <Sparkles size={15} />
                              {busyId === item.id ? "抽取中" : "AI 抽取"}
                            </button>
                          </div>
                        </article>
                      );
                    })
                  )}
                </div>
              </section>

              <section className="panel deadline-panel">
                <div className="panel-head">
                  <div>
                    <h3 className="panel-title">临近节点</h3>
                    <p className="panel-sub">只展示已知时间，不替你猜截止日</p>
                  </div>
                  <span className="section-kicker">NEXT UP</span>
                </div>
                <div className="deadline-list">
                  {upcomingDeadlines(events).map(({ event, at, kind }) => {
                    const parts = dateBlockParts(at);
                    return (
                      <div className="deadline-row" key={event.id}>
                        <div className="date-block">
                          <strong>{parts?.day ?? "--"}</strong>
                          <span>{parts?.month ?? "待核"}</span>
                        </div>
                        <div className="deadline-copy">
                          <b>{event.title}</b>
                          <span>
                            {kind === "deadline" ? "报名截止" : "活动开始"} ·{" "}
                            {formatRelativeDate(at)}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="empty-hint">
                  时间存在冲突或缺少年份时，会先标记待核对，不会自动排进计划。
                </div>
              </section>
            </div>

            <div className="page-toolbar">
              <h2>快速收集一条新线索</h2>
              <span className="section-kicker">CAPTURE FIRST</span>
            </div>

            <form className="composer" onSubmit={handleSubmit}>
              <label>
                链接
                <input
                  id="composer-input"
                  value={sourceUrl}
                  onChange={(event) => setSourceUrl(event.target.value)}
                  placeholder="粘贴微信公众号、小红书或网页链接…"
                />
              </label>
              <label>
                正文
                <textarea
                  value={rawText}
                  onChange={(event) => setRawText(event.target.value)}
                  placeholder="也可以直接粘贴活动介绍、报名通知或正文…"
                />
              </label>
              <div className="composer-actions">
                <button type="button" className="secondary" title="Upload screenshot">
                  <Upload size={16} />
                  截图稍后接入
                </button>
                <button type="submit" disabled={isSaving || (!rawText && !sourceUrl)}>
                  {isSaving ? "保存中" : "收进 Inbox ↗"}
                </button>
              </div>
              <p className="collect-hint">
                原始内容会保留。AI 提取结果进入 Review 后，仍需人工核对关键字段。
              </p>
            </form>
          </>
        ) : null}

        {activeView === "review" ? (
          <>
            <div className="page-toolbar">
              <h2>
                待确认草稿 <span className="toolbar-note">/ {pad2(reviewItems.length)}</span>
              </h2>
              <span className="section-kicker">REVIEW QUEUE</span>
            </div>
            <section className="review-list">
              {reviewItems.length === 0 ? (
                <p className="empty">没有待确认草稿。很好，至少这里暂时不是第二个垃圾桶。</p>
              ) : (
                reviewItems.map((item) => (
                  <ReviewCard key={item.extraction.id} item={item} apiBase={API_BASE_URL}
                    onSaved={async () => {
                      if (!await loadReviewQueue()) throw new Error("草稿列表读取失败");
                    }} onConfirm={confirmReview} onReject={rejectReview} />
                ))
              )}
            </section>
          </>
        ) : null}

        {activeView === "activities" ? (
          <>
            <div className="page-toolbar">
              <h2>
                已确认活动 <span className="toolbar-note">{events.length} 条 · 当前筛选内</span>
              </h2>
              <a className="button-link" href={`${API_BASE_URL}/api/calendar/export.ics`}>
                <Download size={16} />
                导出待行动日历
              </a>
            </div>

            <section className="filter-bar">
              <div className="search-field">
                <Search size={14} />
                <input
                  type="search"
                  aria-label="搜索活动"
                  placeholder="搜索活动名称、主办方、地点…"
                  value={filters.keyword}
                  onChange={(e) => setFilters({ ...filters, keyword: e.target.value })}
                />
              </div>
              <div className="filter-chips">
                {STATUS_OPTIONS.map((option) => (
                  <button
                    type="button"
                    key={option.value}
                    className={`filter-chip ${filters.status === option.value ? "active" : ""}`}
                    onClick={() => setFilters({ ...filters, status: option.value })}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <select
                value={filters.deadlineWithinDays}
                aria-label="截止时间筛选"
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
                events.map((event) => {
                  const dateParts = dateBlockParts(event.event_start_at ?? event.registration_deadline_at);
                  return (
                    <article className="source-row" key={event.id}>
                      <div className="source-date">
                        <b>{dateParts?.day ?? "--"}</b>
                        <span>{dateParts?.month ?? "待核"}</span>
                      </div>
                      <div>
                        <h3>{event.title}</h3>
                        <p>
                          {[event.registration_deadline_at, event.event_start_at, event.location]
                            .filter(Boolean)
                            .join(" · ") || "时间地点待补充"}
                        </p>
                        {event.event_type || event.time_issues?.length || !event.registration_deadline_at ? (
                          <div className="activity-tags">
                            {event.event_type ? <span className="tag">{event.event_type}</span> : null}
                            {event.time_issues?.length ? (
                              <span className="tag">时间待核对</span>
                            ) : null}
                            {!event.registration_deadline_at ? (
                              <span className="tag">截止未知</span>
                            ) : null}
                          </div>
                        ) : null}
                        {event.time_issues?.length ? (
                          <p className="missing">时间待核对：{timeReviewMessage(event.time_issues)}</p>
                        ) : null}
                        <div className="inline-actions">
                          <select
                            className="status-select"
                            aria-label="修改活动状态"
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
                            onClick={() => checkDuplicates(event.id)}
                          >
                            检查重复
                          </button>
                        </div>
                        <ActivityDetails eventId={event.id} apiBase={API_BASE_URL} onUpdated={loadEvents} />
                      </div>
                      <div className="row-actions">
                        <span className={`status ${event.status}`}>
                          {STATUS_LABELS[event.status] ?? event.status}
                        </span>
                        <a
                          className="button-link secondary"
                          href={`${API_BASE_URL}/api/calendar/events/${event.id}.ics`}
                        >
                          <Download size={15} />
                          ICS
                        </a>
                      </div>
                    </article>
                  );
                })
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

        {activeView === "brief" ? (
          briefError ? (
            <section className="empty">
              <p>简报数据读取失败，请检查后端服务后重试。</p>
              <button
                type="button"
                className="secondary"
                onClick={() => setBriefReload((value) => value + 1)}
              >
                重试
              </button>
            </section>
          ) : briefEvents === null ? (
            <p className="empty">简报加载中…</p>
          ) : (
            <BriefView events={briefEvents} reviewItems={reviewItems} />
          )
        ) : null}
      </section>
    </main>
  );
}

/** 已知且未过期的时间节点，按时间升序取前 4 条；needs_review/unknown 不进列表 */
type UpcomingNode = { event: EventItem; at: string; kind: "deadline" | "start" };

function upcomingDeadlines(events: EventItem[]): UpcomingNode[] {
  return events
    .flatMap<UpcomingNode>((event) => {
      if (event.registration_deadline_at) {
        return [{ event, at: event.registration_deadline_at, kind: "deadline" as const }];
      }
      if (event.event_start_at) {
        return [{ event, at: event.event_start_at, kind: "start" as const }];
      }
      return [];
    })
    .filter(({ at }) => {
      const status = deadlineStatus(at);
      return status !== "expired" && status !== "needs_review" && status !== "unknown";
    })
    .sort((a, b) => a.at.localeCompare(b.at))
    .slice(0, 4);
}

function NavButton({
  active,
  onClick,
  label,
  count,
  children,
}: {
  active: boolean;
  onClick: () => void;
  /** aria-label 与可见文案一致；数量徽章对辅助技术隐藏，避免读成按钮名的一部分 */
  label: string;
  count?: number;
  children: ReactNode;
}) {
  return (
    <button className={`nav-item ${active ? "active" : ""}`} type="button" aria-label={label} onClick={onClick}>
      {children}
      {count && count > 0 ? (
        <span className="nav-count" aria-hidden="true">
          {pad2(count)}
        </span>
      ) : null}
    </button>
  );
}

function crumbName(view: View) {
  if (view === "review") return "待核对";
  if (view === "activities") return "机会库";
  if (view === "brief") return "机会简报";
  return "收集箱";
}

function pageEyebrow(view: View) {
  if (view === "review") return "HUMAN IN THE LOOP";
  if (view === "activities") return "YOUR OPPORTUNITY LIBRARY";
  if (view === "brief") return "A CLEARER VIEW OF WHAT'S NEXT";
  const now = new Date();
  const weekday = new Intl.DateTimeFormat("en-US", { weekday: "long" }).format(now);
  const day = new Intl.DateTimeFormat("en-US", { day: "2-digit" }).format(now);
  const month = new Intl.DateTimeFormat("en-US", { month: "short" }).format(now);
  return `${weekday} · ${day} ${month} ${now.getFullYear()}`.toUpperCase();
}

function pageTitle(view: View) {
  if (view === "review") return "先核对事实，再做决定。";
  if (view === "activities") return "值得关注的，不止是收藏。";
  if (view === "brief") return "把注意力留给重要的事。";
  return "把机会，变成下一步。";
}

function pageSubtitle(view: View) {
  if (view === "review") return "AI 帮你整理信息，最终确认权仍然在你手里。";
  if (view === "activities") return "集中查看已确认的活动，并持续更新参与状态。";
  if (view === "brief") return "汇总近期机会、关键节点与尚未解决的信息。";
  return "散落在各处的信息，值得被认真整理。";
}

function BriefView({
  events,
  reviewItems,
}: {
  events: EventItem[];
  reviewItems: ReviewQueueItem[];
}) {
  const now = new Date();
  const urgentEvents = events.filter((e) => deadlineStatus(e.registration_deadline_at, now) === "urgent");

  const missingInfoEvents = events.filter((e) =>
    [e.registration_deadline_at, e.event_start_at, e.location].some((v) => !v) ||
    Boolean(e.time_issues?.length)
  );

  const expiredEvents = events.filter((e) => deadlineStatus(e.registration_deadline_at, now) === "expired");

  type Step = { key: string; title: string; meta: string; badge: string; badgeStatus: string };
  const steps: Step[] = [
    ...urgentEvents.slice(0, 3).map<Step>((e) => ({
      key: `urgent-${e.id}`,
      title: `报名截止临近：${e.title}`,
      meta: `截止 ${formatRelativeDate(e.registration_deadline_at!)}`,
      badge: "紧急",
      badgeStatus: "failed",
    })),
    ...reviewItems.slice(0, 3).map<Step>((item) => ({
      key: `review-${item.extraction.id}`,
      title: item.extraction.extracted.title || "未命名草稿",
      meta: "草稿待人工确认后入库",
      badge: "待核对",
      badgeStatus: "pending",
    })),
    ...missingInfoEvents.slice(0, 2).map<Step>((e) => ({
      key: `missing-${e.id}`,
      title: `补充「${e.title}」的关键信息`,
      meta: e.time_issues?.length ? "时间待核对" : "待补充关键信息",
      badge: "待补全",
      badgeStatus: "pending",
    })),
    ...expiredEvents.slice(0, 2).map<Step>((e) => ({
      key: `expired-${e.id}`,
      title: e.title,
      meta: "报名已截止，可更新状态或放弃",
      badge: "已过期",
      badgeStatus: "unparsed",
    })),
  ];

  return (
    <section className="brief-view">
      <div className="brief-feature">
        <div className="radar-kicker">
          <i className="live-dot" /> THIS WEEK AT A GLANCE
        </div>
        <h2>本周机会简报</h2>
        <p>先处理事实不完整的线索，再决定是否投入时间。不要让收藏数量冒充进展。</p>
        <div className="brief-number">
          {pad2(reviewItems.length)} <span>条信息需要你确认</span>
        </div>
      </div>

      <div className="brief-side">
        <div className="brief-mini">
          <b>{pad2(urgentEvents.length)}</b>
          <span>3 天内截止</span>
        </div>
        <div className="brief-mini">
          <b>{pad2(missingInfoEvents.length)}</b>
          <span>信息待核对</span>
        </div>
      </div>

      <div className="page-toolbar">
        <h2>下一步建议</h2>
        <span className="section-kicker">NEXT ACTIONS</span>
      </div>

      <section className="panel">
        <div className="brief-list">
          {steps.length === 0 ? (
            <p className="empty">今日无事，一切尽在掌握。</p>
          ) : (
            steps.map((step, index) => (
              <div className="brief-item" key={step.key}>
                <div className="date-block">
                  <strong>{pad2(index + 1)}</strong>
                  <span>STEP</span>
                </div>
                <div className="deadline-copy">
                  <b>{step.title}</b>
                  <span>{step.meta}</span>
                </div>
                <span className={`status ${step.badgeStatus}`}>{step.badge}</span>
              </div>
            ))
          )}
        </div>
      </section>

      <div className="empty-hint">
        按目标评估机会、拆分准备任务并生成具体时段计划，属于下一阶段功能；此处只汇总已确认的事实。
      </div>
    </section>
  );
}
