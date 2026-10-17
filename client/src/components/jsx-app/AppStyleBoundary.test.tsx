import { createContext, useContext } from "react";
import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
	adaptDocumentStylesheetForShadowRoot,
	AppStyleBoundary,
} from "./AppStyleBoundary";

const TestContext = createContext("missing");

function ContextReader() {
	return <span data-context-value>{useContext(TestContext)}</span>;
}

afterEach(() => {
	document.documentElement.classList.remove("dark");
	document
		.querySelectorAll("style[data-test-document-stylesheet]")
		.forEach((style) => style.remove());
});

describe("AppStyleBoundary", () => {
	it("retargets standalone document selectors without touching lookalikes", () => {
		const style = document.createElement("style");
		style.dataset.testDocumentStylesheet = "true";
		style.textContent = `
			:root { --app-color: red; }
			html.dark, :root[data-theme="dim"]:not(.paused) { --app-color: blue; }
			body > main { padding: 1rem; }
			.body, [data-label="body"] { color: green; }
			@media (min-width: 1px) { :is(:root, body) { margin: 0; } }
		`;
		document.head.appendChild(style);
		if (!style.sheet) throw new Error("Test stylesheet did not parse");

		adaptDocumentStylesheetForShadowRoot(style.sheet);

		const rules = Array.from(style.sheet.cssRules);
		expect((rules[0] as CSSStyleRule).selectorText).toBe(":host");
		expect((rules[1] as CSSStyleRule).selectorText).toBe(
			':host(.dark), :host([data-theme="dim"]:not(.paused))',
		);
		expect((rules[2] as CSSStyleRule).selectorText).toBe(
			'[data-bifrost-app-mount="true"] > main',
		);
		expect((rules[3] as CSSStyleRule).selectorText).toBe(
			'.body, [data-label="body"]',
		);
		const mediaRule = rules[4] as CSSMediaRule;
		expect((mediaRule.cssRules[0] as CSSStyleRule).selectorText).toBe(
			':is(:host, [data-bifrost-app-mount="true"])',
		);
	});

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
