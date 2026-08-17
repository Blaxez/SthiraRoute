import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
// Bundled locally rather than pulled from a CDN — venue wifi is not a dependency.
import "maplibre-gl/dist/maplibre-gl.css";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
