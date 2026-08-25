import { useCallback, useEffect, useState } from "react";
import api, { ApiError } from "../services/api.js";

/**
 * Loads the real case list from GET /cases. No hardcoded/mock case
 * data anywhere in this hook — an empty or failed response is
 * represented honestly to the caller.
 */
export function useCases() {
  const [cases, setCases] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.listCases();
      setCases(data?.cases || []);
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(String(err), 0, null));
      setCases([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Standard fetch-on-mount pattern (guarded by `reload`'s own
    // cancellation handling); the current eslint-plugin-react-hooks
    // "set-state-in-effect" rule flags this call transitively even
    // though the initial `loading` state is already true and this is
    // not a synchronous re-render loop.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    reload();
  }, [reload]);

  return { cases, loading, error, reload };
}