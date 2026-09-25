import { randomUUID } from "node:crypto";
import { test, expect, type AuthedApi } from "./fixtures/api-fixture";

const suffix = randomUUID().slice(0, 8);
const agentName = `Navigation Agent ${suffix}`;
const agentDescription = `Middle-click agent fixture ${suffix}`;
const appName = `Navigation App ${suffix}`;
const appSlug = `navigation-app-${suffix}`;
const appDescription = `Middle-click app fixture ${suffix}`;
const workflowName = `navigation_workflow_${suffix}`;
const workflowPath = `${workflowName}.py`;
const formName = `Navigation Form ${suffix}`;
const formDescription = `Middle-click form fixture ${suffix}`;

async function expectOk(
	response: Awaited<ReturnType<AuthedApi["get"]>>,
	label: string,
) {
	expect(response.ok(), `${label}: ${await response.text()}`).toBe(true);
}

async function expectDeleted(
	response: Awaited<ReturnType<AuthedApi["delete"]>>,
) {
	expect([200, 204, 404]).toContain(response.status());
}

test("resource links and table rows navigate without leaking menu actions", async ({
	page,
	api,
}) => {
	let agentId: string | undefined;
	let appId: string | undefined;
	let formId: string | undefined;

	try {
		const agentResponse = await api.post("/api/agents", {
			data: {
				name: agentName,
				description: agentDescription,
				system_prompt: "You are a navigation regression fixture.",
				access_level: "authenticated",
				channels: ["chat"],
			},
		});
		await expectOk(agentResponse, "create agent");
		agentId = ((await agentResponse.json()) as { id: string }).id;

		const appResponse = await api.post("/api/applications", {
			data: {
				name: appName,
				slug: appSlug,
				description: appDescription,
				access_level: "authenticated",
				role_ids: [],
				app_model: "inline_v1",
			},
		});
		await expectOk(appResponse, "create app");
		appId = ((await appResponse.json()) as { id: string }).id;

		const writeWorkflow = await api.put("/api/files/editor/content", {
			data: {
				path: workflowPath,
				content: `from bifrost import workflow\n\n@workflow(name="${workflowName}")\nasync def ${workflowName}(summary: str = "") -> dict:\n    return {"summary": summary}\n`,
				encoding: "utf-8",
			},
		});
		await expectOk(writeWorkflow, "write workflow");
		const registerWorkflow = await api.post("/api/workflows/register", {
			data: { path: workflowPath, function_name: workflowName },
		});
		await expectOk(registerWorkflow, "register workflow");
		const workflow = (await registerWorkflow.json()) as { id: string };

		const formResponse = await api.post("/api/forms", {
			data: {
				name: formName,
				description: formDescription,
				workflow_id: workflow.id,
				form_schema: {
					fields: [
						{
							name: "summary",
							label: "Summary",
							type: "text",
							required: false,
						},
					],
				},
				access_level: "authenticated",
			},
		});
		await expectOk(formResponse, "create form");
		formId = ((await formResponse.json()) as { id: string }).id;

		await page.goto("/agents");
		await page
			.getByRole("textbox", { name: "Search agents", exact: true })
			.fill(agentName);
		const agentCard = page
			.getByRole("article")
			.filter({ has: page.getByRole("link", { name: agentName }) });
		await expect(agentCard).toBeVisible();
		await expect(
			agentCard.getByRole("link", { name: agentName }),
		).toHaveAttribute("href", `/agents/${agentId}`);
		await agentCard
			.getByRole("button", { name: `${agentName} actions` })
			.click();
		await expect(
			page.getByRole("menuitem", { name: "Edit Agent" }),
		).toBeVisible();
		await expect(page).toHaveURL(/\/agents$/);
		await page.keyboard.press("Escape");

		await page
			.getByRole("button", { name: "Table view", exact: true })
			.click();
		const agentRow = page
			.getByRole("row")
			.filter({ has: page.getByRole("link", { name: agentName }) });
		await expect(agentRow).toBeVisible();
		await agentRow
			.getByRole("button", { name: `${agentName} actions` })
			.click();
		await expect(
			page.getByRole("menuitem", { name: "Edit Agent" }),
		).toBeVisible();
		await expect(page).toHaveURL(/\/agents$/);
		await page.keyboard.press("Escape");

		await page.goto("/apps");
		await page.getByLabel("Search apps").fill(appName);
		const appCard = page
			.getByRole("article")
			.filter({ has: page.getByRole("link", { name: appName }) });
		await expect(appCard).toBeVisible();
		await expect(
			appCard.getByRole("link", { name: appName }),
		).toHaveAttribute("href", `/apps/${appSlug}/preview`);
		await page.getByRole("radio", { name: "Table view" }).click();
		const appRow = page
			.getByRole("row")
			.filter({ has: page.getByRole("link", { name: appName }) });
		await expect(appRow).toBeVisible();
		await expect(
			appRow.getByRole("link", { name: appName }),
		).toHaveAttribute("href", `/apps/${appSlug}/preview`);

		await page.goto("/forms");
		await page.getByRole("radio", { name: "Table view" }).click();
		await page
			.getByPlaceholder(
				"Search forms by name, description, or workflow...",
			)
			.fill(formName);
		const formRow = page
			.getByRole("row")
			.filter({ has: page.getByRole("link", { name: formName }) });
		await expect(formRow).toBeVisible();
		await formRow
			.getByRole("button", { name: `${formName} actions` })
			.click();
		await expect(
			page.getByRole("menuitem", { name: "Edit Form" }),
		).toBeVisible();
		await expect(page).toHaveURL(/\/forms$/);
		await page.keyboard.press("Escape");
		await formRow.click();
		await expect(page).toHaveURL(new RegExp(`/execute/${formId}$`));
		await page.goto("/forms");
		await page.getByRole("radio", { name: "Table view" }).click();
		await page
			.getByPlaceholder(
				"Search forms by name, description, or workflow...",
			)
			.fill(formName);
		await page
			.getByRole("row")
			.filter({ hasText: formName })
			.getByRole("button", { name: `${formName} actions` })
			.click();
		await page.getByRole("menuitem", { name: "Edit Form" }).click();
		await expect(page).toHaveURL(new RegExp(`/forms/${formId}/edit$`));

		await page.goto("/workflows");
		await page
			.getByPlaceholder("Search by name, description, or category...")
			.fill(workflowName);
		const workflowLink = page.getByRole("link", {
			name: workflowName,
			exact: true,
		});
		await expect(workflowLink).toHaveAttribute(
			"href",
			`/workflows/${workflowName}/execute`,
		);
		await workflowLink.click();
		await expect(page).toHaveURL(
			new RegExp(`/workflows/${workflowName}/execute$`),
		);
	} finally {
		if (formId)
			await expectDeleted(await api.delete(`/api/forms/${formId}`));
		if (agentId)
			await expectDeleted(await api.delete(`/api/agents/${agentId}`));
		if (appId)
			await expectDeleted(await api.delete(`/api/applications/${appId}`));
		await expectDeleted(
			await api.delete(
				`/api/files/editor?path=${encodeURIComponent(workflowPath)}`,
			),
		);
	}
});
