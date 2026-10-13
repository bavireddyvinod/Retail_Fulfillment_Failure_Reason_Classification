"use client";

import { useParams } from "next/navigation";
import ObservabilityDashboard from "../../components/ObservabilityDashboard";

export default function Page() {
  const params = useParams<{ traceId: string }>();
  return <ObservabilityDashboard view="trace-detail" traceId={params.traceId} />;
}