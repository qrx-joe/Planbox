/** 活动核对页的数据契约与纯展示规则；来源位置和人工决定分别表达。 */
import type { TimeIssue } from "./time";
export const EDIT_FIELDS = [
  ["title", "标题"], ["event_type", "类型"], ["organizer", "主办方"],
  ["registration_url", "报名链接"], ["registration_start_at", "报名开始"],
  ["registration_deadline_at", "报名截止"], ["event_start_at", "活动开始"],
  ["event_end_at", "活动结束"], ["location", "地点"], ["is_online", "是否线上"],
  ["fee", "费用"], ["prize", "奖项"], ["team_required", "组队要求"],
  ["audience", "参与对象"], ["schedule", "赛程"], ["notes", "备注"],
] as const;
export type EditField = (typeof EDIT_FIELDS)[number][0];
export type ActivityRecord = {
  id: string; title: string; time_issues?: TimeIssue[]; [key: string]: unknown;
};
export type DraftRecord = { id: string; status: string; extracted: Omit<ActivityRecord, "id"> & {
  title: string; time_issues?: TimeIssue[];
} };
export type EvidenceItem = {
  id: string;
  source_item_id: string;
  field_name: string;
  field_value: string | null;
  evidence_text: string | null;
  verification_status: "located" | "unverified";
  value_relation: string;
  start_offset: number | null;
  end_offset: number | null;
  source_title?: string | null;
  source_url?: string | null;
  extraction_result_id?: string | null;
};
export type Correction = {
  id: string;
  operation_id: string;
  field_name: string;
  old_value: unknown;
  new_value: unknown;
  actor: string;
  reason: string | null;
  source_item_id: string | null;
  action: "correction" | "retraction";
  reverts_id: string | null;
  created_at: string;
  can_retract: boolean;
  operation_field_count: number;
};
export type CorrectionPage = { items: Correction[]; total: number; revision: number };
export type ScheduleNode = Record<string, string | null>;

export function fieldLabel(field: string): string {
  const known = EDIT_FIELDS.find(([key]) => key === field);
  if (known) return known[1];
  if (field.startsWith("schedule.")) return "赛程节点";
  return "其他字段";
}

export function displayValue(value: unknown): string {
  if (value == null || value === "") return "未知";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.length ? value.map(displayValue).join("\n") : "无赛程节点";
  if (typeof value === "object") return Object.entries(value).map(([key, item]) => {
    const label = key === "name" ? "名称" : key === "start_at" ? "开始"
      : key === "end_at" ? "结束" : key === "description" ? "说明" : fieldLabel(key);
    return `${label}：${displayValue(item)}`;
  }).join("；");
  return String(value);
}

export function relationLabel(value: string): string {
  const labels: Record<string, string> = {
    matches_current: "与当前值一致", differs_from_current: "与当前值不同，需核对",
    no_current_value: "当前值未知", draft: "草稿候选", unknown_field: "字段关系待核对",
    unavailable: "当前活动不可读取",
    matches_draft: "与当前草稿一致", differs_from_draft: "与当前草稿不同，需核对",
    no_draft_value: "当前草稿值未知",
  };
  return labels[value] ?? "关系待核对";
}

/** 只允许用户主动打开 HTTP(S) 来源；不将外部字符串当作可执行链接。 */
export function sourceLink(value: string | null | undefined): string | null {
  if (!value || /[\s\u0000-\u001f\\]/u.test(value)) return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

/** 保持日期原精度；布尔与赛程保留类型，空文本代表未知。最终以服务端校验为准。 */
export function editValue(field: Exclude<EditField, "schedule">, input: string): unknown {
  if (field === "is_online") return input === "" ? null : input === "true";
  if (field === "title" && !input.trim()) throw new Error("标题不能为空");
  return input.trim() ? input : null;
}

/** 赛程编辑保留现有附加字段；格式异常时拒绝静默丢弃。 */
export function scheduleRows(value: unknown): ScheduleNode[] {
  if (!Array.isArray(value)) throw new Error("赛程格式需要核对");
  return value.map((node: unknown) => {
    if (!node || typeof node !== "object" || Array.isArray(node)) throw new Error("赛程节点需要核对");
    const result: ScheduleNode = {};
    for (const [key, item] of Object.entries(node)) {
      if (typeof item !== "string" && item !== null) throw new Error("赛程字段需要核对");
      result[key] = item;
    }
    return result;
  });
}

/** 分页可能截断一次保存；必须加载齐该次字段后才提供整次撤回。 */
export function correctionGroups(items: Correction[]) {
  const groups = new Map<string, Correction[]>();
  for (const item of items) {
    const group = groups.get(item.operation_id) ?? [];
    if (!group.some((existing) => existing.id === item.id)) group.push(item);
    groups.set(item.operation_id, group);
  }
  return [...groups.entries()].map(([id, changes]) => ({
    id, changes,
    retractable: changes.length === changes[0].operation_field_count
      && changes.every((item) => item.can_retract),
  }));
}

export async function requestDetail<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    if (typeof body?.detail === "string") throw new Error(body.detail);
    if (response.status === 422) throw new Error("字段格式不符合要求，请检查日期、时区或赛程内容");
    throw new Error(`读取或保存失败（${response.status}），请重试或重新打开核对`);
  }
  if (body === null) throw new Error("返回内容无法读取，请重新打开核对");
  return body;
}
