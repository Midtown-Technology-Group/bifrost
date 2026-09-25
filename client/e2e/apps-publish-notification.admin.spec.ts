import { test, expect } from "./fixtures/api-fixture";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const APP_SLUG = `e2e-publish-notification-${UNIQUE}`;
const APP_NAME = `E2E Publish Notification ${UNIQUE}`;

test.describe("Application publish notifications", () => {
	let appId = "";

	test.beforeAll(async ({ api }) => {
		const response = await api.post("/api/applications", {
			data: {
				name: APP_NAME,
				slug: APP_SLUG,
				app_model: "inline_v1",
			},
		});
		expect(response.ok(), await response.text()).toBe(true);
		appId = (await response.json()).id;
	});

	test.afterAll(async ({ api }) => {
		if (appId) await api.delete(`/api/applications/${appId}`);
	});

	test("queues in the dialog and reports completion through notifications", async ({
		page,
		api,
	}) => {
		await page.goto(`/apps/${APP_SLUG}/edit`);
		await page.getByRole("button", { name: "Publish" }).click();
		const dialog = page.getByRole("dialog", { name: "Publish Application" });
		await dialog.getByLabel("Publish Message (optional)").fill("WebSocket release");
		const published = page.waitForResponse(
			(response) =>
				response.request().method() === "POST" &&
				response.url().endsWith(`/api/applications/${appId}/publish`),
		);
		await dialog.getByRole("button", { name: "Publish" }).click();
		const accepted = (await (await published).json()) as { job_id: string };

		await expect(dialog).toBeHidden();
		await expect(page.getByText("Application publish queued")).toBeVisible();

		await page.getByRole("button", { name: "Notifications" }).click();
		const notification = page.getByRole("article", {
			name: `Publishing ${APP_NAME}`,
			exact: true,
		});
		await expect(notification).toBeVisible();
		// If terminal delivery stalls, retain the durable job state before CI tears down.
		const diagnostic = setTimeout(() => {
			void api
				.get(`/api/platform-jobs/${accepted.job_id}`)
				.then(async (response) => {
					const job = (await response.json()) as {
						status: string;
						phase: string;
						progress_percent: number | null;
					};
					console.info("publish job at 20s", {
						job_id: accepted.job_id,
						status: job.status,
						phase: job.phase,
						progress_percent: job.progress_percent,
					});
				})
				.catch((error) => console.info("publish job diagnostic failed", error));
		}, 20_000);
		try {
			await expect(
				notification.getByText("Completed", { exact: true }),
			).toBeVisible({ timeout: 30_000 });
		} finally {
			clearTimeout(diagnostic);
		}
	});
});
