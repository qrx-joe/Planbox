import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  EDIT_FIELDS, correctionGroups, displayValue, editValue, fieldLabel, relationLabel,
  requestDetail, sourceLink, scheduleRows,
  type ActivityRecord, type CorrectionPage, type EditField, type EvidenceItem, type ScheduleNode,
  type DraftRecord,
} from "./activity-details";
import { timeReviewMessage } from "./time";

/** 按活动 ID 挂载独立核对状态；加载取消、错误和未知写入结果均不伪装成空数据。 */
export function ActivityDetails({ eventId, apiBase, onUpdated }: {
  eventId: string; apiBase: string; onUpdated: () => Promise<void>;
}) {
  return <RecordDetails recordId={eventId} kind="event" apiBase={apiBase} onUpdated={onUpdated} />;
}

export function DraftDetails({ extractionId, apiBase, onUpdated, onOpenChange, disabled, readOnly }: {
  extractionId: string; apiBase: string; onUpdated: () => Promise<void>;
  onOpenChange?: (open: boolean) => void; disabled?: boolean; readOnly?: boolean;
}) {
  return <RecordDetails recordId={extractionId} kind="draft" apiBase={apiBase}
    onUpdated={onUpdated} onOpenChange={onOpenChange} disabled={disabled} readOnly={readOnly} />;
}

function RecordDetails({ recordId, kind, apiBase, onUpdated, onOpenChange, disabled, readOnly }: {
  recordId: string; kind: "event" | "draft"; apiBase: string; onUpdated: () => Promise<void>;
  onOpenChange?: (open: boolean) => void; disabled?: boolean; readOnly?: boolean;
}) {
  const [opened, setOpened] = useState(false);
  return <div className="activity-details">
    <button type="button" className="link-button" aria-expanded={opened} disabled={disabled}
      onClick={() => { setOpened(!opened); onOpenChange?.(!opened); }}>
      {kind === "event" ? opened ? "收起核对" : "查看证据与修改"
        : readOnly ? opened ? "收起入库前记录" : "查看入库前草稿"
          : opened ? "收起编辑，返回确认" : "修改草稿与依据"}</button>
    {opened ? <DetailBody key={recordId} recordId={recordId} kind={kind} apiBase={apiBase}
      onUpdated={onUpdated} readOnly={readOnly} /> : null}
  </div>;
}

