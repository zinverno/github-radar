import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Minimal async-data hook: reload on every ``key`` change, expose
 * ``data/loading/error/reload``. Errors are normalized to ``Error`` so the UI
 * never has to look at a raw fetch failure. The key is stringified so that
 * freshly-built array/object keys do not re-trigger the effect on every render.
 */
export function useAsync<T>(
  key: unknown,
  loader: () => Promise<T>,
): { data: T | null; loading: boolean; error: Error | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);
  const loaderRef = useRef(loader);
  const keyJson = JSON.stringify(key);

  useEffect(() => {
    loaderRef.current = loader;
  }, [loader]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    loaderRef.current()
      .then((value) => {
        if (!cancelled) setData(value);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err : new Error(String(err)));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [keyJson, tick]);

  const reload = useCallback(() => setTick((value) => value + 1), []);
  return { data, loading, error, reload };
}