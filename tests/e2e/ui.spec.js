const fs = require("fs");
const path = require("path");
const { test, expect } = require("@playwright/test");

const repoRoot = path.resolve(__dirname, "..", "..");
const agentflowOwnedSkillsRoot = process.env.AGENTFLOW_OWNED_SKILLS_ROOT || path.join(repoRoot, ".agents", "skills");
const uiPipelinePath = path.join(__dirname, "fixtures", "ui-pipeline.yaml");
const cancelPipelinePath = path.join(__dirname, "fixtures", "cancel-pipeline.yaml");
const skillOwnedPipelinePath = path.join(__dirname, "fixtures", "skill-owned-pipeline.yaml");
const skillDefaultIgnorePipelinePath = path.join(__dirname, "fixtures", "skill-default-ignore-pipeline.yaml");
const skillTrustedTargetPipelinePath = path.join(__dirname, "fixtures", "skill-trusted-target-pipeline.yaml");
const skillPolicyWorkspaceRoot = path.join(__dirname, "fixtures", "skill-policy-workspace");
const workspaceGitMarkerPath = path.join(skillPolicyWorkspaceRoot, ".git");

function writeOwnedSkillPackage(packageName, workflowId, content) {
  const packageDir = path.join(agentflowOwnedSkillsRoot, packageName);
  const workflowDir = path.join(packageDir, "skills", workflowId);
  fs.mkdirSync(workflowDir, { recursive: true });
  fs.writeFileSync(path.join(packageDir, "SKILL.md"), `# ${packageName} wrapper`, "utf8");
  fs.writeFileSync(path.join(workflowDir, "SKILL.md"), content, "utf8");
}

function removeOwnedSkillPackage(packageName) {
  fs.rmSync(path.join(agentflowOwnedSkillsRoot, packageName), { recursive: true, force: true });
  if (fs.existsSync(agentflowOwnedSkillsRoot) && fs.readdirSync(agentflowOwnedSkillsRoot).length === 0) {
    fs.rmSync(agentflowOwnedSkillsRoot, { recursive: true, force: true });
  }
}

function ensureSkillPolicyWorkspaceRepoBoundary() {
  fs.writeFileSync(workspaceGitMarkerPath, "e2e fixture repo boundary\n", "utf8");
}

function clearSkillPolicyWorkspaceRepoBoundary() {
  fs.rmSync(workspaceGitMarkerPath, { force: true });
}

async function validatePipeline(request, pipelinePath) {
  const response = await request.post("/api/runs/validate", { data: { pipeline_path: pipelinePath } });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function createRun(request, pipelinePath) {
  const response = await request.post("/api/runs", { data: { pipeline_path: pipelinePath } });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function rerun(request, runId) {
  const response = await request.post(`/api/runs/${runId}/rerun`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function cancelRun(request, runId) {
  const response = await request.post(`/api/runs/${runId}/cancel`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function getRun(request, runId) {
  const response = await request.get(`/api/runs/${runId}`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function waitForTerminalRun(request, runId, expectedStatus) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const run = await getRun(request, runId);
    if (run.status === expectedStatus) {
      return run;
    }
    if (["completed", "failed", "cancelled"].includes(run.status) && run.status !== expectedStatus) {
      throw new Error(`run ${runId} finished with unexpected status ${run.status}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`timed out waiting for run ${runId} to reach ${expectedStatus}`);
}

async function artifactText(request, runId, nodeId, name) {
  const response = await request.get(`/api/runs/${runId}/artifacts/${nodeId}/${name}`);
  expect(response.ok()).toBeTruthy();
  return response.text();
}

test("validates, runs, retries, and reruns a DAG through the API", async ({ request }) => {
  const validation = await validatePipeline(request, uiPipelinePath);
  expect(validation.ok).toBeTruthy();
  expect(validation.pipeline.nodes).toHaveLength(4);

  const run = await createRun(request, uiPipelinePath);
  const completed = await waitForTerminalRun(request, run.id, "completed");

  expect(completed.nodes.plan.attempts).toHaveLength(2);
  const launch = await artifactText(request, completed.id, "plan", "launch.json");
  expect(launch).toContain('"command"');
  expect(launch).toContain("tests/e2e/bin/codex");

  const stdout = await artifactText(request, completed.id, "plan", "stdout.log");
  expect(stdout).toContain("plan success");

  const rerunResponse = await rerun(request, completed.id);
  expect(rerunResponse.id).not.toBe(completed.id);
  await waitForTerminalRun(request, rerunResponse.id, "completed");
});

test("cancels a running DAG through the API", async ({ request }) => {
  const run = await createRun(request, cancelPipelinePath);

  for (let attempt = 0; attempt < 20; attempt += 1) {
    const latest = await getRun(request, run.id);
    if (latest.status === "running") {
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }

  await cancelRun(request, run.id);
  const cancelled = await waitForTerminalRun(request, run.id, "cancelled");
  expect(cancelled.nodes.slow.status).toBe("cancelled");
});

test("uses AgentFlow-owned skill roots and ignores target repo skill packages by default", async ({ request }) => {
  writeOwnedSkillPackage("e2e-owned-analysis", "plan", "# E2E Owned Plan");
  writeOwnedSkillPackage("e2e-shared-analysis", "plan", "# E2E Owned Shared Plan");
  ensureSkillPolicyWorkspaceRepoBoundary();

  try {
    const ownedRun = await createRun(request, skillOwnedPipelinePath);
    const ownedCompleted = await waitForTerminalRun(request, ownedRun.id, "completed");
    const ownedStdout = await artifactText(request, ownedCompleted.id, "plan", "stdout.log");
    expect(ownedStdout).toContain("# E2E Owned Plan");

    const ignoredRun = await createRun(request, skillDefaultIgnorePipelinePath);
    const ignoredCompleted = await waitForTerminalRun(request, ignoredRun.id, "completed");
    const ignoredStdout = await artifactText(request, ignoredCompleted.id, "plan", "stdout.log");
    expect(ignoredStdout).toContain("# E2E Owned Shared Plan");
    expect(ignoredStdout).not.toContain("# E2E Target Shared Plan");
  } finally {
    removeOwnedSkillPackage("e2e-owned-analysis");
    removeOwnedSkillPackage("e2e-shared-analysis");
    clearSkillPolicyWorkspaceRepoBoundary();
  }
});

test("uses target repo skill packages only when explicit trust is enabled", async ({ request }) => {
  ensureSkillPolicyWorkspaceRepoBoundary();
  try {
    const run = await createRun(request, skillTrustedTargetPipelinePath);
    const completed = await waitForTerminalRun(request, run.id, "completed");
    const stdout = await artifactText(request, completed.id, "review", "stdout.log");

    expect(stdout).toContain("# E2E Trusted Target Review");
  } finally {
    clearSkillPolicyWorkspaceRepoBoundary();
  }
});
