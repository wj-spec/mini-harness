/** 审批预览里的层级行由后端追加在末尾；界面把它提到标题，避免窄屏被折叠。 */
export type ApprovalPreviewSplit = {body: string; tier: string | null};

export function splitApprovalPreview(preview: string): ApprovalPreviewSplit {
  const lines = preview.split('\n');
  let end = lines.length;
  while (end > 0 && (lines[end - 1] ?? '').trim() === '') end -= 1;
  const last = lines[end - 1] ?? '';
  const match = last.match(/^\[审批层\]\s*(.*)$/);
  if (!match) return {body: preview, tier: null};
  const body = lines.slice(0, end - 1).join('\n').trimEnd();
  return {body: body || preview, tier: (match[1] ?? '').trim() || null};
}

export function approvalTitle(tool: string, tier: string | null): string {
  return tier ? `审批 · ${tier} · ${tool}` : `审批 · ${tool}`;
}
