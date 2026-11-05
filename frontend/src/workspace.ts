/**
 * Hand-over to the editor (Workspace) from other pages: the start page's drop
 * zone and the review queue put a request here and switch to "Umwandeln"; the
 * Workspace, which stays mounted, picks it up.
 */
export type WorkspaceRequest = { kind: "files"; files: File[] } | { kind: "review"; id: number };

type Listener = (request: WorkspaceRequest) => void;
let listener: Listener | null = null;
let pending: WorkspaceRequest | null = null;

export function requestWorkspace(request: WorkspaceRequest) {
  if (listener) listener(request);
  else pending = request;
}

export function onWorkspaceRequest(l: Listener): () => void {
  listener = l;
  if (pending) {
    const p = pending;
    pending = null;
    l(p);
  }
  return () => {
    if (listener === l) listener = null;
  };
}
