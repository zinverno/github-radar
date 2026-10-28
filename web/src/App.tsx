import { Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { LocalOnly } from "./components/LocalOnly";
import { AiPage } from "./pages/AiPage";
import { BridgesPage } from "./pages/BridgesPage";
import { DeveloperDetailPage } from "./pages/DeveloperDetailPage";
import { DevelopersPage } from "./pages/DevelopersPage";
import { EcosystemPage } from "./pages/EcosystemPage";
import { ExplorePage } from "./pages/ExplorePage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { RepositoriesPage } from "./pages/RepositoriesPage";
import { RepositoryDetailPage } from "./pages/RepositoryDetailPage";
import { TopicDetailPage } from "./pages/TopicDetailPage";
import { TopicsPage } from "./pages/TopicsPage";
import { WorkspaceLayout } from "./pages/WorkspaceLayout";
import { WorkspacePage } from "./pages/WorkspacePage";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<ExplorePage />} />
        <Route path="repositories" element={<RepositoriesPage />} />
        <Route path="repositories/:owner/:name" element={<RepositoryDetailPage />} />
        <Route path="topics" element={<TopicsPage />} />
        <Route path="topics/:name" element={<TopicDetailPage />} />
        <Route path="developers" element={<DevelopersPage />} />
        <Route path="developers/:login" element={<DeveloperDetailPage />} />
        <Route path="ecosystem" element={<EcosystemPage />} />
        <Route path="bridges" element={<BridgesPage />} />
        <Route path="ai" element={<LocalOnly><AiPage /></LocalOnly>} />
        <Route path="workspace" element={<LocalOnly><WorkspaceLayout /></LocalOnly>}>
          <Route index element={<Navigate to="developers" replace />} />
          <Route path="developers" element={<WorkspacePage key="developers" kind="developers" />} />
          <Route path="repositories" element={<WorkspacePage key="repositories" kind="repositories" />} />
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
