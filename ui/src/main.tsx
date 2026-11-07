import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./styles.css";

const root = document.getElementById("root");
if (root === null) throw new Error("Application root is unavailable");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
