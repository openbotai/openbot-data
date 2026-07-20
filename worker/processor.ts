import { Container } from "@cloudflare/containers";

interface Env {
  DATA_PROCESSOR_CONTAINER: DurableObjectNamespace<DataProcessorContainer>;
  OPENBOT_PROCESSOR_SECRET: string;
  OPENBOT_ANNOTATION_SECRET: string;
  OPENBOT_ANNOTATION_PROVIDER: string;
  OPENBOT_ANNOTATION_MODEL: string;
  OPENBOT_ANNOTATION_URL: string;
}

export class DataProcessorContainer extends Container {
  defaultPort = 8080;
  requiredPorts = [8080];
  sleepAfter = "15m";
  enableInternet = true;
  pingEndpoint = "/health";
}

function jsonError(status: number, message: string): Response {
  return Response.json({ error: message }, { status });
}

function instanceName(jobId: string): string {
  let hash = 0;
  for (const char of jobId) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return `pool-${hash % 3}`;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const isHealth = request.method === "GET" && url.pathname === "/health";
    const isProcess = request.method === "POST" && url.pathname === "/v1/process/subtasks";
    if (!isHealth && !isProcess) return jsonError(404, "not found");
    if (isProcess && request.headers.get("authorization") !== `Bearer ${env.OPENBOT_PROCESSOR_SECRET}`) {
      return jsonError(401, "invalid processor credentials");
    }

    let jobId = "health";
    if (isProcess) {
      try {
        const clone = request.clone();
        const body = await clone.json() as { job_id?: unknown };
        if (typeof body.job_id !== "string" || !body.job_id) {
          return jsonError(422, "job_id is required");
        }
        jobId = body.job_id;
      } catch {
        return jsonError(400, "request body must be valid JSON");
      }
    }

    const container = env.DATA_PROCESSOR_CONTAINER.getByName(instanceName(jobId));
    try {
      await container.startAndWaitForPorts({
        ports: [8080],
        startOptions: {
          envVars: {
            OPENBOT_PROCESSOR_SECRET: env.OPENBOT_PROCESSOR_SECRET,
            OPENBOT_ANNOTATION_SECRET: env.OPENBOT_ANNOTATION_SECRET,
            OPENBOT_ANNOTATION_PROVIDER: env.OPENBOT_ANNOTATION_PROVIDER,
            OPENBOT_ANNOTATION_MODEL: env.OPENBOT_ANNOTATION_MODEL,
            OPENBOT_ANNOTATION_URL: env.OPENBOT_ANNOTATION_URL,
          },
        },
        cancellationOptions: { portReadyTimeoutMS: 30_000 },
      });
      const target = new URL(request.url);
      target.protocol = "http:";
      target.hostname = "container";
      target.port = "8080";
      return await container.fetch(new Request(target, request));
    } catch (error) {
      console.error("Data processor container request failed", error);
      return jsonError(503, "data processor is temporarily unavailable");
    }
  },
} satisfies ExportedHandler<Env>;
