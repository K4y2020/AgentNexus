// Worker that fronts the AgentNexus container and proxies all HTTP (and
// WebSocket) traffic to it. AgentNexus needs a SINGLE server instance (in-memory
// runner registry), so every request routes to one fixed container instance.
import { Container, getContainer } from "@cloudflare/containers";

// Durable Object class/binding identities preserve existing deployments.
export class OmnigentServer extends Container {
  // Port the agentnexus server listens on inside the container.
  defaultPort = 8000;
  // Keep the container warm so D1-backed sessions don't cold-start constantly.
  sleepAfter = "30m";

  constructor(ctx, env) {
    super(ctx, env);
    // Env passed into the container. Secrets (DATABASE_URL, the cookie secret,
    // the AWS_* R2 keys) come from `wrangler secret put`; the rest are plain
    // vars in wrangler.jsonc.
    this.envVars = {
      DATABASE_URL: env.DATABASE_URL,
      AGENTNEXUS_ACCOUNTS_COOKIE_SECRET: env.AGENTNEXUS_ACCOUNTS_COOKIE_SECRET,
      AGENTNEXUS_AUTH_ENABLED: "1",
      AGENTNEXUS_AUTH_PROVIDER: "accounts",
      AGENTNEXUS_ACCOUNTS_AUTO_OPEN: "0",
      HOST: "0.0.0.0",
      PORT: "8000",
      // Artifact store -> R2 over the S3 API (agentnexus's native S3 backend).
      // AGENTNEXUS_ARTIFACT_URI selects it; AWS_* point boto3 at R2.
      AGENTNEXUS_ARTIFACT_URI: env.AGENTNEXUS_ARTIFACT_URI,
      AWS_ENDPOINT_URL_S3: env.AWS_ENDPOINT_URL_S3,
      AWS_DEFAULT_REGION: "auto",
      AWS_ACCESS_KEY_ID: env.AWS_ACCESS_KEY_ID,
      AWS_SECRET_ACCESS_KEY: env.AWS_SECRET_ACCESS_KEY,
    };
  }
}

export default {
  async fetch(request, env) {
    // One shared instance for the whole app (single-replica requirement).
    return await getContainer(env.OMNIGENT, "singleton").fetch(request);
  },
};
