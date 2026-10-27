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

/** Auth status and controls, for components that render on login state or prompt a login. */
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
