import { createContext, useContext } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchAuthStatus } from "@/api/client";
import type { AuthStatus } from "@/types";

export const AUTH_STATUS_KEY = ["auth-status"] as const;

interface AuthControls {
  /** Pop the login modal. No-op when auth is disabled on the backend. */
  showLogin: () => void;
}

export const AuthContext = createContext<AuthControls>({ showLogin: () => {} });

/**
 * Auth status + controls. Components reach for this when they need to
 * render conditionally on login state (e.g. show a sign-in button) or
 * proactively prompt the user to log in.
 */
export function useAuth() {
  const query = useQuery<AuthStatus>({
    queryKey: AUTH_STATUS_KEY,
    queryFn: fetchAuthStatus,
    staleTime: Infinity,
    retry: false,
  });
  const { showLogin } = useContext(AuthContext);
  return {
    enabled: query.data?.enabled ?? false,
    authenticated: query.data?.authenticated ?? true,
    isLoading: query.isPending,
    showLogin,
  };
}
