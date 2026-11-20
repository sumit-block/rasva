import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import "./index.css";
import { AppShell } from "@/components/layout/AppShell";
import { JobsProvider } from "@/hooks/useJobs";
import { PrefsProvider } from "@/hooks/usePrefs";
import { HomePage } from "@/pages/Home";
import { LibraryPage } from "@/pages/Library";
import { JobsPage } from "@/pages/Jobs";
import { ModelsPage } from "@/pages/Models";
import { SettingsPage } from "@/pages/Settings";
import { AudioStudioPage } from "@/pages/AudioStudio";
import { ExperimentsPage, ImageStudioPage, NotFoundPage } from "@/pages/Placeholders";

document.documentElement.dataset.theme ??= "dark";

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "audio", element: <AudioStudioPage /> },
      { path: "image", element: <ImageStudioPage /> },
      { path: "library", element: <LibraryPage /> },
      { path: "jobs", element: <JobsPage /> },
      { path: "models", element: <ModelsPage /> },
      { path: "experiments", element: <ExperimentsPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <PrefsProvider>
      <JobsProvider>
        <RouterProvider router={router} />
      </JobsProvider>
    </PrefsProvider>
  </StrictMode>,
);