function DetailBody({ recordId, kind, apiBase, onUpdated, readOnly }: {
  recordId: string; kind: "event" | "draft"; apiBase: string;
  onUpdated: () => Promise<void>; readOnly?: boolean;
}) {
  const [event, setEvent] = useState<ActivityRecord | null>(null);
  const [evidence, setEvidence] = useState<EvidenceItem[] | null>(null);
  const [history, setHistory] = useState<CorrectionPage | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [reload, setReload] = useState(0);
  const [field, setField] = useState<EditField>("title");
  const [input, setInput] = useState("");
  const [schedule, setSchedule] = useState<ScheduleNode[]>([]);
  const [scheduleError, setScheduleError] = useState("");
  const [reason, setReason] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [retractReason, setRetractReason] = useState("");
  const [message, setMessage] = useState("");
  const mounted = useRef(true);
  const formId = `edit-${kind}-${recordId}`;
  const resourceUrl = `${apiBase}/api/${kind === "draft" ? "review-queue" : "events"}/${recordId}`;
  const evidenceUrl = kind === "draft"
    ? `${apiBase}/api/evidences?extraction_result_id=${encodeURIComponent(recordId)}`
    : `${apiBase}/api/evidences/${recordId}`;

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    const signal = controller.signal;
    setLoading(true);
    setErrors({});
    setEvent(null); setHistory(null); setEvidence(null);
    async function load() {
      const results = await Promise.allSettled([
        kind === "draft" ? requestDetail<DraftRecord>(resourceUrl, { signal }).then((draft) => ({
          ...draft.extracted, id: draft.id, status: draft.status,
        })) : requestDetail<ActivityRecord>(resourceUrl, { signal }),
        requestDetail<{ items: EvidenceItem[] }>(evidenceUrl, { signal }),
        requestDetail<CorrectionPage>(`${resourceUrl}/corrections?limit=20`, { signal }),
      ]);
      if (signal.aborted) return;
      const [eventResult, evidenceResult, historyResult] = results;
      const failures: Record<string, string> = {};
      if (eventResult.status === "fulfilled" && eventResult.value.id === recordId) {
        setEvent(eventResult.value);
      } else failures.event = "当前内容读取失败，请重新读取";
      if (evidenceResult.status === "fulfilled") setEvidence(evidenceResult.value.items);
      else failures.evidence = "证据读取失败，请重新读取";
      if (historyResult.status === "fulfilled") setHistory(historyResult.value);
      else failures.history = "更正历史读取失败，暂不能安全编辑";
      setErrors(failures); setLoading(false);
    }
    void load();
    return () => { mounted.current = false; controller.abort(); };
  }, [resourceUrl, evidenceUrl, recordId, kind, reload]);

  useEffect(() => {
    const value = event?.[field];
    setInput(field === "schedule" ? ""
      : value == null ? "" : String(value));
    if (field === "schedule") {
      try { setSchedule(scheduleRows(value ?? [])); setScheduleError(""); }
      catch { setScheduleError("原赛程格式需要核对，暂不能通过此页面编辑"); }
    }
  }, [event, field]);

  const sources = [...new Map((evidence ?? []).map((item) => [item.source_item_id, item])).values()];
  const groups = correctionGroups(history?.items ?? []);
  const editable = !readOnly && (kind === "event" || event?.status === "pending_review");
  const sourceDraftIds = [...new Set((evidence ?? []).map((item) => item.extraction_result_id)
    .filter((id): id is string => typeof id === "string"))];

  async function mutate(url: string, method: string, body: unknown) {
    setSaving(true); setMessage("");
    try {
      await requestDetail<unknown>(url, {
        method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!mounted.current) return;
      setMessage("已保存，请查看当前值和更正历史");
      setReason(""); setRetractReason("");
      setReload((value) => value + 1);
      try { await onUpdated(); }
      catch { if (mounted.current) setMessage("已保存，列表刷新失败；请重新读取核对"); }
    } catch (error) {
      if (mounted.current) setMessage(error instanceof TypeError
        ? "网络中断，保存结果未知。请先重新读取并检查历史，再决定是否重试。"
        : error instanceof Error ? error.message : "操作失败，请重新读取核对");
    } finally { if (mounted.current) setSaving(false); }
  }

  async function save(eventObject: FormEvent<HTMLFormElement>) {
    eventObject.preventDefault();
      if (!event || !history || saving || !editable) return;
    try {
      const value = field === "schedule" ? schedule : editValue(field, input);
      await mutate(resourceUrl, "PATCH", kind === "draft" ? {
        changes: { [field]: value }, expected_values: { [field]: event[field] ?? null },
        expected_revision: history.revision, reason: reason || null,
      } : {
        [field]: value, expected_values: { [field]: event[field] ?? null },
        expected_revision: history.revision, correction_reason: reason || null,
        source_item_id: sourceId || null,
      });
    } catch (error) { setMessage(error instanceof Error ? error.message : "输入格式不正确"); }
  }

  async function moreHistory() {
    if (!history || saving) return;
    setSaving(true);
    try {
      const page = await requestDetail<CorrectionPage>(
        `${resourceUrl}/corrections?limit=20&offset=${history.items.length}`,
      );
      if (!mounted.current) return;
      if (page.revision !== history.revision) {
        setMessage("更正历史已变化，请重新读取后查看");
      } else setHistory({ ...page, items: [...history.items, ...page.items] });
    } catch { if (mounted.current) setMessage("更多历史读取失败，已有内容仍保留"); }
    finally { if (mounted.current) setSaving(false); }
  }

  return <section className="detail-body" aria-label={kind === "draft" ? "草稿核对" : "活动核对"}>
    <div className="inline-actions">
      <button type="button" className="secondary" disabled={saving || loading}
        onClick={() => {
          setReload((value) => value + 1);
          void onUpdated().catch(() => {
            if (mounted.current) setMessage("列表读取失败，当前内容请分别核对");
          });
        }}>重新读取</button>
    </div>
    {loading ? <p role="status">正在读取活动、证据与历史…</p> : null}
    {Object.values(errors).map((error) => <p className="missing" role="alert" key={error}>{error}</p>)}
    {message ? <p role="status">{message}</p> : null}
    {kind === "draft" && editable ? <p className="detail-note">
      先保存修改，再收起编辑进行确认；未保存的输入不会入库。
    </p> : null}
    {kind === "draft" && event && !editable ? <p className="detail-note">
      这里保留入库前草稿与更正历史，当前只读；正式活动请在活动信息中更正。
    </p> : null}
    {kind === "draft" && event?.time_issues?.length ? <p className="missing" role="status">
      时间待核对：{timeReviewMessage(event.time_issues)}。可以先保存候选，解决问题后才能确认入库。
    </p> : null}
    {evidence ? <>
      <h4>字段依据</h4>
      <p className="detail-note">“可定位”仅表示能在原文找到片段，内容含义仍需你核对。</p>
      {evidence.length ? <ul className="evidence-list">{evidence.map((item) => {
        const link = sourceLink(item.source_url);
        return <li key={item.id}>
          <strong>{fieldLabel(item.field_name)}：{item.field_value ?? "未知"}</strong>
          <p>{item.verification_status === "located" ? "原文可定位" : "未找到可定位依据"}
            {" · "}{relationLabel(item.value_relation)}</p>
          {item.verification_status === "located" && item.evidence_text ?
            <blockquote>{item.evidence_text}</blockquote> : null}
          {item.verification_status === "located" && item.start_offset != null && item.end_offset != null ?
            <p className="detail-note">正文字符 {item.start_offset + 1}–{item.end_offset}</p> : null}
          {link ? <a href={link} target="_blank" rel="noreferrer">打开来源：{item.source_title || "原文"}</a> :
            <span className="detail-note">来源：{item.source_title || "已保存的素材"}</span>}
        </li>;
      })}</ul> : <p>此活动尚无字段证据；不代表内容已经核实。</p>}
    </> : null}
    {event && history && editable ? <form className="correction-form" onSubmit={(e) => void save(e)}>
      <h4>{kind === "draft" ? "修改草稿信息" : "修改活动信息"}</h4>
      <label htmlFor={`${formId}-field`}>选择字段</label>
      <select id={`${formId}-field`} value={field} disabled={saving || loading}
        onChange={(e) => {
          const selected = EDIT_FIELDS.find(([key]) => key === e.target.value);
          if (selected) setField(selected[0]);
        }}>{EDIT_FIELDS.filter(([key]) => kind === "event" || key !== "notes")
          .map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
      <p>当前值：{field === "schedule" ? `共 ${Array.isArray(event.schedule) ? event.schedule.length : "未知"} 个赛程节点`
        : displayValue(event[field])}</p>
      {field === "schedule" ? <p>更正后的赛程</p> :
        <label htmlFor={`${formId}-value`}>更正后的{fieldLabel(field)}</label>}
      {field === "schedule" ? <div id={`${formId}-value`}>
        {scheduleError ? <p role="alert">{scheduleError}</p> : <>
          {schedule.map((node, index) => <fieldset key={index} className="schedule-node">
            <legend>赛程 {index + 1}</legend>
            {[["name", "名称"], ["start_at", "开始"], ["end_at", "结束"]].map(([key, label]) =>
              <label key={key}>{label}<input value={node[key] ?? ""} disabled={saving}
                onChange={(e) => setSchedule((rows) => rows.map((row, position) => position === index
                  ? { ...row, [key]: e.target.value || null } : row))} /></label>)}
            <button type="button" className="secondary" disabled={saving}
              onClick={() => setSchedule((rows) => rows.filter((_, position) => position !== index))}>
              移除赛程 {index + 1}</button>
          </fieldset>)}
          <button type="button" className="secondary" disabled={saving}
            onClick={() => setSchedule((rows) => [...rows, { name: null, start_at: null, end_at: null }])}>
            添加赛程节点</button>
        </>}
      </div> : field === "is_online" ? <select id={`${formId}-value`} value={input} disabled={saving}
        onChange={(e) => setInput(e.target.value)}>
        <option value="">未知</option><option value="true">是</option><option value="false">否</option>
      </select> : <textarea id={`${formId}-value`} value={input} disabled={saving}
        required={field === "title"}
        onChange={(e) => setInput(e.target.value)} aria-describedby={`${formId}-help`} />}
      <p id={`${formId}-help`} className="detail-note">{field.endsWith("_at")
        ? "仅日期填 YYYY-MM-DD；精确时刻请带时区，例如 2027-01-05T09:00:00+08:00。留空表示未知。"
        : field === "schedule" ? "逐个填写赛程；日期或时间沿用上述格式。未知时留空，不自动补年份或时刻。"
          : "修改会保留旧值；留空表示未知。"}</p>
      <label htmlFor={`${formId}-reason`}>更正原因（选填）</label>
      <input id={`${formId}-reason`} value={reason} maxLength={1000} disabled={saving}
        onChange={(e) => setReason(e.target.value)} />
      {kind === "event" ? <><label htmlFor={`${formId}-source`}>关联来源（选填）</label>
      <select id={`${formId}-source`} value={sourceId} disabled={saving || !evidence}
        onChange={(e) => setSourceId(e.target.value)}>
        <option value="">未关联来源</option>
        {sources.map((source, index) => <option key={source.source_item_id} value={source.source_item_id}>
          {source.source_title || `已保存素材 ${index + 1}`}</option>)}
      </select>
      <p className="detail-note">关联来源表示你的更正依据，不自动证明来源支持新值。</p></> :
        <p className="detail-note">更正关联当前素材；原文未支持的新值仍标为未证实。</p>}
      <button type="submit" disabled={saving || loading || (field === "schedule" && !!scheduleError)}>
        {saving ? "处理中…" : "保存更正"}</button>
    </form> : null}
    {history ? <div className="correction-history">
      <h4>更正历史</h4>
      <p className="detail-note">人工更正记录供你回看，不代表主办方已确认。</p>
      {history.total === 0 ? <p>尚无人工更正记录。</p> : <>
        <label htmlFor={`${formId}-retract`}>撤回原因（撤回时必填）</label>
        <input id={`${formId}-retract`} value={retractReason} maxLength={1000} disabled={saving}
          onChange={(e) => setRetractReason(e.target.value)} />
        <p className="detail-note">撤回会恢复同次保存的全部字段，并保留反向历史；后续修改不会被强行覆盖。</p>
        <ul className="evidence-list">{groups.map((group) => <li key={group.id}>
          <p>{group.changes[0].action === "retraction" ? "撤回记录" : "人工更正"}
            {" · "}{group.changes[0].created_at}</p>
          {group.changes.map((change) => <p key={change.id}>
            <strong>{fieldLabel(change.field_name)}</strong>：{displayValue(change.old_value)} → {displayValue(change.new_value)}
          </p>)}
          <p>原因：{group.changes[0].reason || "未提供"}</p>
          {group.retractable && editable ? <button type="button" className="secondary"
            disabled={saving || loading || !retractReason.trim()}
            onClick={() => void mutate(`${resourceUrl}/corrections/${group.id}/retract`,
              "POST", { reason: retractReason })}>撤回这次保存</button> :
            <p className="detail-note">{group.changes.length < group.changes[0].operation_field_count
              ? "请先加载更多，补齐这次保存的全部字段。"
              : group.changes[0].action === "retraction" ? "撤回已保留原更正记录。"
                : "此记录已有后续修改或不可直接撤回，可重新更正。"}</p>}
        </li>)}</ul>
        {history.items.length < history.total ? <button type="button" className="secondary"
          disabled={saving || loading} onClick={() => void moreHistory()}>加载更多历史</button> : null}
      </>}
    </div> : null}
    {kind === "event" ? sourceDraftIds.map((id) => <DraftDetails key={id} extractionId={id}
      apiBase={apiBase} onUpdated={onUpdated} readOnly />) : null}
  </section>;
}
