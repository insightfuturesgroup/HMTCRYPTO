import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import PlatformApp from "./PlatformApp";
import "./styles.css";

const root = document.getElementById("root");
if (root === null) throw new Error("Application root is unavailable");

createRoot(root).render(
  <StrictMode>
    {window.location.pathname.startsWith("/operator") ? <App /> : <PlatformApp />}
  </StrictMode>,
);
