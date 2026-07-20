interface Env {
  AI: Ai;
  OPENBOT_ANNOTATION_SECRET: string;
  OPENBOT_ANNOTATION_MODEL: string;
}

interface AnnotationRequest {
  task_hint?: string | null;
  taxonomy: string[];
  video: { duration_seconds: number } & Record<string, unknown>;
  frames: Array<{ index: number; timestamp_sec: number }>;
  contact_sheets: Array<{ mime_type: string; base64_data: string }>;
  prompt_version: string;
  model?: string;
}

const segmentSchema = {
  type: "object",
  properties: {
    segments: {
      type: "array",
      items: {
        type: "object",
        properties: {
          start_sec: { type: "number", minimum: 0 },
          end_sec: { type: "number", minimum: 0 },
          action: { type: ["string", "null"] },
          object: { type: ["string", "null"] },
          source: { type: ["string", "null"] },
          target: { type: ["string", "null"] },
          state_change: { type: ["string", "null"] },
          outcome: {
            type: "string",
            enum: ["success", "failure", "intervention", "recovery", "uncertain"],
          },
          label: { type: "string" },
          evidence_frame_indices: {
            type: "array",
            items: { type: "integer", minimum: 0 },
          },
        },
        required: [
          "start_sec",
          "end_sec",
          "action",
          "object",
          "source",
          "target",
          "state_change",
          "outcome",
          "label",
          "evidence_frame_indices",
        ],
        additionalProperties: false,
      },
    },
  },
  required: ["segments"],
  additionalProperties: false,
} as const;

function jsonError(status: number, message: string): Response {
  return Response.json({ error: message }, { status });
}

function authorized(request: Request, secret: string): boolean {
  return request.headers.get("authorization") === `Bearer ${secret}`;
}

function validRequest(value: unknown): value is AnnotationRequest {
  if (!value || typeof value !== "object") return false;
  const body = value as Partial<AnnotationRequest>;
  return (
    Array.isArray(body.taxonomy) &&
    body.taxonomy.length > 0 &&
    !!body.video &&
    typeof body.video.duration_seconds === "number" &&
    Array.isArray(body.frames) &&
    body.frames.length > 0 &&
    Array.isArray(body.contact_sheets) &&
    body.contact_sheets.length > 0 &&
    typeof body.prompt_version === "string"
  );
}

function extractStructuredResponse(result: unknown): Record<string, unknown> | null {
  if (!result || typeof result !== "object") return null;
  const value = result as Record<string, unknown>;
  const choices = value.choices;
  let content: unknown = value.response;
  if (Array.isArray(choices)) {
    const first = choices[0] as Record<string, unknown> | undefined;
    const message = first?.message as Record<string, unknown> | undefined;
    content = message?.content;
  }
  if (typeof content === "string") {
    try {
      const parsed = JSON.parse(content);
      return parsed && typeof parsed === "object" ? parsed as Record<string, unknown> : null;
    } catch {
      return null;
    }
  }
  return content && typeof content === "object" ? content as Record<string, unknown> : null;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return Response.json({ status: "ok", service: "openbot-data-annotator" });
    }
    if (request.method !== "POST" || url.pathname !== "/v1/annotate/subtasks") {
      return jsonError(404, "not found");
    }
    if (!authorized(request, env.OPENBOT_ANNOTATION_SECRET)) {
      return jsonError(401, "invalid annotation credentials");
    }

    let body: unknown;
    try {
      body = await request.json();
    } catch {
      return jsonError(400, "request body must be valid JSON");
    }
    if (!validRequest(body)) return jsonError(422, "invalid annotation request");

    const prompt = [
      "You annotate robot and egocentric video for embodied-AI training data.",
      "Split the observed episode into non-overlapping chronological subtasks.",
      "Only state details visible in the contact sheets. Use null when a slot is not observable.",
      "Use uncertain when outcome cannot be verified. Never infer success from the task hint alone.",
      `Task hint: ${body.task_hint || "not supplied"}`,
      `Allowed taxonomy hints: ${JSON.stringify(body.taxonomy)}`,
      `Video duration seconds: ${body.video.duration_seconds}`,
      `Frame index: ${JSON.stringify(body.frames)}`,
      `Prompt version: ${body.prompt_version}`,
    ].join("\n");
    const content: Array<Record<string, unknown>> = [{ type: "text", text: prompt }];
    for (const sheet of body.contact_sheets.slice(0, 4)) {
      if (!sheet || typeof sheet.base64_data !== "string" || !sheet.base64_data) {
        return jsonError(422, "invalid contact sheet");
      }
      content.push({
        type: "image_url",
        image_url: { url: `data:${sheet.mime_type || "image/jpeg"};base64,${sheet.base64_data}` },
      });
    }

    const model = body.model || env.OPENBOT_ANNOTATION_MODEL;
    try {
      const result = await env.AI.run(model as keyof AiModels, {
        messages: [{ role: "user", content }],
        temperature: 0,
        max_completion_tokens: 4096,
        chat_template_kwargs: { thinking: false },
        response_format: {
          type: "json_schema",
          json_schema: {
            name: "openbot_subtask_timeline",
            strict: true,
            schema: segmentSchema,
          },
        },
      } as never) as unknown;
      const parsed = extractStructuredResponse(result);
      if (!parsed || !Array.isArray(parsed.segments)) {
        return jsonError(502, "Workers AI returned an invalid structured response");
      }
      const raw = result as Record<string, unknown>;
      return Response.json({
        segments: parsed.segments,
        provider: "cloudflare-workers-ai",
        model_version: model,
        provider_run_id: typeof raw.id === "string" ? raw.id : null,
        usage: raw.usage && typeof raw.usage === "object" ? raw.usage : null,
      });
    } catch (error) {
      console.error("Workers AI annotation failed", error);
      return jsonError(502, "Workers AI annotation failed");
    }
  },
} satisfies ExportedHandler<Env>;
