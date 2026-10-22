/** 日期按展示日判断，只有明确偏移的时刻才参与倒计时；未知时间不猜成 UTC。 */
export type DeadlineStatus = "unknown" | "needs_review" | "expired" | "urgent" | "future";

export type TimeIssue = { field: string; code: string };
const DAY_MS = 86400000;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const DATETIME = /^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:[Zz]|[+-]\d{2}:\d{2})$/;

function validDay(value: string): boolean {
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

function kind(value: string): "date" | "datetime" | "needs_review" {
  if (DATE.test(value) && validDay(value)) return "date";
  const offset = value.match(/[+-](\d{2}):(\d{2})$/);
  if (DATETIME.test(value) && validDay(value.slice(0, 10)) &&
      Number(value.slice(11, 13)) < 24 && Number(value.slice(14, 16)) < 60 &&
      !value.endsWith("-00:00") &&
      (!offset || (Number(offset[1]) <= 23 && Number(offset[2]) <= 59)) &&
      Number.isFinite(new Date(value).getTime())) return "datetime";
  return "needs_review";
}

function localDay(value: Date, timezone: string): string {
  const parts = new Intl.DateTimeFormat("en", {
    timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(value);
  const field = (name: string) => parts.find((part) => part.type === name)!.value;
  return `${field("year").padStart(4, "0")}-${field("month")}-${field("day")}`;
}

function calendarDays(value: string, now: Date, timezone: string): number {
  const day = kind(value) === "date" ? value : localDay(new Date(value), timezone);
  return (Date.parse(`${day}T00:00:00Z`) - Date.parse(`${localDay(now, timezone)}T00:00:00Z`)) / DAY_MS;
}

/** date 按日历日判断过期；精确时间按毫秒比较。默认展示时区 Asia/Shanghai。 */
export function deadlineStatus(
  value: string | null | undefined, now = new Date(), timezone = "Asia/Shanghai",
): DeadlineStatus {
  if (value == null) return "unknown";
  const precision = kind(value);
  if (precision === "needs_review") return "needs_review";
  const distance = precision === "date" ? calendarDays(value, now, timezone) :
    (new Date(value).getTime() - now.getTime()) / DAY_MS;
  if (distance < 0) return "expired";
  return distance <= 3 ? "urgent" : "future";
}

/** 显式显示仅日期精度；未经核对的内容不显示“今天/已过期”等确定结论。 */
export function formatRelativeDate(value: string, now = new Date(), timezone = "Asia/Shanghai"): string {
  const status = deadlineStatus(value, now, timezone);
  if (status === "needs_review") return "时间待核对";
  if (status === "expired") return "已过期";
  const days = calendarDays(value, now, timezone);
  const label = days === 0 ? "今天" : days === 1 ? "明天" : `${days}天后`;
  return kind(value) === "date" ? `${label}（仅日期，时刻未知）` : label;
}

/** 把服务端核对信息转成人可读提示；字段路径仅用于识别，不作为页面文案。 */
export function timeReviewMessage(issues: TimeIssue[] | undefined): string {
  const labels: Record<string, string> = {
    registration_start_at: "报名开始", registration_deadline_at: "报名截止",
    event_start_at: "活动开始", event_end_at: "活动结束",
  };
  const reasons: Record<string, string> = {
    invalid: "日期无效", missing_timezone: "时区未确认", incomplete: "日期未确认",
    mixed_precision: "区间精度不一致", range_order: "结束早于或等于开始",
  };
  return [...new Set((issues ?? []).map((issue) =>
    `${labels[issue.field] ?? "赛程时间"}：${reasons[issue.code] ?? "需要核对"}`,
  ))].join("；");
}
