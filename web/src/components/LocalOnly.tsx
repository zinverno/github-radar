import { useContext, type ReactNode } from "react";
import { DeploymentContext } from "../lib/deployment";
import { STATIC_PREVIEW } from "../lib/preview";

export function LocalOnly({ children }: { children: ReactNode }) {
  const mode = useContext(DeploymentContext);
  if (STATIC_PREVIEW) return <section><h1>Unavailable in preview</h1><p>AI, authentication and private workspace are unavailable in this static demo. No live backend is connected.</p></section>;
  if (!mode) return <p>Local tools are unavailable until deployment capabilities are confirmed.</p>;
  if (mode !== "local") return <p>This installation provides read-only public intelligence. Local admin tools are unavailable.</p>;
  return <>{children}</>;
}
