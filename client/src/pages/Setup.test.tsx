import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { Setup } from "./Setup";
import { registerUser } from "@/services/auth";
import { setupWithPasskey } from "@/services/passkeys";

vi.mock("@/services/auth", () => ({
	registerUser: vi.fn(async () => undefined),
}));
vi.mock("@/services/passkeys", () => ({
	setupWithPasskey: vi.fn(async () => ({ access_token: "test-token" })),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({
		needsSetup: true,
		isLoading: false,
		checkAuthStatus: vi.fn(),
		completeLoginWithToken: vi.fn(),
	}),
}));
vi.mock("@/lib/applicationName", () => ({
	useApplicationName: () => "Example Portal",
}));
vi.mock("@/components/branding/Logo", () => ({
	Logo: ({ alt }: { alt: string }) => <img alt={alt} />,
}));

describe("Setup account details", () => {
	it("offers default-off everywhere Secrets Reader consent", () => {
		renderWithProviders(<Setup />, { initialEntries: ["/setup"] });
		expect(
			screen.getByRole("checkbox", { name: "Grant me Secrets Reader" }),
		).not.toBeChecked();
		expect(
			screen.getByText(
				/Global, the provider organization, and all managed organizations/,
			),
		).toBeInTheDocument();
	});
	it("validates email, advances with Enter and preserves editable account details", async () => {
		const { user } = renderWithProviders(<Setup />, {
			initialEntries: ["/setup"],
		});
		const email = screen.getByLabelText("Email", { exact: true });
		await user.type(
			screen.getByLabelText("Name", { exact: true }),
			"Design Review",
		);
		await user.type(email, "invalid");
		await user.click(screen.getByRole("button", { name: "Continue" }));
		expect(
			screen.queryByRole("button", { name: "Use password instead" }),
		).not.toBeInTheDocument();
		await user.clear(email);
		await user.type(email, "review@example.invalid{Enter}");
		expect(screen.getByRole("heading", { level: 1 })).toHaveFocus();
		expect(
			screen.getByRole("button", { name: "Use password instead" }),
		).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Change account details" }),
		);
		expect(screen.getByLabelText("Name", { exact: true })).toHaveValue(
			"Design Review",
		);
		expect(screen.getByLabelText("Email", { exact: true })).toHaveValue(
			"review@example.invalid",
		);
	});
});

describe("Setup consent submission", () => {
	beforeEach(() => vi.clearAllMocks());
	it.each([false, true])(
		"forwards consent %s for either credential method",
		async (consent) => {
			const { user } = renderWithProviders(<Setup />, {
				initialEntries: ["/setup"],
			});
			await user.type(
				screen.getByLabelText("Email", { exact: true }),
				"first@example.com",
			);
			if (consent)
				await user.click(
					screen.getByRole("checkbox", {
						name: "Grant me Secrets Reader",
					}),
				);
			await user.click(screen.getByRole("button", { name: "Continue" }));
			await user.click(
				screen.getByRole("button", { name: "Set up passkey" }),
			);
			expect(setupWithPasskey).toHaveBeenCalledWith(
				"first@example.com",
				"",
				undefined,
				consent,
			);
		},
	);
	it("forwards explicit password consent", async () => {
		const { user } = renderWithProviders(<Setup />, {
			initialEntries: ["/setup"],
		});
		await user.type(
			screen.getByLabelText("Email", { exact: true }),
			"first@example.com",
		);
		await user.click(
			screen.getByRole("checkbox", { name: "Grant me Secrets Reader" }),
		);
		await user.click(screen.getByRole("button", { name: "Continue" }));
		await user.click(
			screen.getByRole("button", { name: "Use password instead" }),
		);
		await user.type(
			screen.getByLabelText("Password", { exact: true }),
			"test-password",
		);
		await user.type(
			screen.getByLabelText("Confirm password", { exact: true }),
			"test-password",
		);
		await user.click(
			screen.getByRole("button", { name: "Create account" }),
		);
		expect(registerUser).toHaveBeenCalledWith(
			"first@example.com",
			"test-password",
			"",
			true,
		);
	});
});
