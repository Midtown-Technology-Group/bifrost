import { createContext, useContext } from "react";
import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AppStyleBoundary } from "./AppStyleBoundary";

const TestContext = createContext("missing");

function ContextReader() {
	return <span data-context-value>{useContext(TestContext)}</span>;
}

afterEach(() => document.documentElement.classList.remove("dark"));

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
		const containment = view.container.querySelector<HTMLElement>(
			"[data-bifrost-app-containment-boundary]",
		);
		expect(host).not.toBeNull();
		expect(containment).not.toBeNull();

		await waitFor(() => expect(host?.shadowRoot).not.toBeNull());
		const stylesheet = host?.shadowRoot?.querySelector<HTMLLinkElement>(
			"link[data-bifrost-app-stylesheet]",
		);
		expect(stylesheet?.getAttribute("href")).toBe(
			"data:text/css,span%7Bcolor%3Ared%7D",
		);
		expect(document.head.contains(stylesheet ?? null)).toBe(false);
		expect(
			host?.shadowRoot?.querySelector(
				"style[data-bifrost-platform-stylesheet]",
			),
		).not.toBeNull();
		expect(
			host?.shadowRoot?.querySelector("[data-context-value]"),
		).toHaveTextContent("platform-context");
		expect(containment?.style.getPropertyValue("contain")).toBe("strict");
		expect(containment?.style.getPropertyValue("overflow")).toBe("hidden");
		expect(containment?.style.getPropertyValue("isolation")).toBe(
			"isolate",
		);
		expect(host?.style.getPropertyValue("contain")).toBe("strict");
		expect(host?.style.getPropertyPriority("contain")).toBe("important");
		expect(host?.style.getPropertyValue("position")).toBe("relative");
		expect(host?.style.getPropertyPriority("position")).toBe("important");

		const appRoot = host?.shadowRoot?.querySelector<HTMLElement>(
			"[data-bifrost-app-root]",
		);
		expect(appRoot).not.toHaveClass("dark");
		document.documentElement.classList.add("dark");
		await waitFor(() => expect(appRoot).toHaveClass("dark"));
	});
});
