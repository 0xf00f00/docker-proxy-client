import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { fetchAuthStatus, setOnUnauthorized } from "@/api/client";
import LoginModal from "@/components/auth/LoginModal";
import { AUTH_STATUS_KEY, AuthContext } from "@/hooks/useAuth";

/**
 * Non-blocking auth provider
 */
export default function AuthGate({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);

  const auth = useQuery({
    queryKey: AUTH_STATUS_KEY,
    queryFn: fetchAuthStatus,
    staleTime: Infinity,
    retry: false,
  });
  const enabled = auth.data?.enabled ?? false;

  const showLogin = useCallback(() => {
    // No-op if the backend doesn't require auth — saves the user from a modal
    // that wouldn't do anything useful (the login endpoint returns 400).
    if (!enabled) return;
    setOpen(true);
  }, [enabled]);

  useEffect(() => {
    setOnUnauthorized(() => showLogin());
    return () => setOnUnauthorized(() => {});
  }, [showLogin]);

  return (
    <AuthContext.Provider value={{ showLogin }}>
      {children}
      <LoginModal
        open={open}
        onClose={() => setOpen(false)}
        onSuccess={() => {
          setOpen(false);
          // No queryKey → every cached query refetches. The /auth/status
          // query is covered; read-only queries that 401'd refetch with
          // the new cookie and silently recover.
          qc.invalidateQueries();
          toast.success("Signed in");
        }}
      />
    </AuthContext.Provider>
  );
}
