const MAX_ATTEMPTS = 3;

const isRetryable = (status) => status === 429 || status >= 500;

async function dispatchOnce(env) {
  const res = await fetch(
    `https://api.github.com/repos/${env.REPO}/actions/workflows/${env.WORKFLOW}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "internship-alert-trigger",
      },
      body: JSON.stringify({ ref: env.REF }),
    },
  );
  return { status: res.status, body: res.status === 204 ? "" : await res.text() };
}

async function dispatch(env) {
  let last = "no attempt made";
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      const { status, body } = await dispatchOnce(env);
      if (status === 204) return attempt;
      last = `${status} ${body}`;
      // Bad token / repo / workflow name won't fix itself on retry.
      if (!isRetryable(status)) break;
    } catch (err) {
      last = `network error: ${err.message}`;
    }
    if (attempt < MAX_ATTEMPTS) await new Promise((r) => setTimeout(r, attempt * 1000));
  }
  throw new Error(`workflow dispatch failed: ${last}`);
}

export default {
  async scheduled(controller, env) {
    const attempts = await dispatch(env);
    console.log(
      JSON.stringify({
        event: "dispatched",
        attempts,
        scheduledFor: new Date(controller.scheduledTime).toISOString(),
      }),
    );
  },

  async fetch() {
    return new Response("Not found", { status: 404 });
  },
};
