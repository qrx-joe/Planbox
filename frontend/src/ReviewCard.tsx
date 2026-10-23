import { useState } from "react";
import { Check, X } from "lucide-react";
import { DraftDetails } from "./ActivityDetails";
import { fieldLabel } from "./activity-details";
import { timeReviewMessage, type TimeIssue } from "./time";

export type ReviewQueueItem = {
  extraction: {
    id: string; source_item_id: string; revision: number;
    extracted: {
      title: string; event_type?: string | null; registration_deadline_at?: string | null;
      event_start_at?: string | null; location?: string | null; time_issues?: TimeIssue[];
    };
    missing_fields: string[]; confidence?: number | null;
  };
  source_url?: string | null; source_text?: string | null; source_type: string;
};

/** 单个草稿拥有独立编辑/动作状态；打开编辑期间先保存收起，再确认保存的版本。 */
export function ReviewCard({ item, apiBase, onSaved, onConfirm, onReject }: {
  item: ReviewQueueItem; apiBase: string; onSaved: () => Promise<void>;
  onConfirm: (id: string, revision: number) => Promise<void>;
  onReject: (id: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [acting, setActing] = useState(false);
  const { extraction } = item;
  const draft = extraction.extracted;
  async function act(operation: () => Promise<void>) {
    setActing(true);
    try { await operation(); } finally { setActing(false); }
  }
  return <article className="review-card">
    <div className="review-source">
      <p className="eyebrow">{item.source_type}</p>
      <p>{item.source_text || item.source_url || "当前来源暂无可读正文"}</p>
    </div>
    <div className="review-fields">
      <h2>{draft.title}</h2>
      <dl>
        <dt>类型</dt><dd>{draft.event_type || "待补充"}</dd>
        <dt>报名截止</dt><dd>{draft.registration_deadline_at || "待补充"}</dd>
        <dt>举办时间</dt><dd>{draft.event_start_at || "待补充"}</dd>
        <dt>地点</dt><dd>{draft.location || "待补充"}</dd>
      </dl>
      {extraction.missing_fields.length ? <p className="missing">
        待补充或核对：{extraction.missing_fields.map(fieldLabel).join("、")}
      </p> : null}
      {draft.time_issues?.length ? <p className="missing" role="status">
        时间待核对：{timeReviewMessage(draft.time_issues)}
      </p> : null}
      <DraftDetails extractionId={extraction.id} apiBase={apiBase} onUpdated={onSaved}
        onOpenChange={setEditing} disabled={acting} />
      {editing ? <p className="detail-note">完成保存并收起编辑后，可确认入库或放弃。</p> : null}
      <div className="composer-actions">
        <button type="button" disabled={editing || acting || !!draft.time_issues?.length}
          onClick={() => void act(() => onConfirm(extraction.id, extraction.revision))}>
          <Check size={17} />确认入库</button>
        <button type="button" className="secondary" disabled={editing || acting}
          onClick={() => void act(() => onReject(extraction.id))}><X size={17} />放弃</button>
      </div>
    </div>
  </article>;
}
