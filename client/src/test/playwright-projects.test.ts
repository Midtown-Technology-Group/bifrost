// @vitest-environment node
import { describe, expect, it } from "vitest";
import config from "../../playwright.config";

describe("browser project isolation", () => {
	it("finishes global MCP settings mutations before parallel consumers", () => {
		const projects = config.projects ?? [];
		const settings = projects.find((project) => project.name === "mcp-settings");
		expect(settings?.dependencies).toEqual(["setup"]);
		expect(settings?.testMatch).toBeInstanceOf(RegExp);
		expect("mcp-settings-acceptance.admin.spec.ts").toMatch(settings!.testMatch as RegExp);
		for (const project of projects.filter((entry) => !["setup", "mcp-settings"].includes(entry.name!))) {
			expect(project.dependencies, project.name).toContain("mcp-settings");
		}
		const admin = projects.find((project) => project.name === "platform-admin");
		expect("mcp-settings-acceptance.admin.spec.ts").toMatch(admin!.testIgnore as RegExp);
		expect("memory.admin.spec.ts").not.toMatch(admin!.testIgnore as RegExp);
	});
});
