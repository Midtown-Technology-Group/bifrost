import { test, expect } from "./fixtures/api-fixture";

test("an admin saves, reloads and clears device and agent grants", async ({
	page,
	api,
}) => {
	const created = await api.post("/api/roles", {
		data: {
			name: `Device and agent grants ${crypto.randomUUID().slice(0, 8)}`,
			description: "Admin grant management acceptance",
		},
	});
	expect(created.status()).toBe(201);
	const { id: roleId } = (await created.json()) as { id: string };
	const grantPath = `/api/roles/${roleId}/bridge-grants`;
	try {
		await page.goto(`/roles/${roleId}/permissions`);
		const panel = page.getByRole("region", {
			name: "Device and agent grants",
		});
		await expect(panel).toBeVisible();
		const names = [
			"Manage devices",
			"Execute devices",
			"Promote own agents",
		];
		for (const name of names) {
			await panel.getByRole("checkbox", { name, exact: true }).check();
		}
		const saved = page.waitForResponse(
			(response) =>
				new URL(response.url()).pathname === grantPath &&
				response.request().method() === "PUT",
		);
		await panel
			.getByRole("button", { name: "Save device and agent grants" })
			.click();
		expect((await saved).status()).toBe(200);
		await page.reload();
		for (const name of names) {
			await expect(
				panel.getByRole("checkbox", { name, exact: true }),
			).toBeChecked();
			await panel.getByRole("checkbox", { name, exact: true }).uncheck();
		}
		const cleared = page.waitForResponse(
			(response) =>
				new URL(response.url()).pathname === grantPath &&
				response.request().method() === "PUT",
		);
		await panel
			.getByRole("button", { name: "Save device and agent grants" })
			.click();
		expect((await cleared).status()).toBe(200);
		await page.reload();
		for (const name of names) {
			await expect(
				panel.getByRole("checkbox", { name, exact: true }),
			).not.toBeChecked();
		}
	} finally {
		const removed = await api.delete(`/api/roles/${roleId}`);
		expect(removed.status()).toBe(204);
	}
});
