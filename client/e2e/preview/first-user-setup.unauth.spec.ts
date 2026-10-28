import { test, expect } from "@playwright/test";

test("first administrator explicitly chooses everywhere Secrets Reader", async ({
	page,
	request,
}) => {
	expect((await (await request.get("/auth/status")).json()).needs_setup).toBe(
		true,
	);
	await page.goto("/setup");
	await page.getByLabel("Name", { exact: true }).fill("First Admin");
	await page
		.getByLabel("Email", { exact: true })
		.fill("first-run@example.com");
	const consent = page.getByRole("checkbox", {
		name: "Grant me Secrets Reader",
	});
	await expect(consent).not.toBeChecked();
	await expect(
		page.getByText(
			/Global, the provider organization, and all managed organizations/,
		),
	).toBeVisible();
	await consent.check();
	await page.getByRole("button", { name: "Continue", exact: true }).click();
	await page.getByRole("button", { name: "Use password instead" }).click();
	await page
		.getByLabel("Password", { exact: true })
		.fill("Test-only-password-123!");
	await page
		.getByLabel("Confirm password", { exact: true })
		.fill("Test-only-password-123!");
	const registered = page.waitForResponse(
		(r) =>
			r.url().endsWith("/auth/register") &&
			r.request().method() === "POST",
	);
	await page
		.getByRole("button", { name: "Create account", exact: true })
		.click();
	expect((await registered).status()).toBe(201);
	await expect(page).toHaveURL(/\/login$/);
	expect((await (await request.get("/auth/status")).json()).needs_setup).toBe(
		false,
	);
});
