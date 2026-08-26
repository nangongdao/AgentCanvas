/** Async cleanup hooks that must finish before credentials are revoked. */

type SessionCleanup = () => Promise<void>;

const cleanups = new Set<SessionCleanup>();

export function registerSessionCleanup(cleanup: SessionCleanup): () => void {
  cleanups.add(cleanup);
  return () => cleanups.delete(cleanup);
}

export async function runSessionCleanups(): Promise<void> {
  await Promise.allSettled([...cleanups].map((cleanup) => cleanup()));
}
