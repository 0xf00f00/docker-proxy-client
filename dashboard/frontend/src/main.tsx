import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import App from "./App";
import AuthGate from "./components/auth/AuthGate";
import "./app.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1 },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthGate>
        <App />
      </AuthGate>
      <Toaster
        theme="dark"
        position="bottom-center"
        richColors
        offset="1rem"
        mobileOffset="1rem"
      />
    </QueryClientProvider>
  </StrictMode>,
);
