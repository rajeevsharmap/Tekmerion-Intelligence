import { useCallback, useMemo, useState } from "react";
import { InvestigatorContext } from "./investigatorContextStore.js";

/*
 * FRONTEND-ONLY (MOCK/DEV) AUTHENTICATION
 * ==========================================
 * The backend does not currently expose a login/authentication endpoint.
 * This context is a development convenience, not a security boundary —
 * it lets the investigator pick one of the backend's real, known test
 * identities (services/investigators.js) and remembers the choice for
 * the session. It must be replaced with real backend authentication
 * when one exists.
 *
 * Every action that actually changes case state (human-review, action
 * submission) still sends only `investigator_id` to the backend, which
 * independently resolves and authorizes the role — this context's
 * `role` field is a UX label for rendering the sidebar/action buttons,
 * never something trusted for authorization.
 */

const STORAGE_KEY = "tekmerion_investigator";

function readStoredInvestigator() {
  const raw = sessionStorage.getItem(STORAGE_KEY) || localStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    sessionStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(STORAGE_KEY);
    return null;
  }
}

export function InvestigatorProvider({ children }) {
  const [investigator, setInvestigator] = useState(() => readStoredInvestigator());

  const login = useCallback((investigatorData, rememberDevice) => {
    sessionStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(STORAGE_KEY);
    const store = rememberDevice ? localStorage : sessionStorage;
    store.setItem(STORAGE_KEY, JSON.stringify(investigatorData));
    setInvestigator(investigatorData);
  }, []);

  const logout = useCallback(() => {
    sessionStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(STORAGE_KEY);
    setInvestigator(null);
  }, []);

  const value = useMemo(
    () => ({
      investigator,
      isAuthenticated: Boolean(investigator),
      role: investigator?.role === "senior" ? "senior" : "junior",
      login,
      logout,
    }),
    [investigator, login, logout]
  );

  return (
    <InvestigatorContext.Provider value={value}>{children}</InvestigatorContext.Provider>
  );
}