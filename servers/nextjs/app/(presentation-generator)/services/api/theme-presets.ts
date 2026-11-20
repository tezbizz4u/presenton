import { getHeader } from "@/app/(presentation-generator)/services/api/header";
import {
  ApiResponseHandler,
  apiFetch,
} from "@/app/(presentation-generator)/services/api/api-error-handler";
import { getApiUrl } from "@/utils/api";

export interface ThemePresetSummary {
  id: string;
  name: string;
  family: string;
  mode: string;
  trend: string;
  font: string;
  swatches: string[];
}

export interface ThemePresetList {
  total: number;
  items: ThemePresetSummary[];
}

export class ThemePresetApi {
  static async list(params: {
    q?: string;
    mode?: string;
    trend?: string;
    offset?: number;
    limit?: number;
  }): Promise<ThemePresetList> {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== "") query.set(key, String(value));
    });
    const response = await apiFetch(
      getApiUrl(`/api/v1/ppt/theme-presets?${query.toString()}`),
      { method: "GET" },
    );
    return await ApiResponseHandler.handleResponse(response, "Failed to load themes");
  }

  /** Apply a preset to a deck. presetId=null restores the template's own theme. */
  static async apply(
    presentationId: string,
    presetId: string | null,
    templateId?: string | null,
  ): Promise<{ theme: Record<string, any> | null }> {
    const response = await apiFetch(
      getApiUrl(`/api/v1/ppt/presentation/${presentationId}/theme-preset`),
      {
        method: "PUT",
        headers: getHeader(),
        body: JSON.stringify({ preset_id: presetId, template_id: templateId ?? null }),
      },
    );
    return await ApiResponseHandler.handleResponse(response, "Failed to apply theme");
  }
}
