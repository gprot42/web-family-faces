import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.jsx";
import { applyLabelSize, applyLabelStyle, applyNametag, readLabelSize, readLabelStyle, readNametag } from "./nametag.js";
import { applyTheme, readTheme } from "./theme.js";
import "./styles.css";

applyTheme(readTheme());
applyNametag(readNametag());
applyLabelSize(readLabelSize());
applyLabelStyle(readLabelStyle());

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
