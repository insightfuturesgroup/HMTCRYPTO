import { lazy, StrictMode, Suspense } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { AppErrorBoundary, installChunkRecovery } from "./AppErrorBoundary";
import PlatformApp from "./PlatformApp";
import { queryClient } from "./queryClient";
import "./styles.css";

const OperatorApp = lazy(() => import("./App"));

installChunkRecovery();

const root = document.getElementById("root");
if (root === null) throw new Error("Application root is unavailable");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppErrorBoundary>
          <Suspense fallback={<div className="app-boot-skeleton" aria-label="Loading application" />}>
            {window.location.pathname.startsWith("/operator") ? <OperatorApp /> : <PlatformApp />}
          </Suspense>
        </AppErrorBoundary>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
