import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import config from "../../playwright.config.ts";

function matches(patterns, file) {
	return [patterns].flat().some((pattern) => pattern?.test(file));
}

test("shared workspace browser fixtures cannot execute concurrently", () => {
	assert.equal(config.workers, 1);
	assert.equal(config.fullyParallel, false);
	assert.equal(config.retries, 0);
	for (const project of config.projects) {
		assert.equal(project.workers ?? config.workers, 1, project.name);
		assert.equal(
			project.fullyParallel ?? config.fullyParallel,
			false,
			project.name,
		);
		assert.equal(project.retries ?? config.retries, 0, project.name);
	}
});

test("each browser spec remains in exactly one project with its original auth", () => {
	const root = fileURLToPath(new URL("../", import.meta.url));
	for (const path of readdirSync(root, { recursive: true })) {
		if (!path.endsWith(".spec.ts")) continue;
		const file = path.replaceAll("\\", "/");
		const selected = config.projects.filter(
			(project) =>
				matches(project.testMatch, file) &&
				!matches(project.testIgnore, file),
		);
		assert.equal(selected.length, 1, file);
		const expected =
			file.endsWith(".unauth.spec.ts") || file.endsWith(".docs.spec.ts")
				? undefined
				: file.endsWith(".user.spec.ts")
					? "e2e/.auth/org1_user.json"
					: "e2e/.auth/platform_admin.json";
		assert.equal(selected[0].use.storageState, expected, file);
	}
});

test("selected runs add only existing auth and MCP setup dependencies", () => {
	for (const project of config.projects) {
		assert(
			(project.dependencies ?? []).every((name) =>
				["setup", "mcp-settings"].includes(name),
			),
			project.name,
		);
	}
});
