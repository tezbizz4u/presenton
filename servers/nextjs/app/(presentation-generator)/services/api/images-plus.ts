import { ApiResponseHandler, apiFetch } from "./api-error-handler";
import { getApiUrl } from "@/utils/api";

export type ImageSource = "pexels" | "pixabay" | "unsplash";

export interface ImageCredit {
  source: string;
  author?: string;
  author_url?: string;
  page_url?: string;
  license?: string;
  model?: string;
}

export interface PlusImage {
  id: string;
  source: string;
  thumb?: string;
  url: string;
  width?: number;
  height?: number;
  alt?: string;
  credit?: ImageCredit;
  download_location?: string | null;
}

export interface KeyStatus {
  configured: boolean;
  hint: string;
}
export type KeysResponse = Record<ImageSource, KeyStatus>;

export interface AiOptions {
  models: { key: "quality" | "fast"; id: string; label: string }[];
  styles: { key: string; label: string }[];
  aspects: string[];
}

export type Orientation = "any" | "landscape" | "portrait" | "square";

const base = "/api/v1/ppt/images-plus";

async function json(path: string, init: RequestInit | undefined, fallback: string) {
  const response = await apiFetch(getApiUrl(`${base}${path}`), init);
  return ApiResponseHandler.handleResponse(response, fallback);
}

export class ImagesPlusApi {
  static getKeys(): Promise<KeysResponse> {
    return json("/keys", { cache: "no-cache" }, "Could not load image source keys");
  }

  static saveKeys(keys: Partial<Record<ImageSource, string>>): Promise<KeysResponse> {
    return json(
      "/keys",
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(keys),
      },
      "Could not save the keys",
    );
  }

  static testKey(source: ImageSource, key?: string): Promise<{ ok: boolean; found: number }> {
    return json(
      "/keys/test",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source, key: key || undefined }),
      },
      "Key test failed",
    );
  }

  static searchStock(params: {
    query: string;
    sources: ImageSource[];
    orientation: Orientation;
    page?: number;
  }): Promise<{ results: PlusImage[]; errors: Record<string, string> }> {
    const q = new URLSearchParams({
      query: params.query,
      sources: params.sources.join(","),
      orientation: params.orientation,
      per_source: "8",
      page: String(params.page ?? 1),
    });
    return json(`/stock?${q.toString()}`, undefined, "Stock search failed");
  }

  static searchWeb(params: {
    query: string;
    orientation: Orientation;
    page?: number;
  }): Promise<{ results: PlusImage[]; errors: Record<string, string> }> {
    const q = new URLSearchParams({
      query: params.query,
      orientation: params.orientation,
      limit: "24",
      page: String(params.page ?? 1),
    });
    return json(`/web?${q.toString()}`, undefined, "Web search failed");
  }

  static importImage(image: PlusImage, prompt?: string): Promise<{ id: string; url: string; credit: ImageCredit }> {
    return json(
      "/import",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: image.url,
          source: image.source,
          credit: image.credit ?? null,
          download_location: image.download_location ?? null,
          prompt: prompt ?? null,
        }),
      },
      "Could not add this image",
    );
  }

  static aiOptions(): Promise<AiOptions> {
    return json("/ai-options", { cache: "no-cache" }, "Could not load AI options");
  }

  static generate(body: {
    prompt: string;
    style: string;
    aspect: string;
    model: "quality" | "fast";
    count: number;
    no_text: boolean;
  }): Promise<{ results: PlusImage[]; failed: number }> {
    return json(
      "/generate",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
      "Image generation failed",
    );
  }
}
