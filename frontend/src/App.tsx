import { useEffect, useState } from "react";
import { getHealth, HealthResponse } from "./api/client";

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : String(err))
      );
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem" }}>
      <h1>Product Truth Engine</h1>
      <p>Repository foundation - no product intelligence features yet.</p>
      {error && <p style={{ color: "red" }}>Backend unreachable: {error}</p>}
      {health && (
        <p>
          Backend online: {health.app} v{health.version} ({health.status})
        </p>
      )}
    </main>
  );
}
