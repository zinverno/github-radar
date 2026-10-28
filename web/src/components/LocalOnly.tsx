import { useContext, type ReactNode } from "react";
import { DeploymentContext } from "../lib/deployment";

export function LocalOnly({ children }: { children: ReactNode }) {
  const mode = useContext(DeploymentContext);
  if (!mode) return <p>Local tools are unavailable until deployment capabilities are confirmed.</p>;
  if (mode !== "local") return <p>This installation provides read-only public intelligence. Local admin tools are unavailable.</p>;
  return <>{children}</>;
}
