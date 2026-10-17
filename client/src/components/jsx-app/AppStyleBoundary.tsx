import type { CSSProperties, ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import platformStyles from "@/index.css?inline";

const APP_CONTAINMENT_STYLE: CSSProperties = {
	position: "relative",
	width: "100%",
	height: "100%",
	minWidth: 0,
	minHeight: 0,
	overflow: "hidden",
	contain: "strict",
	isolation: "isolate",
	zIndex: 0,
};

const HOST_CONSTRAINTS: ReadonlyArray<readonly [string, string]> = [
	["display", "block"],
	["position", "relative"],
	["inset", "auto"],
	["z-index", "auto"],
	["width", "100%"],
	["height", "100%"],
	["min-width", "0"],
	["min-height", "0"],
	["margin", "0"],
	["padding", "0"],
	["border", "0"],
	["overflow", "auto"],
	["contain", "strict"],
	["isolation", "isolate"],
];

const APP_MOUNT_SELECTOR = '[data-bifrost-app-mount="true"]';
const DOCUMENT_ROOT_SELECTOR =
	/(^|[\s>+~,(])(:root|html|body)(?=$|[\s>+~.#:[,)])/g;

function isolatedDocumentSelector(selector: string): string {
	return selector.replace(
		DOCUMENT_ROOT_SELECTOR,
		(_match: string, prefix: string, root: string) =>
			`${prefix}${root === "body" ? APP_MOUNT_SELECTOR : ":host"}`,
	);
}

/**
 * Retarget standalone-document selectors after an existing bundle stylesheet
 * loads inside Shadow DOM. Mutating the linked sheet preserves its own URL as
 * the base for relative assets while keeping every rule inside the app root.
 */
export function adaptDocumentStylesheetForShadowRoot(
	stylesheet: CSSStyleSheet,
): void {
	const visit = (rules: CSSRuleList): void => {
		for (const rule of Array.from(rules)) {
			if (rule.type === CSSRule.STYLE_RULE) {
				const styleRule = rule as CSSStyleRule;
				const isolated = isolatedDocumentSelector(styleRule.selectorText);
				if (isolated !== styleRule.selectorText) {
					styleRule.selectorText = isolated;
				}
			}
			const nested = (rule as CSSRule & { cssRules?: CSSRuleList }).cssRules;
			if (nested) visit(nested);
		}
	};

	visit(stylesheet.cssRules);
}

/**
 * Return the app's Shadow DOM root and give its host a predictable baseline.
 *
 * App stylesheets are authored by app developers and must not participate in
 * the platform document's cascade. A stylesheet's important `:host` rules can
 * outrank these outer inline declarations, so security containment belongs on
 * AppContainmentBoundary, which the shadow stylesheet cannot select.
 */
export function ensureIsolatedAppShadow(host: HTMLElement): ShadowRoot {
	for (const [property, value] of HOST_CONSTRAINTS) {
		host.style.setProperty(property, value, "important");
	}
	const shadowRoot = host.shadowRoot ?? host.attachShadow({ mode: "open" });
	ensurePlatformStyles(shadowRoot);
	return shadowRoot;
}

/** Install the trusted platform component rules inside an isolated app root. */
export function ensurePlatformStyles(shadowRoot: ShadowRoot): void {
	if (shadowRoot.querySelector("style[data-bifrost-platform-stylesheet]")) {
		return;
	}
	const style = document.createElement("style");
	style.dataset.bifrostPlatformStylesheet = "true";
	style.textContent = platformStyles;
	shadowRoot.prepend(style);
}

/**
 * Keep even adversarial `:host` CSS inside the platform-owned content region.
 * This element stays outside the app shadow tree, so app CSS cannot select it.
 */
export function AppContainmentBoundary({ children }: { children: ReactNode }) {
	return (
		<div
			data-bifrost-app-containment-boundary="true"
			style={APP_CONTAINMENT_STYLE}
		>
			{children}
		</div>
	);
}

/**
 * Render an inline app through a portal so React context crosses the Shadow DOM
 * boundary while the app stylesheet and DOM remain cascade-isolated.
 */
export function AppStyleBoundary({
	stylesheetHref,
	children,
}: {
	stylesheetHref: string | null;
	children: ReactNode;
}) {
	const hostRef = useRef<HTMLDivElement>(null);
	const [shadowRoot, setShadowRoot] = useState<ShadowRoot | null>(null);
	const [isDark, setIsDark] = useState(() =>
		document.documentElement.classList.contains("dark"),
	);

	useEffect(() => {
		const host = hostRef.current;
		if (!host) return;
		setShadowRoot(ensureIsolatedAppShadow(host));
	}, []);

	useEffect(() => {
		const updateTheme = () =>
			setIsDark(document.documentElement.classList.contains("dark"));
		const observer = new MutationObserver(updateTheme);
		observer.observe(document.documentElement, {
			attributes: true,
			attributeFilter: ["class"],
		});
		return () => observer.disconnect();
	}, []);

	return (
		<AppContainmentBoundary>
			<div ref={hostRef} data-bifrost-app-style-boundary="true">
				{shadowRoot
					? createPortal(
							<>
								{stylesheetHref ? (
									<link
										rel="stylesheet"
										href={stylesheetHref}
										data-bifrost-app-stylesheet="true"
									/>
								) : null}
								<div
									data-bifrost-app-root="true"
									className={isDark ? "dark" : undefined}
									style={{ width: "100%", height: "100%" }}
								>
									{children}
								</div>
							</>,
							shadowRoot,
						)
					: null}
			</div>
		</AppContainmentBoundary>
	);
}
