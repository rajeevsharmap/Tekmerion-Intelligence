import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App.jsx";
import { InvestigatorProvider } from "./context/investigator.jsx";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <BrowserRouter>
      <InvestigatorProvider>
        <App />
      </InvestigatorProvider>
    </BrowserRouter>
  </StrictMode>
);