import {useCallback, useEffect, useRef, useState} from 'react';
import {readFileDiffAsync, readWorkspaceStatusAsync, type ChangedFile, type DiffResult, type StatusResult} from './git.js';
import {applyWorkspace, focusAgentFile, initialSidebarState, selectedFile, type SidebarState} from './state.js';
import type {FileSource} from '../inspection.js';
import {checkFileSource} from './file-source.js';

export type SidebarReaders = {
  readStatus?: (workspace: string) => StatusResult | Promise<StatusResult>;
  readDiff?: (workspace: string, file: ChangedFile, hasHead: boolean) => DiffResult | Promise<DiffResult>;
};
type Snapshot = {path: string | null; file: ChangedFile | null; text: string; error: string | null; revision?: string; truncated?: boolean};
type Reading = {shown: Snapshot; pending: Snapshot | null};
const emptySnapshot = (): Snapshot => ({path: null, file: null, text: '', error: null});
const initial = (sourceKey = '') => ({sourceKey, state: initialSidebarState(), reading: {shown: emptySnapshot(), pending: null} as Reading});

function receive(reading: Reading, next: Snapshot, replace: boolean): Reading {
  if (replace || reading.shown.path !== next.path) return {shown: next, pending: null};
  const same = next.revision === reading.shown.revision && next.text === reading.shown.text && next.error === reading.shown.error;
  return {...reading, pending: same ? null : next};
}

/** 单次在途查询；阅读快照与最新工作区分别更新。 */
export function useSidebar(workspace: string, refreshMs: number, readers: SidebarReaders, fileSource?: FileSource | null) {
  const sourceKey = JSON.stringify([workspace, fileSource === undefined ? 'workspace-default' : fileSource && [fileSource.root, fileSource.identity]]);
  const [model, setModel] = useState(() => initial(sourceKey));
  const [activityPending, setActivityPending] = useState(false);
  const current = useRef(model);
  const readerRef = useRef(readers);
  readerRef.current = readers;
  const latestFiles = useRef<readonly string[]>([]);
  const selectionVersion = useRef(0);
  const request = useRef<(accept?: boolean) => void>(() => {});
  const publish = useCallback((next: typeof model) => {current.current = next; setModel(next);}, []);

  const updateState = useCallback((fn: (state: SidebarState) => SidebarState) => {
    const previous = current.current;
    let state = fn(previous.state);
    if (state.follow) state = focusAgentFile(state, latestFiles.current);
    if (state.follow) setActivityPending(false);
    const changed = state.selected !== previous.state.selected;
    if (changed) selectionVersion.current += 1;
    publish({...previous, state, reading: changed ? {shown: emptySnapshot(), pending: null} : previous.reading});
    if (changed || state.follow !== previous.state.follow) request.current();
  }, [publish]);

  useEffect(() => {
    let alive = true;
    let busy = false;
    let queued = false;
    let acceptPath: string | null = null;
    const refresh = (accept = false) => {
      if (accept) acceptPath = current.current.state.selected;
      if (busy) {queued = true; return;}
      void run();
    };
    const run = async () => {
      busy = true;
      queued = false;
      const accept = acceptPath;
      acceptPath = null;
      let version = selectionVersion.current;
      try {
        const root = fileSource?.root ?? workspace;
        const problem = fileSource !== undefined ? await checkFileSource(fileSource) : null;
        const status: StatusResult = problem ? {ok: false, reason: 'error', message: problem}
          : await (readerRef.current.readStatus ?? readWorkspaceStatusAsync)(root);
        if (!alive) return;
        const previous = current.current;
        const state = status.ok
          ? focusAgentFile(applyWorkspace(previous.state, status.status), latestFiles.current)
          : {...previous.state, ...(problem ? {files: [], selected: null} : {}), error: status.message};
        if (state.selected !== previous.state.selected) selectionVersion.current += 1;
        publish({...previous, state, reading: state.selected === previous.state.selected ? previous.reading : {shown: emptySnapshot(), pending: null}});
        version = selectionVersion.current;
        const file = selectedFile(state);
        let next: Snapshot;
        if (!status.ok) next = {path: state.selected, file, text: '', error: status.message};
        else if (!file) next = {path: state.selected, file: null, text: '', error: state.selected ? '所选文件不再有差异（已清理或重命名）' : null};
        else {
          const diff = await (readerRef.current.readDiff ?? readFileDiffAsync)(root, file, state.head !== null);
          const changed = fileSource !== undefined ? await checkFileSource(fileSource) : null;
          if (changed) throw new Error(changed);
          next = diff.ok ? {path: file.path, file, text: diff.text, error: null, revision: diff.revision, truncated: diff.truncated}
            : {path: file.path, file, text: '', error: diff.message};
        }
        if (!alive) return;
        if (version !== selectionVersion.current) {queued = true; return;}
        const latest = current.current;
        publish({...latest, reading: receive(latest.reading, next, latest.state.follow || accept === next.path)});
      } catch (error) {
        if (version !== selectionVersion.current) {queued = true; return;}
        if (alive) {
          const latest = current.current;
          const message = `刷新失败：${error instanceof Error ? error.message : String(error)}`;
          publish({...latest, state: {...latest.state, error: message}, reading: receive(latest.reading,
            {path: latest.state.selected, file: selectedFile(latest.state), text: '', error: message}, latest.state.follow)});
        }
      } finally {
        busy = false;
        if (alive && queued) refresh();
      }
    };
    publish(initial(sourceKey));
    setActivityPending(false);
    request.current = refresh;
    refresh();
    const timer = refreshMs > 0 ? setInterval(refresh, refreshMs) : null;
    return () => {alive = false; if (timer) clearInterval(timer); request.current = () => {};};
  }, [sourceKey, refreshMs, publish]);

  const refresh = useCallback((accept = false) => {
    if (accept && current.current.reading.pending) {
      const previous = current.current;
      publish({...previous, reading: {shown: previous.reading.pending!, pending: null}});
    }
    request.current(accept);
  }, [publish]);
  const followFiles = useCallback((files: readonly string[]) => {
    if (!current.current.state.follow && files.length && files.join('\0') !== latestFiles.current.join('\0')) setActivityPending(true);
    latestFiles.current = files;
    updateState(state => focusAgentFile(state, files));
    request.current();
  }, [updateState]);
  return {...(model.sourceKey === sourceKey ? model : initial(sourceKey)), activityPending, updateState, refresh, followFiles};
}
