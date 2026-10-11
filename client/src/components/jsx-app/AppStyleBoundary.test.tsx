import { createContext, useContext } from "react";
import { render, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AppStyleBoundary } from "./AppStyleBoundary";

const TestContext = createContext("missing");

function ContextReader() {
	return <span data-context-value>{useContext(TestContext)}</span>;
}

describe("AppStyleBoundary", () => {
	it("keeps app CSS out of the platform document while preserving React context", async () => {
		const view = render(
			<TestContext.Provider value="platform-context">
				<AppStyleBoundary stylesheetHref="data:text/css,span%7Bcolor%3Ared%7D">
					<ContextReader />
				</AppStyleBoundary>
			</TestContext.Provider>,
		);
		const host = view.container.querySelector<HTMLElement>(
			"[data-bifrost-app-style-boundary]",
		);
		expect(host).not.toBeNull();

		await waitFor(() => expect(host?.shadowRoot).not.toBeNull());
		const stylesheet = host?.shadowRoot?.querySelector<HTMLLinkElement>(
			"link[data-bifrost-app-stylesheet]",
		);
		expect(stylesheet?.getAttribute("href")).toBe(
			"data:text/css,span%7Bcolor%3Ared%7D",
		);
		expect(document.head.contains(stylesheet ?? null)).toBe(false);
		expect(
			host?.shadowRoot?.querySelector("[data-context-value]"),
		).toHaveTextContent("platform-context");
		expect(host?.style.getPropertyValue("contain")).toBe("strict");
		expect(host?.style.getPropertyPriority("contain")).toBe("important");
		expect(host?.style.getPropertyValue("position")).toBe("relative");
		expect(host?.style.getPropertyPriority("position")).toBe("important");
	});
});
