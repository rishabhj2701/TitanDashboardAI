import { apiFetchApp } from './http';

export interface FeatureCapability {
  available: boolean;
  reason?: string;
  missing_endpoints?: string[];
}

export interface CapabilitiesResponse {
  version: string;
  generated_at: string;
  features: Record<string, FeatureCapability>;
  limits?: {
    area_analysis_timeout_ms?: number;
    workzone_analysis_timeout_ms?: number;
    area_analysis_fast_approx_area_km2?: number;
  };
}

export const getCapabilities = async (): Promise<CapabilitiesResponse> => {
  const response = await apiFetchApp('/api/capabilities', { skipSessionHeader: true });
  return response.json();
};

export const isCapabilityAvailable = (
  capabilities: CapabilitiesResponse | null,
  feature: string
): boolean => Boolean(capabilities?.features?.[feature]?.available);

export const getCapabilityReason = (
  capabilities: CapabilitiesResponse | null,
  feature: string
): string | null => capabilities?.features?.[feature]?.reason ?? null;
