const path = require("path");
const { defineConfig } = require("@playwright/test");

const repoRoot = __dirname;
const mockBin = path.join(repoRoot, "tests", "e2e", "bin");
const runsDir = path.join(repoRoot, ".agentflow", "e2e-runs");
const ownedSkillsRoot = path.join(runsDir, "owned-skills");

process.env.AGENTFLOW_OWNED_SKILLS_ROOT = ownedSkillsRoot;

module.exports = defineConfig({
  testDir: path.join("tests", "e2e"),
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:8123",
    headless: true,
  },
  webServer: {
    command: `bash -lc 'rm -rf "${runsDir}" && mkdir -p "${runsDir}" && rm -f .e2e-flaky && . .venv/bin/activate && uvicorn agentflow.app:create_app --factory --host 127.0.0.1 --port 8123'`,
    url: "http://127.0.0.1:8123/api/health",
    cwd: repoRoot,
    reuseExistingServer: true,
    env: {
      ...process.env,
      AGENTFLOW_OWNED_SKILLS_ROOT: ownedSkillsRoot,
      PATH: `${mockBin}:${process.env.PATH || ""}`,
      AGENTFLOW_RUNS_DIR: runsDir,
      AGENTFLOW_KIMI_MOCK_RESPONSE: "kimi mock review",
      AGENTFLOW_MAX_CONCURRENT_RUNS: "2",
    },
  },
});
